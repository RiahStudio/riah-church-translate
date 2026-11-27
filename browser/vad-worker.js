// Speech detection worker: Silero VAD (MIT), one speech probability per 512 samples (32 ms at
// 16 kHz), streamed. Windows are counted from the start of the audio (window k = samples k*512 on)
// so the transcriber can line them up with its own buffer.
//
// in:  {type:"load"} {type:"audio", at: absolute sample index, data: Float32Array} {type:"reset"}
// out: {type:"ready", sec} {type:"vad", first: window index, probs: Float32Array} {type:"error", message, fatal}

import { TRANSFORMERS_URL, VAD } from "./models.js";

const W = 512, CONTEXT = 64;
let T = null, model = null, sr = null, state = null;
let context = new Float32Array(CONTEXT);
let pos = null;                 // absolute index of the next window's first sample
let pending = new Float32Array(0);
let busy = Promise.resolve();

const post = (m, transfer) => self.postMessage(m, transfer || []);

self.onmessage = (e) => {
  const m = e.data;
  busy = busy.then(() => handle(m)).catch((err) => {
    post({ type: "error", message: String(err && err.message || err), fatal: m.type === "load" });
  });
};

function fresh() {
  state = new T.Tensor("float32", new Float32Array(2 * 128), [2, 1, 128]);
  context = new Float32Array(CONTEXT);
}

async function handle(m) {
  if (m.type === "load") {
    const t0 = performance.now();
    T ??= await import(TRANSFORMERS_URL);
    T.env.allowLocalModels = false;
    if (T.env.backends?.onnx?.wasm) T.env.backends.onnx.wasm.numThreads = 1;
    model = await T.AutoModel.from_pretrained(VAD.id, { revision: VAD.revision, config: { model_type: "custom" }, dtype: "fp32", device: "wasm" });
    sr = new T.Tensor("int64", [16000n], []);
    fresh();
    post({ type: "ready", sec: (performance.now() - t0) / 1000 });
    return;
  }
  if (m.type === "reset") { pos = null; pending = new Float32Array(0); return; }
  if (m.type !== "audio" || !model) return;

  const at = m.at, data = m.data;
  const end = pos === null ? null : pos + pending.length;
  if (end === null || at !== end) {
    // First audio, or a gap (the microphone restarted): start again on the next window boundary.
    pos = Math.ceil(at / W) * W;
    const skip = pos - at;
    pending = skip < data.length ? data.slice(skip) : new Float32Array(0);
    fresh();
  } else {
    const joined = new Float32Array(pending.length + data.length);
    joined.set(pending, 0);
    joined.set(data, pending.length);
    pending = joined;
  }
  const n = Math.floor(pending.length / W);
  if (!n) return;
  const probs = new Float32Array(n);
  const input = new Float32Array(CONTEXT + W);
  for (let i = 0; i < n; i++) {
    const win = pending.subarray(i * W, (i + 1) * W);
    input.set(context, 0);
    input.set(win, CONTEXT);
    const out = await model({ input: new T.Tensor("float32", input.slice(), [1, CONTEXT + W]), sr, state });
    state = out.stateN;
    probs[i] = out.output.data[0];
    context = win.slice(W - CONTEXT);
  }
  const first = pos / W;
  pos += n * W;
  pending = pending.slice(n * W);
  post({ type: "vad", first, probs }, [probs.buffer]);
}
