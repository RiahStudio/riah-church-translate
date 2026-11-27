// The live loop, a port of the app's engine.py Engine: sound in, lines and translations out to the
// hub. The three models each run in their own worker; a worker that crashes or hangs is replaced
// and its model loaded again (from the browser's cache) while the rest carries on.

import { StreamingTranscriber, SR } from "./streaming.js";
import { Translations } from "./scheduler.js";
import { lang } from "./langs.js";

const MIN_CHUNK = 1.0;      // seconds of new sound before Whisper looks again
const BUSY = 0.85;          // Whisper needs this share of real time: no drafts (they'd slow the lines)
const ASR_TIMEOUT = 60000;  // one pass that takes longer than this is a stuck worker
const MT_TIMEOUT = 90000;

export class NotReady extends Error {
  constructor(what) { super(`${what} is not ready`); this.notReady = true; }
}

// ---------------------------------------------------------------------- workers
// One model in one worker. state: "loading" | "ready" | "failed". A worker that crashes, hangs or
// keeps failing mid-service is replaced and its model loaded again; a model that can't load is
// tried again every 10 s while `keepTrying()` says so.
export class WorkerClient {
  constructor(url, name, { onProgress = () => {}, onRestart = () => {}, keepTrying = () => false } = {}) {
    this.url = url;
    this.name = name;
    this.onProgress = onProgress;
    this.onRestart = onRestart;
    this.keepTrying = keepTrying;
    this.w = null;
    this.state = "failed";
    this.loading = null;
    this.loadMsg = null;
    this.info = null;
    this.calls = new Map();
    this.nextId = 1;
    this.restarts = 0;
    this.fails = 0;
    this.retry = 0;
    this.onMessage = null;      // other messages (the speech detector's results)
  }

  get ready() {
    return this.state === "ready";
  }

  spawn() {
    this.w = new Worker(this.url, { type: "module", name: this.name });
    this.w.onmessage = (e) => this.heard(e.data);
    this.w.onerror = (e) => { e.preventDefault?.(); this.crashed(e.message || "crashed"); };
    this.w.onmessageerror = () => this.crashed("bad message");
  }

  heard(m) {
    if (!m) return;
    if (m.type === "progress") return this.onProgress(m);
    if (m.type === "ready") {
      this.state = "ready";
      this.info = m;
      this.fails = 0;
      this.loading?.resolve(m);
      return;
    }
    if (m.type === "error" && m.fatal) return this.loadFailed(new Error(m.message));
    const call = m.id != null ? this.calls.get(m.id) : null;
    if (call) {
      this.calls.delete(m.id);
      clearTimeout(call.timer);
      if (m.type === "error") {
        call.reject(new Error(m.message));
        // A model that fails three times running is broken (out of memory, say): start it afresh.
        if (++this.fails >= 3) this.crashed("failing");
      } else {
        this.fails = 0;
        call.resolve(m);
      }
      return;
    }
    this.onMessage?.(m);
  }

  // Loads the model (a fresh worker if the last one failed). Resolves with the "ready" message.
  load(msg) {
    clearTimeout(this.retry);
    this.loadMsg = msg;
    if (this.state === "ready" && this.w) return Promise.resolve(this.info);
    if (this.state === "loading" && this.loading) return this.loading.promise;
    this.kill();
    this.spawn();
    this.state = "loading";
    let resolve, reject;
    const promise = new Promise((a, b) => { resolve = a; reject = b; });
    promise.catch(() => {});
    this.loading = { resolve, reject, promise };
    this.w.postMessage(msg);
    return promise;
  }

  loadFailed(err) {
    this.state = "failed";
    this.loading?.reject(err);
    this.kill();
    clearTimeout(this.retry);
    this.retry = setTimeout(() => { if (this.state === "failed" && this.keepTrying()) this.load(this.loadMsg); }, 10000);
  }

  crashed(why) {
    if (this.state === "loading") return this.loadFailed(new Error(`${this.name}: ${why}`));
    if (this.state !== "ready") return;
    this.restarts += 1;
    this.onRestart(why);
    this.kill();
    this.state = "failed";
    this.load(this.loadMsg);
  }

  post(msg, transfer) {
    if (this.w && this.state !== "failed") this.w.postMessage(msg, transfer || []);
  }

  call(msg, transfer, timeoutMs) {
    if (!this.ready) return Promise.reject(new NotReady(this.name));
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.calls.delete(id);
        reject(new Error(`${this.name} took too long`));
        this.crashed("stuck");
      }, timeoutMs);
      this.calls.set(id, { resolve, reject, timer });
      this.w.postMessage({ ...msg, id }, transfer || []);
    });
  }

  kill() {
    try { this.w?.terminate(); } catch {}
    this.w = null;
    for (const c of this.calls.values()) { clearTimeout(c.timer); c.reject(new NotReady(this.name)); }
    this.calls.clear();
  }

  terminate() {
    clearTimeout(this.retry);
    this.kill();
    this.state = "failed";
    this.keepTrying = () => false;
  }
}

// ---------------------------------------------------------------------- the loop
export class Engine {
  constructor(hub, { base, onNote = () => {}, onEvent = () => {} }) {
    this.hub = hub;
    this.base = base;            // URL of this folder (the workers live next to this file)
    this.onNote = onNote;        // plain progress words for the operator
    this.onEvent = onEvent;
    this.asr = null;
    this.vad = null;
    this.mt = null;
    this.asrKey = "";
    this.sched = null;
    this.source = null;
    this.running = false;
    this.stopping = false;
    this.runId = 0;
    this.stopNow = null;
    this.tr = null;
    this.progress = { asr: null, mt: null };
    this.asrParts = {};          // where the speech model's time goes (summed per stage)
  }

  workers({ model, device, threads, wasmDtype = null, wasmModel = null }) {
    const url = (f) => new URL(f, this.base);
    const keepTrying = () => this.running;
    const key = `${model}|${wasmModel}|${device}|${JSON.stringify(wasmDtype)}`;
    if (this.asr && this.asrKey !== key) { this.asr.terminate(); this.asr = null; }
    if (!this.asr) {
      this.asrKey = key;
      this.asr = new WorkerClient(url("asr-worker.js"), "speech", {
        keepTrying,
        onProgress: (m) => this.note("asr", m),
        onRestart: () => this.onNote("restart", "Restarting the speech model. Subtitles carry on in a moment."),
      });
    }
    if (!this.vad) {
      this.vad = new WorkerClient(url("vad-worker.js"), "speech detector", { keepTrying });
      this.vad.onMessage = (m) => { if (m.type === "vad" && this.tr) this.tr.addVad(m.first, m.probs); };
    }
    if (!this.mt) {
      this.mt = new WorkerClient(url("mt-worker.js"), "translator", {
        keepTrying,
        onProgress: (m) => this.note("mt", m),
        onRestart: () => this.onNote("restart", "Restarting the translator. Translations carry on in a moment."),
      });
    }
    const loads = [
      this.asr.load({ type: "load", model, device, threads: threads.asr, wasmDtype, wasmModel }),
      this.vad.load({ type: "load" }),
    ];
    // The translator (about 900 MB the first time) waits for the speech model, so subtitles start
    // as soon as possible; until it is ready the lines show in the spoken language.
    const mt = this.mt;
    this.mtReady = Promise.allSettled(loads).then(() => {
      if (mt !== this.mt || mt.ready) return;
      return mt.load({ type: "load", threads: threads.mt }).then(
        () => this.onNote("mt-ready", null),
        (err) => this.onNote("mt-failed", plainError(err)));
    });
    return Promise.all(loads);
  }

  note(which, m) {
    this.progress[which] = m;
    this.onNote(which, m);
  }

  // cfg: {src, tgts, model, device, threads:{asr, mt}, glossary, drafts, source: async (onChunk, onEnd) => source}
  async start(cfg) {
    if (this.running) return;
    this.running = true;
    this.stopping = false;
    const run = ++this.runId;        // a Stop and a new Start while the models load: the old run bows out
    const status = (state, detail) => this.hub.publish({ type: "status", state, detail });
    try {
      const S = lang(cfg.src);
      const tgts = (cfg.tgts || []).filter((t) => t !== cfg.src);
      status("loading", cfg.loadingText || "Getting ready…");
      // Speech first: subtitles can start while the (much bigger) translator is still arriving.
      const [asrInfo] = await this.workers(cfg);
      if (run !== this.runId) return;
      if (!this.running) return this.finish("Stopped.");
      // A rehearsal waits for the translator too, so it shows what a service will look like.
      if (cfg.file && tgts.length && !this.mt.ready) {
        status("loading", "Getting the translator ready…");
        await this.mtReady;
        if (run !== this.runId) return;
        if (!this.running) return this.finish("Stopped.");
      }
      this.hub.publish({ type: "langs", src: cfg.src, tgts });

      const tr = this.tr = new StreamingTranscriber({
        transcribe: (audio, prompt) => this.transcribe(audio, prompt),
        language: S.whisper, spaced: S.spaced, hints: cfg.glossary?.hints || "",
      });
      const marks = [];                 // [absolute sample, wall ms] per chunk: when each moment was heard
      const heardAt = (sec) => {
        const s = sec * SR;
        let lo = 0, hi = marks.length - 1, best = marks.length ? marks[0][1] : performance.now();
        while (lo <= hi) {
          const mid = (lo + hi) >> 1;
          if (marks[mid][0] <= s) { best = marks[mid][1] + ((s - marks[mid][0]) / SR) * 1000; lo = mid + 1; } else hi = mid - 1;
        }
        return best;
      };
      const ends = new Map();
      const sched = this.sched = new Translations({
        mt: { translate: (text, src, t, opts) => this.translate(text, src, t, opts) },
        src: cfg.src, tgts, glossary: cfg.glossary || {}, spaced: S.spaced, drafts: cfg.drafts !== false,
        onFinal: (id, tgt, text, secs) => {
          const end = ends.get(id);
          const lag = end == null ? null : round2((performance.now() - end) / 1000);
          this.hub.publish({ type: "translation", id, lang: tgt, dst: text, lag, mtSec: round2(secs) });
        },
        onDraft: (seg, tgt, text, srcText) => this.hub.publish({ type: "draft", seg, lang: tgt, text, src: srcText }),
        onError: (err) => console.warn("translation", err),
        staleAfter: () => Math.max(2, Number(this.hub.display?.visibleRows) || 3),
      });

      let nextId = Date.now(), seg = 0, fresh = 0, lastPartial = null, lastLevel = 0, passSec = 0, peak = 0, stepping = false;
      let total = 0, asrBusy = 0, passes = 0, ended = false;
      this.asrParts = {};
      const t0 = performance.now();

      const publishLines = (lines, withLag = true) => {
        for (const ln of lines) {
          nextId += 1;
          const end = heardAt(ln.end);
          ends.set(nextId, end);
          if (ends.size > 200) ends.delete(ends.keys().next().value);
          const lag = withLag ? round2((performance.now() - end) / 1000) : null;
          this.hub.publish({ type: "line", id: nextId, seg, src: ln.text, lag, start: round2(ln.start), end: round2(ln.end),
            keepDraft: sched.draftCovers(seg, ln.text) });
          sched.addLine(nextId, seg, ln.text);
          seg += 1;
        }
      };

      const step = async (final) => {
        stepping = true;
        try {
          const r = await tr.step(final);
          asrBusy += tr.lastTranscribeSec;
          passes += 1;
          passSec = 0.7 * passSec + 0.3 * tr.lastTranscribeSec;
          publishLines(r.lines, !final || !this.stopping);
          if (r.partial !== lastPartial) {
            this.hub.publish({ type: "partial", text: r.partial, seg });
            lastPartial = r.partial;
          }
          if (passSec < BUSY * MIN_CHUNK) sched.wantDraft(seg, tr.pendingText());
        } catch (err) {
          // The speech worker is restarting: keep the sound, try again on the next second of it.
          if (!err.notReady) console.warn("speech", err);
          tr.trim();
        } finally {
          stepping = false;
        }
      };

      const pump = async () => {
        while (!stepping && this.running && !this.stopping && fresh >= MIN_CHUNK * SR) {
          fresh = 0;
          await step(false);
        }
      };

      const onChunk = (chunk, lv) => {
        if (!this.running || this.stopping) return;
        marks.push([total, performance.now()]);
        if (marks.length > 1200) marks.splice(0, 600);
        this.vad.post({ type: "audio", at: total, data: chunk.slice() });
        tr.push(chunk);
        total += chunk.length;
        fresh += chunk.length;
        peak = Math.max(peak, lv.peak || 0);
        const now = performance.now();
        if (now - lastLevel > 200) {
          this.hub.publish({ type: "level", level: round4(lv.rms || 0), peak: round3(peak) });
          lastLevel = now;
          peak = 0;
        }
        this.hub.alive?.();
        pump();
      };

      let finished = null;
      const done = new Promise((r) => { finished = r; });
      this.source = await cfg.source(onChunk, () => { ended = true; finished(); }, (state) => {
        if (state === "lost") status("listening", "The microphone stopped. Opening it again…");
        if (state === "back") {
          status("listening", this.listeningText(cfg, asrInfo));
          // A short silence where the sound broke off, so the line before it ends cleanly.
          onChunk(new Float32Array(SR / 2), { rms: 0, peak: 0 });
        }
      });
      this.stopNow = finished;
      status("listening", this.listeningText(cfg, asrInfo));
      this.onEvent({ type: "started", t0 });
      await done;

      // Stop pressed, or the recording ended: show what was being said, then let translations finish.
      this.stopping = true;
      await this.source?.stop();
      while (stepping) await sleep(50);
      await step(true).catch(() => {});
      await sched.close(30000);
      this.hub.publish({ type: "partial", text: "", seg });
      const audioSec = total / SR;
      this.onEvent({ type: "summary", model: asrInfo.model, device: asrInfo.device, audioSec: round2(audioSec),
        asrBusySec: round2(asrBusy), realtimeFactor: round2(asrBusy / Math.max(1e-9, audioSec)), passes, lines: seg,
        asrParts: Object.fromEntries(Object.entries(this.asrParts).map(([k, v]) => [k, round2(v)])),
        wallSec: round2((performance.now() - t0) / 1000), src: cfg.src, tgts,
        drafts: sched.draftsOn, finalSecPerLang: round2(sched.finalSec), skippedLines: sched.skipped,
        restarts: { asr: this.asr.restarts, mt: this.mt.restarts, vad: this.vad.restarts } });
      this.finish(ended && cfg.file ? "Recording finished." : "Stopped.");
    } catch (err) {
      if (run !== this.runId) return;
      console.error(err);
      try { await this.source?.stop(); } catch {}
      this.running = false;
      this.stopNow = null;
      this.source = null;
      this.tr = null;
      this.sched = null;
      status("error", plainError(err));
    }
  }

  listeningText(cfg, info) {
    const where = cfg.file ? "Playing a recording as if it were live (silently)." : "Listening.";
    const on = info.device === "webgpu" ? "graphics card" : "processor";
    return `${where} Speech model: ${info.model}, on the ${on}.`;
  }

  finish(detail) {
    this.running = false;
    this.stopNow = null;
    this.stopping = false;
    this.source = null;
    this.tr = null;
    this.sched = null;
    this.hub.publish({ type: "status", state: "stopped", detail });
  }

  async stop() {
    if (!this.running) return;
    if (this.stopNow) return this.stopNow();
    // Still loading the models: stop now (they keep downloading into the cache for next time).
    this.runId += 1;
    this.finish("Stopped.");
  }

  // The models are loaded (or loading) in the background before Start, so Start is quick.
  warm(cfg) {
    this.workers(cfg).catch(() => {});
  }

  setTargets(src, tgts) {
    if (this.sched && this.running) this.sched.setTargets(tgts.filter((t) => t !== src));
  }

  async transcribe(audio, prompt) {
    const a = audio.slice();
    const r = await this.asr.call({ type: "transcribe", audio: a, prompt }, [a.buffer], ASR_TIMEOUT);
    if (r.parts) for (const [k, v] of Object.entries(r.parts)) this.asrParts[k] = (this.asrParts[k] || 0) + v;
    return r.words;
  }

  async translate(text, src, tgts, { glossary, keep, draft } = {}) {
    if (!this.mt.ready) throw new NotReady("translator");
    const r = await this.mt.call({ type: "translate", text, src, tgts, keep: keep || {}, draft: !!draft,
      glossary: { fix: glossary?.fix || {}, exact: glossary?.exact || {}, bible: glossary?.bible !== false } }, null, MT_TIMEOUT);
    return r.out;
  }
}

const round2 = (x) => Math.round(x * 100) / 100;
const round3 = (x) => Math.round(x * 1000) / 1000;
const round4 = (x) => Math.round(x * 10000) / 10000;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Plain words for what can go wrong in a browser.
export function plainError(err) {
  const name = err && err.name;
  const m = String((err && err.message) || err || "");
  if (name === "NotAllowedError" || /Permission denied|not allowed/i.test(m))
    return "The browser isn't allowed to use the microphone. Allow it in the address bar (the microphone or lock icon), then press Start.";
  if (name === "NotFoundError" || /Requested device not found/i.test(m))
    return "No microphone found. Plug one in (or the cable from the sound desk), then press Start.";
  if (name === "NotReadableError" || /Could not start audio source/i.test(m))
    return "Another app is using the microphone. Close it, then press Start.";
  if (/QuotaExceeded|quota/i.test(m))
    return "This computer doesn't have room for the models (about 1 GB). Free some disk space, then press Start.";
  if (/Failed to fetch|NetworkError|Load failed|network/i.test(m))
    return "The models couldn't download. Check the internet connection, then press Start. (Once downloaded they work offline.)";
  if (/memory|Out of bounds|allocation/i.test(m))
    return "This computer ran out of memory for the models. Close other tabs and apps, then press Start.";
  return "Something went wrong: " + m;
}
