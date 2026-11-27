// Speech recognition worker: English Whisper (base.en or small.en) through transformers.js, on the
// graphics card (WebGPU) when the browser has one, otherwise on the processor (WASM).
// Decodes the way the app's faster-whisper does: greedy, the operator's words and the last lines
// as a prompt, no timestamps, then word timings from the cross-attention (a second, one-step pass
// over the same encoder output). Audio never leaves this worker except as text.
//
// in:  {type:"load", model, device:"auto"|"wasm"|"webgpu", threads}
//      {type:"transcribe", id, audio:Float32Array (16 kHz), prompt}
// out: {type:"progress", loaded, total, missing} {type:"ready", device, model, sec}
//      {type:"result", id, words:[{start,end,text}], sec, noSpeech, logprob, skipped}
//      {type:"error", id?, message, fatal}

import { TRANSFORMERS_URL, SPEECH, speechFiles, missingBytes, Progress } from "./models.js";
import { wordTimings } from "./whisper-words.js";

const SR = 16000;
const HALLUCINATIONS = new Set(["thank you.", "thanks for watching!", "thank you for watching.", "thanks for watching.",
  "please subscribe.", "subtitles by the amara.org community", "you", "bye.", "so."]);

let T = null;             // the transformers.js module
let model = null, tokenizer = null, extractor = null;
let ids = null;           // special token ids
let device = "wasm";
let busy = Promise.resolve();

const post = (m, transfer) => self.postMessage(m, transfer || []);

self.onmessage = (e) => {
  const m = e.data;
  busy = busy.then(() => handle(m)).catch((err) => {
    post({ type: "error", id: m.id, message: String(err && err.message || err), fatal: m.type === "load" });
  });
};

async function handle(m) {
  if (m.type === "load") return load(m);
  if (m.type === "transcribe") {
    const t0 = performance.now();
    const r = await transcribe(m.audio, m.prompt || "");
    post({ type: "result", id: m.id, ...r, sec: (performance.now() - t0) / 1000 });
  }
}

async function hasWebGpu() {
  try {
    if (!self.navigator || !navigator.gpu) return false;
    return !!(await navigator.gpu.requestAdapter());
  } catch { return false; }
}

// wasmModel: the model to use if this ends up on the processor (a page can ask for a bigger model
// on the graphics card and a smaller one if the card fails).
async function load({ model: want0 = "base.en", device: want = "auto", threads = 1, wasmDtype = null, wasmModel = null }) {
  const t0 = performance.now();
  T ??= await import(TRANSFORMERS_URL);
  T.env.allowLocalModels = false;       // never ask our own site for model files
  T.env.useBrowserCache = true;
  if (T.env.backends?.onnx?.wasm) T.env.backends.onnx.wasm.numThreads = Math.max(1, threads | 0);
  const tryGpu = want === "webgpu" || (want === "auto" && await hasWebGpu());
  const order = tryGpu ? ["webgpu", "wasm"] : ["wasm"];
  let lastErr = null;
  for (const dev of order) {
    const name = dev === "wasm" && SPEECH[wasmModel] ? wasmModel : SPEECH[want0] ? want0 : "base.en";
    const spec = SPEECH[name];
    try {
      const files = speechFiles(name, dev);
      const missing = await missingBytes(spec.id, spec.revision, files);
      const progress = new Progress(files, (loaded, total) => post({ type: "progress", loaded, total, missing }));
      post({ type: "progress", loaded: 0, total: Object.values(files).reduce((a, b) => a + b, 0), missing });
      const opts = { revision: spec.revision, progress_callback: (ev) => progress.event(ev) };
      tokenizer ??= await T.AutoTokenizer.from_pretrained(spec.id, opts);
      extractor ??= await T.AutoFeatureExtractor.from_pretrained(spec.id, opts);
      if (model) { try { await model.dispose(); } catch {} model = null; }
      model = await T.WhisperForConditionalGeneration.from_pretrained(spec.id, {
        ...opts, device: dev, dtype: dev === "webgpu" ? spec.webgpu.dtype : (wasmDtype || spec.wasm.dtype),
      });
      if (!model.forward_params.includes("encoder_outputs")) model.forward_params = [...model.forward_params, "encoder_outputs"];
      device = dev;
      setIds();
      // Warm up (and prove the graphics card really works) on a second of silence.
      await transcribe(new Float32Array(SR), "");
      post({ type: "ready", device, model: name, sec: (performance.now() - t0) / 1000 });
      return;
    } catch (err) {
      lastErr = err;
      if (model) { try { await model.dispose(); } catch {} model = null; }
    }
  }
  throw lastErr;
}

function setIds() {
  const g = model.generation_config || {};
  const tok = (s, fallback) => {
    const v = tokenizer.model.tokens_to_ids?.get?.(s);
    return v ?? fallback;
  };
  const eot = Array.isArray(g.eos_token_id) ? g.eos_token_id[0] : (g.eos_token_id ?? 50256);
  ids = {
    eot,
    sot: g.decoder_start_token_id ?? tok("<|startoftranscript|>", 50257),
    prev: g.prev_sot_token_id ?? tok("<|startofprev|>", 50360),
    noTs: g.no_timestamps_token_id ?? tok("<|notimestamps|>", 50362),
    noSpeech: tok("<|nospeech|>", null) ?? tok("<|nocaptions|>", 50361),
    suppress: g.suppress_tokens || [],
  };
}

const softmaxAt = (data, k) => {
  let max = -Infinity;
  for (let i = 0; i < data.length; i++) if (data[i] > max) max = data[i];
  let s = 0;
  for (let i = 0; i < data.length; i++) s += Math.exp(data[i] - max);
  return Math.exp(data[k] - max) / s;
};

async function transcribe(audio, prompt) {
  const samples = Math.min(audio.length, 30 * SR);
  if (samples < 0.1 * SR) return { words: [], noSpeech: 1, logprob: 0, skipped: "short" };
  const parts = {};               // seconds per stage, for the rehearsal measurements
  let mark = performance.now();
  const lap = (k) => { const t = performance.now(); parts[k] = (t - mark) / 1000; mark = t; };
  const { input_features: feats } = await extractor(audio.subarray(0, samples), { sampling_rate: SR });
  lap("features");
  // The encoder runs once; the text pass and the timing pass both reuse its output.
  let enc = null;
  try {
    const kw = await model._prepare_encoder_decoder_kwargs_for_generation({
      inputs_tensor: feats, model_inputs: { input_features: feats }, model_input_name: "input_features",
      generation_config: { guidance_scale: null },
    });
    enc = kw.encoder_outputs;
  } catch { enc = null; }
  lap("encoder");
  const shared = enc ? { encoder_outputs: enc } : {};

  // 1. Text: [startofprev, prompt..., startoftranscript] then <|notimestamps|> is forced.
  let promptIds = [];
  const p = String(prompt || "").trim();
  if (p) promptIds = tokenizer.encode(" " + p, { add_special_tokens: false }).slice(-223);
  const init = [ids.prev, ...promptIds, ids.sot];
  const suppress = ids.suppress;
  let noSpeech = 0, sumLp = 0, nLp = 0;
  const steer = (inputIds, logits) => {
    const len = inputIds[0].length;
    const d = logits.data;
    if (len === init.length) {           // first step: read "no speech" and force no timestamps
      noSpeech = softmaxAt(d, ids.noSpeech);
      d.fill(-Infinity);
      d[ids.noTs] = 0;
      return logits;
    }
    if (len === init.length + 1) { d[220] = -Infinity; d[ids.eot] = -Infinity; }   // suppress_blank
    for (const t of suppress) if (t < d.length) d[t] = -Infinity;
    for (let t = ids.sot; t < d.length; t++) d[t] = -Infinity;   // special tokens and timestamps
    let max = -Infinity;
    for (let i = 0; i < d.length; i++) if (d[i] > max) max = d[i];
    let s = 0;
    for (let i = 0; i < d.length; i++) s += Math.exp(d[i] - max);
    sumLp += -Math.log(s);               // log p(chosen) = max - logsumexp
    nLp += 1;
    return logits;
  };
  const out = await model.generate({
    inputs: feats, ...shared, decoder_input_ids: init, max_new_tokens: 448 - init.length - 1,
    begin_suppress_tokens: null, logits_processor: [steer],
  });
  const seq = Array.from((out.sequences || out).tolist()[0], Number);
  const text = seq.slice(init.length + 1).filter((t) => t < ids.eot);
  const logprob = nLp ? sumLp / nLp : 0;
  lap("text");
  parts.tokens = text.length;
  const decode = (t) => tokenizer.decode(t, { skip_special_tokens: true, clean_up_tokenization_spaces: false });
  const whole = decode(text).trim();
  if (!text.length) return dispose(enc, { words: [], noSpeech, logprob, skipped: "empty" });
  if ((noSpeech > 0.6 && logprob < -1.0) || (noSpeech > 0.8 && logprob < -0.7)) return dispose(enc, { words: [], noSpeech, logprob, skipped: "silence" });
  if (HALLUCINATIONS.has(whole.toLowerCase())) return dispose(enc, { words: [], noSpeech, logprob, skipped: "hallucination" });
  if (text.length > 40 && new Set(text).size / text.length < 0.3) return dispose(enc, { words: [], noSpeech, logprob, skipped: "loop" });

  // 2. Word timings: one step over [sot, notimestamps, text...] with the attention returned.
  let ts = null;
  try {
    const aligned = await model.generate({
      inputs: feats, ...shared, decoder_input_ids: [ids.sot, ids.noTs, ...text], max_new_tokens: 1,
      begin_suppress_tokens: null, return_token_timestamps: true, num_frames: Math.floor(samples / 320),
    });
    ts = aligned.token_timestamps.tolist()[0].map(Number);
    disposeKv(aligned.past_key_values);
  } catch {
    ts = null;
  }
  lap("timing");
  let words;
  if (ts) {
    words = wordTimings(text, (k) => ts[Math.min(k + 2, ts.length - 1)], decode, ids.eot);
  } else {
    // No timings: spread the words evenly (still usable for lines, just less exact at pauses).
    const parts = whole.split(/\s+/).filter(Boolean);
    const dur = samples / SR;
    words = parts.map((w, i) => ({ text: " " + w, start: dur * i / parts.length, end: dur * (i + 1) / parts.length }));
  }
  return dispose(enc, { words, noSpeech, logprob, skipped: null, timed: !!ts, parts });
}

function disposeKv(kv) {
  if (!kv) return;
  for (const v of Object.values(kv)) { try { v.dispose?.(); } catch {} }
}

function dispose(enc, result) {
  try { enc?.dispose?.(); } catch {}
  return result;
}
