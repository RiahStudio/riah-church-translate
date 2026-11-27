// Speech / no-speech bookkeeping on top of Silero VAD probabilities (one per 512 samples at 16 kHz).
// A port of the parts of faster-whisper's vad.py the app uses (MIT): get_speech_timestamps and
// SpeechTimestampsMap. The model itself runs in vad-worker.js; these functions are pure.

export const WINDOW = 512;

// Speech spans [{start, end}] in samples. `probs[i]` covers samples pos0 + i*WINDOW onwards
// (pos0 may be negative when the first window starts before the audio does); `length` is the
// audio length in samples. Same rules and defaults as faster-whisper's VadOptions.
export function speechSegments(probs, { pos0 = 0, length, sr = 16000, threshold = 0.5, minSilenceMs = 2000,
  speechPadMs = 400, minSpeechMs = 0 } = {}) {
  const negThreshold = Math.max(threshold - 0.15, 0.01);
  const minSpeech = sr * minSpeechMs / 1000;
  const pad = sr * speechPadMs / 1000;
  const minSilence = sr * minSilenceMs / 1000;
  const at = (i) => Math.max(0, pos0 + WINDOW * i);

  let triggered = false;
  const speeches = [];
  let current = null;
  let tempEnd = 0;
  for (let i = 0; i < probs.length; i++) {
    const p = probs[i];
    if (p >= threshold && tempEnd) tempEnd = 0;
    if (p >= threshold && !triggered) {
      triggered = true;
      current = { start: at(i) };
      continue;
    }
    if (p < negThreshold && triggered) {
      if (!tempEnd) tempEnd = at(i);
      if (at(i) - tempEnd < minSilence) continue;
      current.end = tempEnd;
      if (current.end - current.start > minSpeech) speeches.push(current);
      current = null;
      tempEnd = 0;
      triggered = false;
    }
  }
  if (current && length - current.start > minSpeech) {
    current.end = length;
    speeches.push(current);
  }
  for (let i = 0; i < speeches.length; i++) {
    const s = speeches[i];
    if (i === 0) s.start = Math.trunc(Math.max(0, s.start - pad));
    if (i !== speeches.length - 1) {
      const next = speeches[i + 1];
      const gap = next.start - s.end;
      if (gap < 2 * pad) {
        s.end += Math.floor(gap / 2);
        next.start = Math.trunc(Math.max(0, next.start - Math.floor(gap / 2)));
      } else {
        s.end = Math.trunc(Math.min(length, s.end + pad));
        next.start = Math.trunc(Math.max(0, next.start - pad));
      }
    } else {
      s.end = Math.trunc(Math.min(length, s.end + pad));
    }
  }
  return speeches;
}

// Glue the speech spans together (what Whisper hears) ...
export function collectChunks(audio, chunks) {
  let n = 0;
  for (const c of chunks) n += c.end - c.start;
  const out = new Float32Array(n);
  let at = 0;
  for (const c of chunks) {
    out.set(audio.subarray(c.start, c.end), at);
    at += c.end - c.start;
  }
  return out;
}

// ... and map times in the glued audio back to the original (faster-whisper's SpeechTimestampsMap).
export class SpeechMap {
  constructor(chunks, sr = 16000) {
    this.sr = sr;
    this.chunkEnd = [];
    this.silenceBefore = [];
    let prevEnd = 0, silent = 0;
    for (const c of chunks) {
      silent += c.start - prevEnd;
      prevEnd = c.end;
      this.chunkEnd.push(c.end - silent);
      this.silenceBefore.push(silent / sr);
    }
  }

  chunkIndex(time) {
    const sample = Math.trunc(time * this.sr);
    let lo = 0, hi = this.chunkEnd.length;   // bisect_right
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (sample < this.chunkEnd[mid]) hi = mid; else lo = mid + 1;
    }
    return Math.min(lo, this.chunkEnd.length - 1);
  }

  // A word's start and end are mapped with the chunk its middle falls in, as faster-whisper does.
  word(w) {
    const k = this.chunkIndex((w.start + w.end) / 2);
    const r = (t) => Math.round((this.silenceBefore[k] + t) * 100) / 100;
    return { ...w, start: r(w.start), end: r(w.end) };
  }
}
