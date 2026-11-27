// Translation worker: Meta's NLLB-200 (distilled 600M, CC-BY-NC-4.0, fine for a church service,
// not for selling) through transformers.js. A port of the app's NllbTranslator.translate_multi:
// drafts continue the words already on screen, a finished line keeps the draft's start unless that
// reads clearly worse, doubtful lines are tried again, half-translated lines are done in halves,
// and Bible names and church words are fixed afterwards. Greedy search (the app uses beam 4 on a
// computer; in a browser that costs too much time per line).
//
// in:  {type:"load", threads}
//      {type:"translate", id, text, src, tgts, keep:{tgt: ids}, draft, glossary:{fix, exact, bible}}
// out: {type:"progress", loaded, total, missing} {type:"ready", sec}
//      {type:"result", id, out:{tgt: [text, ids|null]}, sec} {type:"error", id?, message, fatal}

import { TRANSFORMERS_URL, TRANSLATE, missingBytes, Progress } from "./models.js";
import { lang } from "./langs.js";
import { tidy, exactPhrase, applyFixes, DRAFT_END, tooShort, halves, joinHalves } from "./text-fixes.js";
import { loadBibleWords } from "./bible-words.js";

const KEEP_BACK = 2;    // pieces at the growing end that a later draft may still change
const LOOSER = 0.12;    // the final line keeps the draft's start unless that scores this much worse
const SHORTER = 0.75;   // a kept line this much shorter than the fresh one has lost words
const DOUBT = -0.75;    // below this the line is translated again with the first letter's case flipped
const MAX_NEW = 200;

let T = null, model = null, tok = null, bible = null;
let EOS = 2, DEC_START = 2, PAD = 1;
let busy = Promise.resolve();

const post = (m) => self.postMessage(m);

self.onmessage = (e) => {
  const m = e.data;
  busy = busy.then(() => handle(m)).catch((err) => {
    post({ type: "error", id: m.id, message: String(err && err.message || err), fatal: m.type === "load" });
  });
};

async function handle(m) {
  if (m.type === "load") return load(m);
  if (m.type === "translate") {
    const t0 = performance.now();
    const out = await translateMulti(m.text, m.src, m.tgts, m.glossary || {}, m.keep || {}, !!m.draft);
    post({ type: "result", id: m.id, out, sec: (performance.now() - t0) / 1000 });
  }
}

async function load({ threads = 1 }) {
  const t0 = performance.now();
  T ??= await import(TRANSFORMERS_URL);
  T.env.allowLocalModels = false;
  if (T.env.backends?.onnx?.wasm) T.env.backends.onnx.wasm.numThreads = Math.max(1, threads | 0);
  const missing = await missingBytes(TRANSLATE.id, TRANSLATE.revision, TRANSLATE.files);
  const progress = new Progress(TRANSLATE.files, (loaded, total) => post({ type: "progress", loaded, total, missing }));
  const opts = { revision: TRANSLATE.revision, progress_callback: (ev) => progress.event(ev) };
  bible = await loadBibleWords(new URL("bible_words.json", import.meta.url).href);
  tok = await T.AutoTokenizer.from_pretrained(TRANSLATE.id, opts);
  model = await T.AutoModelForSeq2SeqLM.from_pretrained(TRANSLATE.id, { ...opts, dtype: TRANSLATE.dtype, device: "wasm" });
  // Let generate() take a forced start (decoder_input_ids) and a reused encoder output.
  for (const k of ["decoder_input_ids", "encoder_outputs"]) if (!model.forward_params.includes(k)) model.forward_params.push(k);
  const g = model.generation_config || {};
  DEC_START = g.decoder_start_token_id ?? 2;
  EOS = Array.isArray(g.eos_token_id) ? g.eos_token_id[0] : (g.eos_token_id ?? 2);
  PAD = g.pad_token_id ?? 1;
  await translateMulti("Good morning.", "en", ["th"], {}, {}, false);   // warm up
  post({ type: "ready", sec: (performance.now() - t0) / 1000 });
}

const tokenId = (s) => tok.model.tokens_to_ids.get(s);

function sourceIds(text, src) {
  return [tokenId(lang(src).nllb), ...tok.encode(text, { add_special_tokens: false }), EOS];
}

// The encoder runs once per source line; every target language reuses it.
async function encode(text, src) {
  const idsArr = sourceIds(text, src);
  const input_ids = new T.Tensor("int64", BigInt64Array.from(idsArr.map(BigInt)), [1, idsArr.length]);
  const attention_mask = new T.Tensor("int64", new BigInt64Array(idsArr.length).fill(1n), [1, idsArr.length]);
  const kw = await model._prepare_encoder_decoder_kwargs_for_generation({
    inputs_tensor: input_ids, model_inputs: { input_ids, attention_mask }, model_input_name: "input_ids",
    generation_config: { guidance_scale: null },
  });
  return { input_ids, attention_mask, encoder_outputs: kw.encoder_outputs };
}

// One greedy run: [score, ids] where ids continue `prefix` (prefix included) and score is the mean
// log probability of the new tokens.
async function run(enc, tgt, prefix) {
  const code = tokenId(lang(tgt).nllb);
  const start = [DEC_START, code, ...(prefix || [])];
  let sum = 0, n = 0;
  const score = (inputIds, logits) => {
    const d = logits.data;
    let max = -Infinity;
    for (let i = 0; i < d.length; i++) if (d[i] > max) max = d[i];
    let s = 0;
    for (let i = 0; i < d.length; i++) s += Math.exp(d[i] - max);
    sum += -Math.log(s);
    n += 1;
    return logits;
  };
  const out = await model.generate({
    inputs: enc.input_ids, attention_mask: enc.attention_mask, encoder_outputs: enc.encoder_outputs,
    decoder_input_ids: start, max_new_tokens: MAX_NEW, repetition_penalty: 1.1, no_repeat_ngram_size: 4,
    logits_processor: [score],
  });
  const seq = Array.from((out.sequences || out).tolist()[0], Number).slice(2);
  const ids = [];
  for (const t of seq) { if (t === EOS) break; if (t !== PAD) ids.push(t); }
  return [n ? sum / n : 0, ids];
}

// Mean log probability of a whole target line [code, ...ids, </s>], forced (CTranslate2's
// score_batch), so a forced start and a free line are scored the same way.
async function scoreLine(enc, tgt, ids) {
  const code = tokenId(lang(tgt).nllb);
  const target = [code, ...ids, EOS];
  const dec = [DEC_START, code, ...ids];
  const decoder_input_ids = new T.Tensor("int64", BigInt64Array.from(dec.map(BigInt)), [1, dec.length]);
  const out = await model.forward({ input_ids: enc.input_ids, attention_mask: enc.attention_mask,
    encoder_outputs: enc.encoder_outputs, decoder_input_ids });
  const V = out.logits.dims[2];
  const d = out.logits.data;
  let total = 0;
  for (let p = 0; p < target.length; p++) {
    const row = d.subarray(p * V, (p + 1) * V);
    let max = -Infinity;
    for (let i = 0; i < V; i++) if (row[i] > max) max = row[i];
    let s = 0;
    for (let i = 0; i < V; i++) s += Math.exp(row[i] - max);
    total += row[target[p]] - max - Math.log(s);
  }
  for (const [k, v] of Object.entries(out)) if (k !== "logits") { try { v.dispose?.(); } catch {} }
  return total / target.length;
}

function textOf(ids, tgt, glossary, draft, srcText) {
  let out = tidy(tok.decode(ids, { skip_special_tokens: true }), tgt);
  if (draft) out = out.replace(DRAFT_END, "");      // the sentence isn't over yet
  if (srcText && glossary.bible !== false && bible) out = bible.fix(srcText, out, tgt);
  return applyFixes(out, (glossary.fix || {})[tgt]);
}

const keepWins = (free, kept, fair) => kept.length >= SHORTER * free.length && (!fair || fair[1] >= fair[0] - LOOSER);
const sameIds = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);

async function translateMulti(text, src, tgts, glossary, keepIn, draft) {
  const out = {};
  for (const t of tgts) {
    const v = exactPhrase(text, t, glossary);
    if (v) out[t] = [v, null];
  }
  tgts = tgts.filter((t) => !(t in out));
  if (!tgts.length) return out;
  const keep = {};
  for (const [t, p] of Object.entries(keepIn || {})) if (p && p.length) keep[t] = p.slice(0, Math.max(0, p.length - KEEP_BACK));
  const enc = await encode(text, src);
  try {
    for (const t of tgts) {
      let score, ids;
      if (draft) {
        [score, ids] = await run(enc, t, keep[t]);
      } else {
        const free = await run(enc, t, null);
        [score, ids] = free;
        let kept = null;
        if (keep[t] && keep[t].length) kept = await run(enc, t, keep[t]);
        if (kept && sameIds(kept[1], free[1])) {
          ids = kept[1];
        } else if (kept && kept[1].length >= SHORTER * free[1].length
                   && keepWins(free[1], kept[1], [await scoreLine(enc, t, free[1]), await scoreLine(enc, t, kept[1])])) {
          ids = kept[1];
        } else if (score < DOUBT && /^\p{L}/u.test(text)) {
          const flipped = swapFirst(text);
          const enc2 = await encode(flipped, src);
          try {
            const alt = await run(enc2, t, null);
            if (alt[0] > score) [score, ids] = alt;
          } finally { disposeEnc(enc2); }
        }
      }
      out[t] = [textOf(ids, t, glossary, draft, text), ids];
      if (!draft && tooShort(text, out[t][0], t)) out[t] = (await bothHalves(text, src, t, glossary)) || out[t];
    }
  } finally {
    disposeEnc(enc);
  }
  return out;
}

function swapFirst(text) {
  const c = text[0];
  const s = c === c.toUpperCase() ? c.toLowerCase() : c.toUpperCase();
  return s + text.slice(1);
}

// A finished line that came out too short: translate each half and join them.
async function bothHalves(text, src, tgt, glossary) {
  const parts = halves(text);
  if (!parts) return null;
  const got = [];
  for (const p of parts) {
    const enc = await encode(p, src);
    try { got.push(await run(enc, tgt, null)); } finally { disposeEnc(enc); }
  }
  const a = textOf(got[0][1], tgt, glossary, false, parts[0]);
  const b = textOf(got[1][1], tgt, glossary, false, parts[1]);
  return [joinHalves(a, b, tgt), [...got[0][1], ...got[1][1]]];
}

function disposeEnc(enc) {
  try { enc.encoder_outputs?.dispose?.(); } catch {}
}
