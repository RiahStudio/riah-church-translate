// Languages, the same list and codes as the app (riah_translate/langs.py).
// `whisper` is the speech-recognition code (null = can be shown as a translation but not listened to);
// `nllb` is the translation model's code; `spaced` says whether words are separated by spaces.
// The browser version listens to English only (the English speech models are the small ones).

export const LANGS = {
  en: { name: "English", native: "English", whisper: "en", nllb: "eng_Latn", spaced: true },
  th: { name: "Thai", native: "ไทย", whisper: "th", nllb: "tha_Thai", spaced: false },
  zh: { name: "Chinese (Simplified)", native: "中文", whisper: "zh", nllb: "zho_Hans", spaced: false },
  "zh-TW": { name: "Chinese (Traditional)", native: "繁體中文", whisper: "zh", nllb: "zho_Hant", spaced: false },
  my: { name: "Burmese", native: "မြန်မာ", whisper: "my", nllb: "mya_Mymr", spaced: false },
  shn: { name: "Shan", native: "တႆး", whisper: null, nllb: "shn_Mymr", spaced: false },
  lo: { name: "Lao", native: "ລາວ", whisper: "lo", nllb: "lao_Laoo", spaced: false },
  km: { name: "Khmer", native: "ខ្មែរ", whisper: "km", nllb: "khm_Khmr", spaced: false },
  vi: { name: "Vietnamese", native: "Tiếng Việt", whisper: "vi", nllb: "vie_Latn", spaced: true },
  ko: { name: "Korean", native: "한국어", whisper: "ko", nllb: "kor_Hang", spaced: true },
  ja: { name: "Japanese", native: "日本語", whisper: "ja", nllb: "jpn_Jpan", spaced: false },
  id: { name: "Indonesian", native: "Bahasa Indonesia", whisper: "id", nllb: "ind_Latn", spaced: true },
  tl: { name: "Tagalog", native: "Tagalog", whisper: "tl", nllb: "tgl_Latn", spaced: true },
  hi: { name: "Hindi", native: "हिन्दी", whisper: "hi", nllb: "hin_Deva", spaced: true },
  es: { name: "Spanish", native: "Español", whisper: "es", nllb: "spa_Latn", spaced: true },
  fr: { name: "French", native: "Français", whisper: "fr", nllb: "fra_Latn", spaced: true },
  de: { name: "German", native: "Deutsch", whisper: "de", nllb: "deu_Latn", spaced: true },
  pt: { name: "Portuguese", native: "Português", whisper: "pt", nllb: "por_Latn", spaced: true },
  ru: { name: "Russian", native: "Русский", whisper: "ru", nllb: "rus_Cyrl", spaced: true },
};

// What the browser can listen to (Whisper's English-only models).
export const LISTENS = new Set(["en"]);

export function lang(code) {
  const l = LANGS[code];
  if (!l) throw new Error(`Unknown language '${code}'`);
  return l;
}

export function publicList() {
  return Object.entries(LANGS).map(([code, v]) => ({ code, name: v.name, native: v.native, can_listen: LISTENS.has(code) }));
}
