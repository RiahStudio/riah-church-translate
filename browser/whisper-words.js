// Word timings from Whisper's token timings, the way faster-whisper does it (MIT): tokens are
// grouped into words, long words at sentence ends and after a pause are trimmed, and punctuation
// is glued to its word. Pure functions, so tests/browser_port_test.mjs can check them in Node.

const PUNCTUATION = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~";   // Python's string.punctuation
const PREPEND = "\"'“¿([{-";
const APPEND = "\"'.。,，!！?？:：”)]}、";
const SENTENCE_END = ".。!！?？";

// faster-whisper's split_tokens_on_unicode: a token that ends half way through a character is
// kept with the next one.
export function splitOnUnicode(tokens, decode) {
  const full = decode(tokens);
  const words = [], wordTokens = [];
  let current = [], offset = 0;
  for (const t of tokens) {
    current.push(t);
    const decoded = decode(current);
    const at = decoded.indexOf("�");
    if (at < 0 || (offset + at < full.length && full[offset + at] === "�")) {
      words.push(decoded);
      wordTokens.push(current);
      current = [];
      offset += decoded.length;
    }
  }
  return [words, wordTokens];
}

// faster-whisper's split_tokens_on_spaces (English): a new word at a space, a punctuation mark or
// a special token.
export function splitOnSpaces(tokens, decode, eot) {
  const [subwords, subTokens] = splitOnUnicode(tokens, decode);
  const words = [], wordTokens = [];
  for (let i = 0; i < subwords.length; i++) {
    const sub = subwords[i], toks = subTokens[i];
    const special = toks[0] >= eot;
    const withSpace = sub.startsWith(" ");
    const punctuation = PUNCTUATION.includes(sub.trim());
    if (special || withSpace || punctuation || words.length === 0) {
      words.push(sub);
      wordTokens.push([...toks]);
    } else {
      words[words.length - 1] += sub;
      wordTokens[wordTokens.length - 1].push(...toks);
    }
  }
  return [words, wordTokens];
}

function median(values) {
  if (!values.length) return 0;
  const s = [...values].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

// faster-whisper's merge_punctuations, in place.
export function mergePunctuations(alignment, prepended = PREPEND, appended = APPEND) {
  let i = alignment.length - 2, j = alignment.length - 1;
  while (i >= 0) {
    const previous = alignment[i], following = alignment[j];
    if (previous.word.startsWith(" ") && prepended.includes(previous.word.trim())) {
      following.word = previous.word + following.word;
      following.tokens = [...previous.tokens, ...following.tokens];
      previous.tokens = [];
      previous.word = "";
    } else {
      j = i;
    }
    i -= 1;
  }
  i = 0; j = 1;
  while (j < alignment.length) {
    const previous = alignment[i], following = alignment[j];
    if (!previous.word.endsWith(" ") && appended.includes(following.word)) {
      previous.word += following.word;
      previous.tokens = [...previous.tokens, ...following.tokens];
      following.tokens = [];
      following.word = "";
    } else {
      i = j;
    }
    j += 1;
  }
}

const r2 = (x) => Math.round(x * 100) / 100;

// textTokens: the decoded text tokens (no special tokens). jump(k): the time (seconds) at which
// token boundary k falls, k = 0 .. textTokens.length (faster-whisper's jump_times).
// Returns [{text, start, end}] like faster-whisper's segment.words for one window.
export function wordTimings(textTokens, jump, decode, eot) {
  if (!textTokens.length) return [];
  const [words, wordTokens] = splitOnSpaces([...textTokens, eot], decode, eot);
  if (wordTokens.length <= 1) return [];
  const bounds = [0];
  for (let k = 0; k < wordTokens.length - 1; k++) bounds.push(bounds[k] + wordTokens[k].length);
  // zip(words, starts, ends) stops at the shorter list, which drops the end-of-text "word".
  const alignment = [];
  for (let k = 0; k < bounds.length - 1; k++) {
    alignment.push({ word: words[k], tokens: wordTokens[k], start: jump(bounds[k]), end: jump(bounds[k + 1]) });
  }

  const durations = alignment.map((w) => w.end - w.start).filter((d) => d !== 0);
  const medianDuration = Math.min(0.7, durations.length ? median(durations) : 0);
  const maxDuration = medianDuration * 2;
  // Truncate long words at sentence boundaries.
  if (durations.length) {
    for (let i = 1; i < alignment.length; i++) {
      if (alignment[i].end - alignment[i].start > maxDuration) {
        if (SENTENCE_END.includes(alignment[i].word)) alignment[i].end = alignment[i].start + maxDuration;
        else if (SENTENCE_END.includes(alignment[i - 1].word)) alignment[i].start = alignment[i].end - maxDuration;
      }
    }
  }
  mergePunctuations(alignment);

  const out = alignment.filter((w) => w.word).map((w) => ({ text: w.word, start: r2(w.start), end: r2(w.end) }));
  // The first and second word after a pause may not be longer than twice the median word
  // (last_speech_timestamp is 0: every pass is a fresh transcription of the buffer).
  if (out.length && out[0].end > medianDuration * 4 && (out[0].end - out[0].start > maxDuration
      || (out.length > 1 && out[1].end - out[0].start > maxDuration * 2))) {
    if (out.length > 1 && out[1].end - out[1].start > maxDuration) {
      const boundary = Math.max(out[1].end / 2, out[1].end - maxDuration);
      out[0].end = out[1].start = boundary;
    }
    out[0].start = Math.max(0, out[0].end - maxDuration);
  }
  return out;
}
