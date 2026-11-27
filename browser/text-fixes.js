// Clean-ups on the translator's output, the same as the app (riah_translate/mt.py).
import { phraseKey } from "./church-words.js";

const THAI = "฀-๿";
const CJK = "㐀-鿿豈-﫿";
const FULL = { ",": "，", ".": "。", "!": "！", "?": "？", ":": "：", ";": "；" };
const THAI_RUN = new RegExp(`[${THAI}]+`, "g");
const THAI_GAP = new RegExp(`(?<=[${THAI}]) (?=[${THAI}])`, "g");
const CJK_PUNCT = new RegExp(`(?<=[${CJK}])\\s*([,.!?:;])(?!\\d)\\s*`, "g");

// Script clean-ups for model output that would look wrong on screen.
export function tidy(text, tgt) {
  text = String(text).replace(/\s+/g, " ").trim();
  if (tgt === "th") {
    text = text.replaceAll("ํา", "ำ");   // nikhahit + sara aa -> sara am
    // Thai puts spaces between phrases, not words. When the model splits every word, join them up.
    const runs = text.match(THAI_RUN) || [];
    if (runs.length >= 3 && runs.reduce((n, r) => n + r.length, 0) / runs.length < 8) text = text.replace(THAI_GAP, "");
  } else if (tgt === "lo") {
    text = text.replaceAll("ໍາ", "ຳ");
  } else if (tgt === "zh" || tgt === "zh-TW" || tgt === "ja") {
    // Chinese punctuation is full width; the model writes the English kind.
    text = text.replace(CJK_PUNCT, (_, p) => FULL[p]);
  }
  return text;
}

// A short church phrase said on its own ("Amen."), with the words churches use.
export function exactPhrase(text, tgt, glossary) {
  const exact = glossary && glossary.exact;
  if (!exact || text.length > 60) return null;
  return (exact[phraseKey(text)] || {})[tgt] || null;
}

// Glossary fixes: exact find -> replace on the translated text.
export function applyFixes(text, fixes) {
  for (const pair of fixes || []) {
    if (Array.isArray(pair) && pair.length === 2 && pair[0]) text = text.split(pair[0]).join(pair[1]);
  }
  return text;
}

export const DRAFT_END = /[.。!！?？]+$/;

// NLLB now and then translates only half of a two-part sentence. Whole lines were at least this long
// next to the English (in characters) on the Mac test runs; half lines were shorter.
const MIN_COVER = { th: 0.5, zh: 0.19, "zh-TW": 0.19 };

export function tooShort(src, out, tgt) {
  return src.length >= 30 && out.length < (MIN_COVER[tgt] || 0) * src.length;
}

// Split at the comma (or ; :) nearest the middle, with at least 3 words each side; else null.
export function halves(text) {
  let best = null;
  for (const m of text.matchAll(/[,;:]\s+/g)) {
    const left = text.slice(0, m.index + 1), right = text.slice(m.index + m[0].length);
    if (left.split(/\s+/).filter(Boolean).length >= 3 && right.split(/\s+/).filter(Boolean).length >= 3) {
      const off = Math.abs(left.length - right.length);
      if (!best || off < best[0]) best = [off, [left, right]];
    }
  }
  return best ? best[1] : null;
}

export function joinHalves(a, b, tgt) {
  if (tgt.startsWith("zh")) return a.replace(/[。.，,]+$/, "") + "，" + b;
  return a.replace(/[ .]+$/, "") + " " + b;
}
