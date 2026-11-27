// Streaming speech recognition, a port of riah_translate/asr.py.
//
// Whisper is not a streaming model: it transcribes a chunk of audio. We keep a rolling buffer of
// the speech that has not been shown yet, re-transcribe it about once a second, and only trust a
// word once two passes in a row agree on it ("local agreement", from ufal/whisper_streaming, MIT).
// Agreed words are cut into lines at sentence ends, at a pause, or when a line gets too long, and
// the audio behind a finished line is dropped from the buffer.
//
// The model runs in a worker: `transcribe(audio, prompt)` is handed in and resolves to
// [{start, end, text}] in seconds from the start of `audio`. Speech detection (Silero VAD) runs in
// its own worker and its probabilities arrive through addVad(); without them a loudness check is
// used, like the app's fallback.

import { WINDOW, speechSegments, collectChunks, SpeechMap } from "./vad.js";

export const SR = 16000;
export const SENT_END = /[.!?。！？]['"”’)\]]*$/;
export const CLAUSE_END = /[,;:，；：、]['"”’)\]]*$/;
const ABBREV = new Set(["mr.", "mrs.", "ms.", "dr.", "st.", "rev.", "vs.", "e.g.", "i.e.", "etc.", "no.", "ch.", "vv.", "v."]);
// Whisper's well-known inventions on silence or music. A line that is only one of these is dropped.
export const HALLUCINATIONS = new Set([
  "thank you.", "thanks for watching!", "thank you for watching.", "thanks for watching.",
  "please subscribe.", "subtitles by the amara.org community", "you", "bye.", "so.",
  "ขอบคุณที่รับชม", "ขอบคุณครับ", "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目", "字幕由amara.org社区提供",
]);

// Python's re.sub(r"[\W_]+", "", t.lower()).
export const norm = (t) => String(t).toLowerCase().replace(/[^\p{L}\p{N}\p{M}]+/gu, "");
export const join = (words) => words.map((w) => w.text).join("").trim();

// Removes scraps Whisper makes out of the end of the previous word in a pause ("son. on. .").
export function dropEchoes(words, before) {
  const out = [];
  for (const w of words) {
    const prev = out.length ? out[out.length - 1] : before;
    const n = norm(w.text);
    if (!n) continue;
    if (prev && w.start - prev.end < 0.15) {
      const p = norm(prev.text);
      if (n.length < p.length && p.endsWith(n)) continue;
    }
    out.push(w);
  }
  return out;
}

// Two normalized words Whisper could make from the same sound ("night" / "right").
export function soundsLike(a, b) {
  if (!a || !b) return false;
  if (a === b || b.endsWith(a) || a.endsWith(b)) return true;
  if (a.length !== b.length || a.length < 4) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) diff++;
  return diff === 1;
}

// For the dimmed partial only: Whisper sometimes says a phrase twice ("good people. people.").
export function dropRepeats(words) {
  const out = [];
  for (const w of words) {
    out.push(w);
    for (const n of [3, 2, 1]) {
      if (out.length < 2 * n) continue;
      let same = true;
      for (let k = 0; k < n; k++) {
        if (norm(out[out.length - n + k].text) !== norm(out[out.length - 2 * n + k].text)) { same = false; break; }
      }
      if (same && (n > 1 || SENT_END.test(out[out.length - 1].text.trim()))) {
        out.splice(out.length - n, n);
        break;
      }
    }
  }
  return out;
}

// Local agreement of two passes (adapted from ufal/whisper_streaming, MIT).
export class HypothesisBuffer {
  constructor() {
    this.buffer = [];     // last pass's words that are not confirmed yet
    this.tail = [];       // last confirmed words (up to 5), to spot repeats
    this.lastTime = 0;    // end of the last confirmed word (absolute seconds)
  }

  insert(words) {
    let fresh = words.filter((w) => w.start > this.lastTime - 0.1);
    fresh = dropEchoes(fresh, this.tail.length ? this.tail[this.tail.length - 1] : null);
    if (fresh.length && this.tail.length && Math.abs(fresh[0].start - this.lastTime) < 1.0) {
      // Whisper often re-hears the last confirmed word or two at the buffer start.
      for (let n = Math.min(this.tail.length, fresh.length, 5); n > 0; n--) {
        const a = this.tail.slice(-n).map((w) => norm(w.text)).join("\u0000");
        const b = fresh.slice(0, n).map((w) => norm(w.text)).join("\u0000");
        if (a === b) { fresh = fresh.slice(n); break; }
      }
    }
    return fresh;
  }

  agree(fresh) {
    const confirmed = [];
    fresh = [...fresh];
    while (fresh.length && this.buffer.length && norm(fresh[0].text) === norm(this.buffer[0].text)) {
      confirmed.push(fresh.shift());
      this.buffer.shift();
    }
    this.buffer = fresh;
    this.mark(confirmed);
    return confirmed;
  }

  // Speaker paused: accept the unconfirmed tail as heard.
  takeAll() {
    const words = this.buffer;
    this.buffer = [];
    this.mark(words);
    return words;
  }

  mark(words) {
    for (const w of words) {
      this.tail.push(w);
      if (this.tail.length > 5) this.tail.shift();
      this.lastTime = Math.max(this.lastTime, w.end);
    }
  }
}

export class StreamingTranscriber {
  constructor({ transcribe, language = "en", spaced = true, hints = "", pauseSec = 0.9, maxWords = 22,
    maxChars = 50, maxBufferSec = 20 }) {
    this.transcribeFn = transcribe;
    this.language = language;
    this.spaced = spaced;
    this.hints = String(hints || "").trim();
    this.pauseSec = pauseSec;
    this.maxWords = maxWords;
    this.maxChars = maxChars;
    this.maxBufferSec = maxBufferSec;
    this.chunks = [];          // audio pushed since the last pass (joined once per pass)
    this.audio = new Float32Array(0);
    this.start = 0;            // absolute sample index of audio[0]
    this.hyp = new HypothesisBuffer();
    this.pending = [];         // confirmed words not yet shown as a finished line
    this.history = [];         // last finished lines (up to 3), Whisper's context prompt
    this.shownUntil = 0;       // end of the last finished line (absolute seconds)
    this.sentenceOpen = false; // the last finished line stopped mid-sentence
    this.lastWord = null;      // last word of the last finished line
    this.lastTranscribeSec = 0;
    this.vadBase = 0;          // absolute window index of vadProbs[0]
    this.vadProbs = [];        // Silero speech probability per 512-sample window
    this.usedVad = false;      // the last pass had speech detection (for the stats)
  }

  // ------------------------------------------------------------------ audio in
  push(chunk) {
    this.chunks.push(chunk);
  }

  // Speech probabilities for windows first, first+1, ... (absolute: window k = samples k*512 on).
  addVad(first, probs) {
    if (!this.vadProbs.length || first !== this.vadBase + this.vadProbs.length) {
      this.vadBase = first;
      this.vadProbs = [];
    }
    for (const p of probs) this.vadProbs.push(p);
  }

  flat() {
    if (this.chunks.length) {
      let n = this.audio.length;
      for (const c of this.chunks) n += c.length;
      const a = new Float32Array(n);
      a.set(this.audio, 0);
      let at = this.audio.length;
      for (const c of this.chunks) { a.set(c, at); at += c.length; }
      this.audio = a;
      this.chunks = [];
    }
    return this.audio;
  }

  get samples() {
    let n = this.audio.length;
    for (const c of this.chunks) n += c.length;
    return n;
  }

  get offset() {
    return this.start / SR;
  }

  get now() {
    return (this.start + this.samples) / SR;
  }

  // ------------------------------------------------------------------ one pass
  // Transcribes the buffer once. Resolves to {lines, partial}.
  async step(final = false) {
    const lines = [];
    const audio = this.flat();
    if (audio.length >= Math.trunc(0.3 * SR)) {
      const silentTail = this.silentTailSec(audio);
      if (silentTail >= audio.length / SR - 0.05 && !this.hyp.buffer.length && !this.pending.length) {
        // Nothing but silence in the buffer: skip Whisper (it invents words on silence).
        this.trimTo(this.now - 0.5);
      } else {
        const words = await this.transcribe(audio);
        const fresh = this.hyp.insert(words);
        this.pending.push(...this.hyp.agree(fresh));
        if (final || silentTail >= this.pauseSec) this.pending.push(...this.hyp.takeAll());
        lines.push(...this.cut(final || silentTail >= this.pauseSec));
      }
    }
    if (final) {
      this.pending.push(...this.hyp.takeAll());
      lines.push(...this.cut(true));
    }
    this.trim();
    return { lines, partial: this.capital(join(dropRepeats([...this.pending, ...this.hyp.buffer]))) };
  }

  // Words Whisper has agreed on that aren't a finished line yet (what a draft translates).
  pendingText() {
    return this.capital(join(this.pending));
  }

  prompt() {
    // The operator's words go first and are never cut; the last lines fill the rest (Whisper reads
    // about 220 tokens of prompt, so keep it short).
    const hints = this.hints.slice(0, 240);
    let said = "";
    if (this.history.length) said = this.history.join(" ");
    else if (this.language === "en") said = "Good morning, everyone. Let's open our Bibles together.";  // nudges punctuation
    said = said.slice(-Math.max(80, 400 - hints.length));
    return [hints, said].filter(Boolean).join(" ");
  }

  async transcribe(audio) {
    const t0 = performance.now();
    const offset = this.offset;
    // Like the app's vad_filter: Whisper hears only the speech, so long silences can't make it invent.
    const chunks = this.speech(audio, 500, 300);
    let words = [];
    if (!chunks) {
      words = await this.transcribeFn(audio, this.prompt());
    } else if (chunks.length) {
      const map = new SpeechMap(chunks, SR);
      words = (await this.transcribeFn(collectChunks(audio, chunks), this.prompt())).map((w) => map.word(w));
    }
    this.lastTranscribeSec = (performance.now() - t0) / 1000;
    return words.map((w) => ({ start: w.start + offset, end: w.end + offset, text: w.text }));
  }

  // ------------------------------------------------------------------ speech detection
  // Speech spans of audio[from..] in samples relative to `from`, or null when the VAD is not
  // keeping up (then the caller falls back to no filtering / the loudness check).
  speech(audio, minSilenceMs, speechPadMs, from = 0) {
    if (!this.vadProbs.length) return null;
    const absFrom = this.start + from;
    const absEnd = this.start + audio.length;
    const first = Math.floor(absFrom / WINDOW);
    const coveredTo = (this.vadBase + this.vadProbs.length) * WINDOW;
    if (first < this.vadBase || coveredTo < absEnd - 0.5 * SR) return null;
    const probs = this.vadProbs.slice(first - this.vadBase);
    return speechSegments(probs, { pos0: first * WINDOW - absFrom, length: audio.length - from, sr: SR,
      minSilenceMs, speechPadMs });
  }

  // Seconds of non-speech at the end of the buffer (the app uses Silero VAD on the last 4 seconds).
  silentTailSec(audio) {
    const from = Math.max(0, audio.length - 4 * SR);
    const spans = this.speech(audio, 250, 60, from);
    this.usedVad = !!spans;
    if (spans) {
      const lastEnd = spans.length ? spans[spans.length - 1].end : 0;
      return (audio.length - from - lastEnd) / SR;
    }
    return silentTailByLoudness(audio);
  }

  // ------------------------------------------------------------------ lines out
  size(words) {
    return this.spaced ? words.length : join(words).length;
  }

  limit() {
    return this.spaced ? this.maxWords : this.maxChars;
  }

  cut(force) {
    const out = [];
    while (this.pending.length) {
      let cut = null;
      for (let i = 0; i < this.pending.length; i++) {
        const t = this.pending[i].text.trim();
        if (SENT_END.test(t) && !ABBREV.has(t.toLowerCase())) { cut = i + 1; break; }
        if (this.size(this.pending.slice(0, i + 1)) >= this.limit()) {
          // Too long without a full stop: break at the last comma if there is a decent one.
          let good = -1;
          for (let j = 0; j <= i; j++) {
            if (CLAUSE_END.test(this.pending[j].text.trim()) && this.size(this.pending.slice(0, j + 1)) >= this.limit() * 0.4) good = j;
          }
          cut = good >= 0 ? good + 1 : i + 1;
          break;
        }
      }
      if (cut === null) {
        if (!force) break;
        cut = this.pending.length;
      }
      const words = this.pending.slice(0, cut);
      this.pending = this.pending.slice(cut);
      const text = this.capital(join(words));
      if (text && !HALLUCINATIONS.has(text.toLowerCase()) && !this.scrap(words)) {
        out.push({ text, start: words[0].start, end: words[words.length - 1].end });
        this.history.push(text);
        if (this.history.length > 3) this.history.shift();
        this.sentenceOpen = !SENT_END.test(text);
        this.lastWord = words[words.length - 1];
      }
      this.shownUntil = Math.max(this.shownUntil, words[words.length - 1].end);
    }
    return out;
  }

  // A line of one or two words made from the tail of the line before ("Chapter 3." then "3.").
  // Real short lines like "Amen." take longer to say.
  scrap(words) {
    if (words.length > 2) return false;
    if (words[words.length - 1].end - words[0].start < 0.25) return true;
    const last = this.lastWord;
    return !!last && words[0].start - last.end < 2.5 && soundsLike(norm(words[0].text), norm(last.text));
  }

  // A new sentence starts with a capital (Whisper often lower-cases it after the prompt).
  capital(text) {
    if (this.sentenceOpen || !text) return text;
    const chars = [...text];
    const i = chars.findIndex((c) => /\p{L}/u.test(c));
    if (i < 0) return text;
    chars[i] = chars[i].toUpperCase();
    return chars.join("");
  }

  trim() {
    // The buffer starts where the last finished line ended, so already-shown speech isn't heard
    // again (it stamps the next sentence's first words with old times and they get dropped).
    if (this.shownUntil > this.offset) this.trimTo(this.shownUntil);
    if (!this.pending.length && !this.hyp.buffer.length && this.hyp.lastTime > this.offset) this.trimTo(this.hyp.lastTime);
    if (this.samples / SR > this.maxBufferSec) {
      // Whisper is stuck (no agreement for a long time): show what we have and move on.
      this.pending.push(...this.hyp.takeAll());
      this.trimTo(this.now - 2.0);
    }
    // Speech probabilities from before the buffer are no longer needed.
    const drop = Math.floor(this.start / WINDOW) - 1 - this.vadBase;
    if (drop > 0 && this.vadProbs.length) {
      const n = Math.min(drop, this.vadProbs.length);
      this.vadProbs.splice(0, n);
      this.vadBase += n;
    }
  }

  trimTo(t) {
    t = Math.min(Math.max(t, this.offset), this.now);
    const cut = Math.trunc((t - this.offset) * SR);
    if (cut > 0) {
      const audio = this.flat();
      this.audio = audio.slice(cut);
      this.start += cut;
    }
  }
}

// The app's fallback when the VAD isn't available: a plain loudness check on the last 4 seconds.
export function silentTailByLoudness(audio) {
  const tail = audio.subarray(Math.max(0, audio.length - 4 * SR));
  const frame = Math.trunc(0.05 * SR);
  const n = Math.floor(tail.length / frame);
  if (n === 0) return 0;
  const rms = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    let s = 0;
    for (let k = i * frame; k < (i + 1) * frame; k++) s += tail[k] * tail[k];
    rms[i] = Math.sqrt(s / frame);
  }
  const gate = Math.max(0.01, percentile(Float64Array.from(rms).sort(), 90) * 0.15);
  let lastEnd = 0;
  for (let i = n - 1; i >= 0; i--) if (rms[i] > gate) { lastEnd = (i + 1) * frame; break; }
  return (tail.length - lastEnd) / SR;
}

// numpy's default (linear) percentile on sorted values.
function percentile(sorted, q) {
  if (!sorted.length) return 0;
  const pos = (sorted.length - 1) * q / 100;
  const lo = Math.floor(pos), hi = Math.ceil(pos);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}
