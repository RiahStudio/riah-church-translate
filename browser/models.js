// Which models the page downloads, pinned to exact versions so a change on Hugging Face can never
// change what runs in church. Everything comes from Hugging Face's free CDN straight into the
// visitor's browser cache; our own site only serves the small page files.

export const TRANSFORMERS_URL = "https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.8.1/dist/transformers.min.js";
export const HF = "https://huggingface.co";

const WHISPER_SMALL_FILES = { "config.json": 2e3, "generation_config.json": 3e3, "preprocessor_config.json": 1e3,
  "tokenizer.json": 2405679, "tokenizer_config.json": 282662 };

// Byte sizes are only for the progress bar before each download reports its own size.
export const SPEECH = {
  "base.en": {
    id: "onnx-community/whisper-base.en_timestamped",
    revision: "fa239a41836c3305f6beec180e5940f3823ff5b8",
    wasm: { dtype: "q8", files: { "onnx/encoder_model_quantized.onnx": 23159173, "onnx/decoder_model_merged_quantized.onnx": 53712196 } },
    webgpu: { dtype: { encoder_model: "fp32", decoder_model_merged: "q4" },
      files: { "onnx/encoder_model.onnx": 82451730, "onnx/decoder_model_merged_q4.onnx": 123736279 } },
    small: WHISPER_SMALL_FILES,
  },
  "small.en": {
    id: "onnx-community/whisper-small.en_timestamped",
    revision: "8085393831131554ad978a4e438295a734d3d2c0",
    wasm: { dtype: "q8", files: { "onnx/encoder_model_quantized.onnx": 92240508, "onnx/decoder_model_merged_quantized.onnx": 156794981 } },
    webgpu: { dtype: { encoder_model: "fp32", decoder_model_merged: "q4" },
      files: { "onnx/encoder_model.onnx": 352791798, "onnx/decoder_model_merged_q4.onnx": 233418140 } },
    small: WHISPER_SMALL_FILES,
  },
};

export const TRANSLATE = {
  id: "Xenova/nllb-200-distilled-600M",
  revision: "261c31d1a5732c67cdd16d80e8d6088507c7ccea",
  dtype: "q8",
  files: { "onnx/encoder_model_quantized.onnx": 419120483, "onnx/decoder_model_merged_quantized.onnx": 475505771,
    "tokenizer.json": 17331224, "tokenizer_config.json": 600, "config.json": 1e3, "generation_config.json": 200 },
};

// Silero VAD (MIT): tells speech from silence, so pauses end lines and Whisper never hears long silences.
export const VAD = {
  id: "onnx-community/silero-vad",
  revision: "e71cae966052b992a7eca6b17738916ce0eca4ec",
  files: { "onnx/model.onnx": 2243022 },
};

export function speechFiles(name, device) {
  const m = SPEECH[name];
  return { ...m.small, ...m[device === "webgpu" ? "webgpu" : "wasm"].files };
}

export const fileUrl = (id, revision, file) => `${HF}/${id}/resolve/${revision}/${file}`;

// Bytes still to download (files not in the browser's model cache yet). transformers.js keeps
// downloads in Cache Storage under "transformers-cache", keyed by the file's URL.
export async function missingBytes(id, revision, files) {
  let cache = null;
  try { cache = await caches.open("transformers-cache"); } catch { return sum(files); }
  let n = 0;
  for (const [f, size] of Object.entries(files)) {
    try { if (!(await cache.match(fileUrl(id, revision, f)))) n += size; } catch { n += size; }
  }
  return n;
}

const sum = (files) => Object.values(files).reduce((a, b) => a + b, 0);

// Turns transformers.js progress events into one running total for a whole model.
export class Progress {
  constructor(files, onChange) {
    this.known = { ...files };
    this.loaded = {};
    this.onChange = onChange;
    this.last = 0;
  }

  event(e) {
    if (e.status !== "progress" && e.status !== "done") return;
    const f = e.file;
    if (e.total) this.known[f] = e.total;
    this.loaded[f] = e.status === "done" ? (this.known[f] || e.loaded || 0) : (e.loaded || 0);
    const now = Date.now();
    if (e.status === "done" || now - this.last > 250) {
      this.last = now;
      const total = Object.values(this.known).reduce((a, b) => a + b, 0);
      const loaded = Object.values(this.loaded).reduce((a, b) => a + b, 0);
      this.onChange(Math.min(loaded, total), total);
    }
  }
}
