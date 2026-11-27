// Sound in: the microphone (or one channel of a sound board) as 16 kHz chunks, levelled so a quiet
// feed and a loud one sound alike to the speech model. If the microphone drops out (a cable pulled,
// the computer asleep, another app grabbing it) it is opened again on its own. A recording can be
// fed in the same way for a rehearsal; it is decoded silently and never played out loud.

export const SR = 16000;

// ---------------------------------------------------------------------- level
// Brings speech to an even level. The gain only moves while someone is speaking (well above the
// room's own hiss), so silence and songs between talking are never pumped up, and it is capped so
// a very quiet feed is lifted at most 20 dB.
export class Normalizer {
  constructor({ target = 0.08, min = 0.5, max = 10 } = {}) {
    this.target = target;
    this.min = min;
    this.max = max;
    this.gain = 1;
    this.speech = 0;          // running loudness of speech (RMS)
    this.floor = 0;           // the quietest the feed has been lately (the room's hiss)
  }

  process(chunk) {
    let s = 0;
    for (let i = 0; i < chunk.length; i++) s += chunk[i] * chunk[i];
    const rms = Math.sqrt(s / Math.max(1, chunk.length));
    // The floor follows quiet moments quickly and creeps up slowly (about 1 dB a second).
    this.floor = this.floor === 0 ? rms : rms < this.floor ? 0.7 * this.floor + 0.3 * rms : this.floor * 1.012;
    const speaking = rms > Math.max(3 * this.floor, 0.0004);
    const from = this.gain;
    if (speaking) {
      this.speech = this.speech === 0 ? rms : 0.97 * this.speech + 0.03 * rms;     // about 3 s
      const want = Math.min(this.max, Math.max(this.min, this.target / this.speech));
      // At most about 6 dB a second, so a single shout or cough doesn't duck the next words.
      const step = Math.min(1.07, Math.max(1 / 1.07, want / this.gain));
      this.gain *= step;
    }
    const out = new Float32Array(chunk.length);
    const n = chunk.length;
    for (let i = 0; i < n; i++) {
      const g = from + (this.gain - from) * (i / n);
      let v = chunk[i] * g;
      // Soft limit near full scale instead of a hard clip.
      if (v > 0.9) v = 0.9 + 0.1 * Math.tanh((v - 0.9) / 0.1);
      else if (v < -0.9) v = -0.9 - 0.1 * Math.tanh((-v - 0.9) / 0.1);
      out[i] = v;
    }
    return { out, rms, peak: peakOf(chunk) };
  }
}

function peakOf(a) {
  let p = 0;
  for (let i = 0; i < a.length; i++) {
    const v = Math.abs(a[i]);
    if (v > p) p = v;
  }
  return p;
}

// ---------------------------------------------------------------------- inputs
// Microphones and sound boards this browser can see. Names show only once the page may use the
// microphone (the browser hides them before that).
export async function listInputs() {
  try {
    const all = await navigator.mediaDevices.enumerateDevices();
    // Before the page may use the microphone some browsers list it with no id: that's the default one.
    return all.filter((d) => d.kind === "audioinput" && d.deviceId && d.deviceId !== "default" && d.deviceId !== "communications")
      .map((d, i) => ({ id: d.deviceId, label: d.label || `Microphone ${i + 1}` , named: !!d.label }));
  } catch {
    return [];
  }
}

function constraints(deviceId) {
  return {
    audio: {
      ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
      // The raw sound: the browser's echo, noise and level processing all hurt the speech model.
      echoCancellation: false, noiseSuppression: false, autoGainControl: false,
      channelCount: { ideal: 8 },
    },
    video: false,
  };
}

// How many channels a device has (opens it for a moment).
export async function channelsOf(deviceId) {
  const stream = await navigator.mediaDevices.getUserMedia(constraints(deviceId));
  const n = stream.getAudioTracks()[0]?.getSettings().channelCount || 1;
  for (const t of stream.getTracks()) t.stop();
  return n;
}

// ---------------------------------------------------------------------- microphone
// onChunk(Float32Array 16 kHz, {rms, peak}) every 100 ms; onState("live" | "lost" | "back", message).
export class MicSource {
  constructor({ deviceId = "", channel = null, workletUrl, onChunk, onState = () => {} }) {
    this.deviceId = deviceId;
    this.channel = channel;
    this.workletUrl = workletUrl;
    this.onChunk = onChunk;
    this.onState = onState;
    this.level = new Normalizer();
    this.ctx = null;
    this.stream = null;
    this.nodes = [];
    this.running = false;
    this.lastChunk = 0;
    this.retry = 0;
    this.tries = 0;
    this.watch = 0;
    this.lost = false;
    this.channels = 1;
    this.onDevices = () => { if (this.lost) this.reopen("devices"); };
  }

  async start() {
    this.running = true;
    // The audio clock: created at the sound card's own rate (Firefox can't mix rates), resampled
    // to 16 kHz on the audio thread.
    this.ctx = new AudioContext({ latencyHint: "playback" });
    await this.ctx.audioWorklet.addModule(this.workletUrl);
    this.ctx.onstatechange = () => {
      if (this.running && this.ctx && this.ctx.state !== "running" && this.ctx.state !== "closed") this.ctx.resume().catch(() => {});
    };
    await this.open(true);
    navigator.mediaDevices.addEventListener?.("devicechange", this.onDevices);
    // A microphone that stops sending (asleep, unplugged, taken by another app) is opened again.
    this.watch = setInterval(() => {
      if (!this.running) return;
      if (this.ctx.state !== "running") this.ctx.resume().catch(() => {});
      if (!this.lost && performance.now() - this.lastChunk > 3000) this.reopen("silent");
    }, 1000);
  }

  async open(first = false) {
    this.close();
    // After a few failed tries on the chosen input, take whatever microphone there is.
    const id = this.tries >= 3 ? "" : this.deviceId;
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia(constraints(id));
    } catch (err) {
      if (first && this.deviceId && /NotFound|Overconstrained/.test(err && err.name)) {
        stream = await navigator.mediaDevices.getUserMedia(constraints(""));
      } else {
        throw err;
      }
    }
    if (!this.running) { for (const t of stream.getTracks()) t.stop(); return; }
    this.stream = stream;
    const track = stream.getAudioTracks()[0];
    this.channels = track?.getSettings().channelCount || 1;
    track?.addEventListener("ended", () => this.reopen("ended"));
    const src = this.ctx.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(this.ctx, "riah-capture", {
      numberOfInputs: 1, numberOfOutputs: 0, channelCount: 1, channelCountMode: "explicit", channelInterpretation: "speakers",
    });
    node.port.onmessage = (e) => this.chunk(e.data);
    this.nodes = [src, node];
    const ch = this.channel;
    if (this.channels > 1 && ch != null && ch < this.channels) {
      const split = this.ctx.createChannelSplitter(this.channels);
      src.connect(split);
      split.connect(node, ch, 0);
      this.nodes.push(split);
    } else if (this.channels > 2) {
      // A many-channel board with no channel picked: channels 1 and 2 together.
      const split = this.ctx.createChannelSplitter(this.channels);
      const mix = this.ctx.createGain();
      mix.gain.value = 0.5;
      src.connect(split);
      split.connect(mix, 0);
      split.connect(mix, 1);
      mix.connect(node);
      this.nodes.push(split, mix);
    } else {
      src.connect(node);     // mono, or both sides of a stereo cable mixed
    }
    if (this.ctx.state !== "running") await this.ctx.resume().catch(() => {});
    this.lastChunk = performance.now();
  }

  chunk(data) {
    if (!this.running) return;
    this.lastChunk = performance.now();
    if (this.lost) {
      this.lost = false;
      this.tries = 0;
      this.onState("back", "");
    }
    const { out, rms, peak } = this.level.process(data);
    this.onChunk(out, { rms, peak });
  }

  reopen(why) {
    if (!this.running) return;
    if (!this.lost) {
      this.lost = true;
      this.onState("lost", why);
    }
    clearTimeout(this.retry);
    this.retry = setTimeout(async () => {
      if (!this.running) return;
      this.tries += 1;
      try {
        await this.open();
        // open() worked; chunk() flips the state back once sound flows again.
      } catch {
        this.reopen(why);   // try again in a moment
      }
    }, Math.min(5000, 500 + this.tries * 1000));
  }

  close() {
    for (const n of this.nodes) { try { n.disconnect(); } catch {} }
    const node = this.nodes[1];
    if (node?.port) { try { node.port.postMessage("stop"); node.port.onmessage = null; } catch {} }
    this.nodes = [];
    if (this.stream) for (const t of this.stream.getTracks()) { try { t.stop(); } catch {} }
    this.stream = null;
  }

  async stop() {
    this.running = false;
    clearInterval(this.watch);
    clearTimeout(this.retry);
    navigator.mediaDevices.removeEventListener?.("devicechange", this.onDevices);
    this.close();
    if (this.ctx) { try { await this.ctx.close(); } catch {} }
    this.ctx = null;
  }
}

// ---------------------------------------------------------------------- recordings (rehearsal)
// Any file the browser can decode, as 16 kHz mono. An offline context never reaches the speakers.
export async function decodeFile(bytes) {
  const Offline = self.OfflineAudioContext || self.webkitOfflineAudioContext;
  const ctx = new Offline(1, SR, SR);
  const buf = await ctx.decodeAudioData(bytes.slice(0));
  if (buf.sampleRate === SR && buf.numberOfChannels === 1) return buf.getChannelData(0).slice();
  // Mix down and resample by rendering offline (still silent).
  const out = new Offline(1, Math.ceil(buf.duration * SR), SR);
  const src = out.createBufferSource();
  src.buffer = buf;
  src.connect(out.destination);
  src.start();
  const r = await out.startRendering();
  return r.getChannelData(0).slice();
}

// Feeds decoded audio in 100 ms chunks, at the speed it was spoken (or `speed` times faster).
export class FileSource {
  constructor({ audio, speed = 1, onChunk, onEnd, level = true }) {
    this.audio = audio;
    this.speed = speed;
    this.onChunk = onChunk;
    this.onEnd = onEnd;
    this.level = level ? new Normalizer() : null;
    this.pos = 0;
    this.timer = 0;
    this.running = false;
    this.startMs = 0;
  }

  async start() {
    this.running = true;
    this.startMs = performance.now();
    const tick = () => {
      if (!this.running) return;
      // Catch up on everything due by now, so a slow timer only delays, never drifts.
      const due = Math.min(this.audio.length, Math.floor(((performance.now() - this.startMs) / 1000) * SR * this.speed));
      while (this.pos + 1600 <= due || (due === this.audio.length && this.pos < due)) {
        const raw = this.audio.slice(this.pos, Math.min(this.pos + 1600, this.audio.length));
        this.pos += raw.length;
        if (this.level) {
          const { out, rms, peak } = this.level.process(raw);
          this.onChunk(out, { rms, peak });
        } else {
          this.onChunk(raw, { rms: 0, peak: 0 });
        }
      }
      if (this.pos >= this.audio.length) {
        this.running = false;
        this.onEnd?.();
        return;
      }
      this.timer = setTimeout(tick, 50);
    };
    tick();
  }

  async stop() {
    this.running = false;
    clearTimeout(this.timer);
  }
}
