// Audio thread: turns the microphone (one channel, at whatever rate the sound card runs) into
// 16 kHz mono, the rate the speech model hears, and hands it over every 100 ms. A low-pass filter
// (windowed sinc) before the rate change keeps high sounds from folding down into the speech band.
// Nothing here makes sound: the node has no outputs.

const OUT_RATE = 16000;
const CHUNK = 1600;          // 100 ms at 16 kHz
const ZERO_CROSSINGS = 8;    // filter length on each side, in output samples
const STEPS = 64;            // filter table resolution per input sample

class Capture extends AudioWorkletProcessor {
  constructor() {
    super();
    const ratio = sampleRate / OUT_RATE;             // input samples per output sample
    this.step = ratio;
    this.scale = Math.max(ratio, 1) / 0.92;           // cutoff a little under 8 kHz
    this.half = Math.ceil(ZERO_CROSSINGS * this.scale);
    // Kernel table over [-half, half] input samples (Blackman window).
    const n = 2 * this.half * STEPS + 1;
    this.table = new Float32Array(n + 1);
    for (let i = 0; i < n; i++) {
      const d = i / STEPS - this.half;
      const x = d / this.scale;
      const sinc = x === 0 ? 1 : Math.sin(Math.PI * x) / (Math.PI * x);
      const w = 0.42 + 0.5 * Math.cos(Math.PI * d / this.half) + 0.08 * Math.cos(2 * Math.PI * d / this.half);
      this.table[i] = (sinc * w) / this.scale;
    }
    this.buf = new Float32Array(16384);   // recent input; buf[0] is input sample `base`
    this.base = 0;
    this.len = 0;
    this.next = 0;                         // input position of the next output sample
    this.out = new Float32Array(CHUNK);
    this.fill = 0;
    this.alive = true;
    this.port.onmessage = (e) => { if (e.data === "stop") this.alive = false; };
  }

  push(x) {
    if (this.len + x.length > this.buf.length) {
      // Drop what the filter no longer needs, then grow if that was not enough.
      const drop = Math.min(this.len, Math.max(0, Math.floor(this.next) - this.half - 1 - this.base));
      if (drop > 0) {
        this.buf.copyWithin(0, drop, this.len);
        this.base += drop;
        this.len -= drop;
      }
      if (this.len + x.length > this.buf.length) {
        const bigger = new Float32Array(Math.max(this.buf.length * 2, this.len + x.length));
        bigger.set(this.buf.subarray(0, this.len));
        this.buf = bigger;
      }
    }
    this.buf.set(x, this.len);
    this.len += x.length;
  }

  produce() {
    const end = this.base + this.len;
    while (this.next + this.half < end) {
      const t = this.next;
      const lo = Math.max(this.base, Math.ceil(t - this.half));
      const hi = Math.min(end - 1, Math.floor(t + this.half));
      let s = 0;
      for (let i = lo; i <= hi; i++) {
        const pos = (i - t + this.half) * STEPS;
        const k = pos | 0;
        const f = pos - k;
        s += this.buf[i - this.base] * (this.table[k] + (this.table[k + 1] - this.table[k]) * f);
      }
      this.out[this.fill++] = s;
      if (this.fill === CHUNK) {
        this.port.postMessage(this.out, [this.out.buffer]);
        this.out = new Float32Array(CHUNK);
        this.fill = 0;
      }
      this.next += this.step;
    }
  }

  process(inputs) {
    const input = inputs[0];
    if (input && input.length && input[0].length) {
      this.push(input[0]);
      this.produce();
    }
    return this.alive;
  }
}

registerProcessor("riah-capture", Capture);
