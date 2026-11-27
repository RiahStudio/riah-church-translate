// Operator page for the browser version of Riah Church Translate. Everything runs here, in this
// browser: the models download once from Hugging Face into the browser's own storage, and no sound,
// text or notes ever leave this computer. The church screen is a second window of this same
// browser, fed over a BroadcastChannel.

import { Hub } from "./hub.js";
import { Engine, plainError } from "./engine.js";
import { publicList } from "./langs.js";
import { churchGlossary } from "./church-words.js";
import { MicSource, FileSource, decodeFile, listInputs, channelsOf } from "./audio.js";
import { SPEECH, TRANSLATE, VAD, speechFiles, missingBytes } from "./models.js";
import { readNotes, namesFrom, joinHints } from "./notes.js";

const $ = (id) => document.getElementById(id);
const HERE = new URL(".", import.meta.url);
const params = new URLSearchParams(location.search);
const STORE = "riah-live";
const STATUS = {
  stopped: ["", "Stopped"],
  loading: ["riah-status--revise", "Getting ready"],
  listening: ["riah-status--keep", "Live"],
  error: ["riah-status--discard", "Problem"],
};

// ---------------------------------------------------------------------- can this browser do it?
function missingFeatures() {
  const out = [];
  if (!window.isSecureContext) out.push("a secure (https) page");
  if (typeof WebAssembly !== "object") out.push("WebAssembly");
  if (typeof AudioWorkletNode === "undefined") out.push("AudioWorklet");
  if (typeof BroadcastChannel === "undefined") out.push("BroadcastChannel");
  if (!("caches" in window)) out.push("Cache Storage");
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) out.push("microphone access");
  let moduleWorkers = false;
  try {
    const url = URL.createObjectURL(new Blob([""], { type: "text/javascript" }));
    new Worker(url, { get type() { moduleWorkers = true; return "module"; } }).terminate();
    URL.revokeObjectURL(url);
  } catch { /* checked through the getter */ }
  if (!moduleWorkers) out.push("module workers");
  return out;
}

// ---------------------------------------------------------------------- settings (this browser only)
function loadSettings() {
  try { return JSON.parse(localStorage.getItem(STORE) || "{}") || {}; } catch { return {}; }
}
let settings = loadSettings();
function save(patch) {
  Object.assign(settings, patch);
  try { localStorage.setItem(STORE, JSON.stringify(settings)); } catch { /* private window: not kept */ }
}

// ---------------------------------------------------------------------- state
const hub = new Hub({
  display: { ...(settings.display || {}), hidden: false },
  langs: { src: settings.src || "en", tgts: settings.tgts || ["th"] },
  names: publicList(),
});
const engine = new Engine(hub, { base: HERE, onNote: note, onEvent: event });
let state = "stopped";
let running = false;
let mic = null;             // the microphone, opened at Start (before the models finish loading)
let sink = null;            // where its sound goes once the engine is ready
let micState = null;
let noteNames = [];         // names read from the sermon notes (memory only)
let feedStart = 0;          // when the sound started (for the rehearsal measurements)
const transcript = [];      // today's lines and translations (memory only, until "Save")
const test = params.has("rehearse") || params.has("test");
const live = window.riahLive = { events: [], summary: null, notes: [], state: () => state };

// ---------------------------------------------------------------------- status
function setStatus(s, detail) {
  state = s;
  const [cls, text] = STATUS[s] || STATUS.stopped;
  const chip = $("status");
  chip.className = "riah-status " + cls;
  chip.textContent = text;
  if (detail !== undefined) $("detail").textContent = detail;
  running = s === "loading" || s === "listening";
  $("startStop").textContent = running ? "Stop" : "Start";
  for (const id of ["src", "input", "channel", "swapLangs", "model", "churchWords", "rehearseGo"]) {
    const el = $(id);
    if (el) el.disabled = running;
  }
  if (!running) {
    setLevel(0);
    notes.asr = notes.mt = "";
    showNotes();
    releaseMic();
  }
  if (s === "listening") { notes.asr = ""; showNotes(); }
  holdWake(running);
}

function releaseMic() {
  const m = mic;
  mic = null;
  sink = null;
  micState = null;
  if (m) m.stop().catch(() => {});
}

function setLevel(rms) {
  const pct = Math.min(100, Math.round(Math.sqrt(Math.max(0, rms) / 0.25) * 100));
  $("meterFill").style.width = pct + "%";
  $("meter").setAttribute("aria-valuenow", String(pct));
}

let lastMt = null;
function setLag(lag, mtLag) {
  if (lag == null) { $("lag").textContent = ""; return; }
  let t = `Delay ${lag.toFixed(1)} s`;
  if (mtLag != null) t += ` · translation ${mtLag.toFixed(1)} s`;
  $("lag").textContent = t;
}

// ---------------------------------------------------------------------- download progress
const notes = { asr: "", mt: "", restart: "" };
const bars = { asr: null, mt: null };
let restartTimer = 0;
const MB = (b) => Math.round(b / 1e6);

function note(which, m) {
  if (which === "asr" || which === "mt") {
    const what = which === "asr" ? "speech model" : "translator";
    if (m.missing > 0) {
      notes[which] = `Downloading the ${what}: ${MB(m.loaded)} of ${MB(m.total)} MB (first time only).`;
      if (which === "mt" && state === "listening") notes.mt += " Spoken words show until then.";
    } else {
      notes[which] = `Loading the ${what} from this computer…`;
    }
    bars[which] = m.total ? m.loaded / m.total : 0;
  } else if (which === "mt-ready") {
    notes.mt = "";
    bars.mt = null;
    keepModels();
  } else if (which === "mt-failed") {
    notes.mt = `The translator didn't load. ${m} Trying again.`;
    bars.mt = null;
  } else if (which === "restart") {
    notes.restart = m;
    clearTimeout(restartTimer);
    restartTimer = setTimeout(() => { notes.restart = ""; showNotes(); }, 10000);
  }
  if (test) live.notes.push({ which, m, t: Math.round(performance.now()) });
  showNotes();
}

function showNotes() {
  const text = [notes.restart, notes.asr, notes.mt].filter(Boolean).join(" ");
  if ($("progress").textContent !== text) $("progress").textContent = text;
  const f = notes.asr && bars.asr != null ? bars.asr : notes.mt && bars.mt != null ? bars.mt : null;
  $("dl").hidden = f == null;
  if (f != null) $("dlFill").style.width = Math.round(Math.min(1, f) * 100) + "%";
}

// Asks the browser to keep the models (about 1 GB) instead of clearing them when space runs low.
function keepModels() {
  if (settings.persistAsked || !navigator.storage || !navigator.storage.persist) return;
  save({ persistAsked: true });
  navigator.storage.persist().catch(() => {});
}

// ---------------------------------------------------------------------- hub events -> this page
hub.taps.push((ev) => {
  switch (ev.type) {
    case "status":
      setStatus(ev.state, ev.detail);
      break;
    case "level":
      setLevel(ev.level);
      break;
    case "display":
      showDisplay(hub.display);
      break;
    case "line":
      transcript.push({ id: ev.id, src: ev.src, dst: {} });
      if (transcript.length > 5000) transcript.shift();
      setLag(ev.lag, lastMt);
      $("saveTranscript").disabled = false;
      break;
    case "translation": {
      for (let i = transcript.length - 1; i >= 0; i--) if (transcript[i].id === ev.id) { transcript[i].dst[ev.lang] = ev.dst; break; }
      if (ev.lag != null) lastMt = ev.lag;
      break;
    }
  }
  if (test && ev.type !== "level") live.events.push({ ...ev, at: feedStart ? (performance.now() - feedStart) / 1000 : null });
});

function event(ev) {
  if (ev.type === "summary") live.summary = ev;
}

// ---------------------------------------------------------------------- languages
const LANGS = publicList();

function fillLangs() {
  const src = $("src"), tgt = $("tgt"), tgt2 = $("tgt2");
  src.textContent = tgt.textContent = "";
  tgt2.length = 1;
  for (const l of LANGS) {
    const label = l.native && l.native !== l.name ? `${l.name} · ${l.native}` : l.name;
    if (l.can_listen) src.add(new Option(label, l.code));
    tgt.add(new Option(label, l.code));
    tgt2.add(new Option(label, l.code));
  }
  const tgts = settings.tgts || ["th"];
  src.value = LANGS.some((l) => l.can_listen && l.code === settings.src) ? settings.src : "en";
  tgt.value = tgts[0] || "th";
  tgt2.value = tgts[1] || "";
}

function chosenTgts() {
  const out = [];
  for (const v of [$("tgt").value, $("tgt2").value]) if (v && v !== $("src").value && !out.includes(v)) out.push(v);
  return out;
}

function sendTgts() {
  const src = $("src").value;
  const tgts = chosenTgts();
  if ($("tgt").value === src && !$("tgt2").value) $("detail").textContent = "Same as the spoken language: only the spoken words show.";
  save({ src, tgts: [$("tgt").value, $("tgt2").value].filter(Boolean) });
  hub.publish({ type: "langs", src, tgts });
  engine.setTargets(src, tgts);
}

// ---------------------------------------------------------------------- microphones
let inputs = [];

async function fillInputs() {
  inputs = await listInputs();
  const sel = $("input");
  const keep = sel.value || settings.deviceId || "";
  sel.length = 1;
  for (const d of inputs) sel.add(new Option(d.label, d.id));
  sel.value = inputs.some((d) => d.id === keep) ? keep : "";
}

async function fillChannels(n, keep) {
  const sel = $("channel");
  sel.textContent = "";
  sel.add(new Option(n > 2 ? "Channels 1 and 2 together" : "Both channels together", ""));
  for (let i = 0; i < n; i++) sel.add(new Option(n === 2 ? (i ? "Channel 2 (right)" : "Channel 1 (left)") : `Channel ${i + 1}`, String(i)));
  sel.value = keep != null && [...sel.options].some((o) => o.value === String(keep)) ? String(keep) : "";
  $("channelField").hidden = n < 2;
}

// Channels show once the browser may use the microphone (it can't count them before that).
async function checkChannels(keep) {
  if (!inputs.some((d) => d.named)) { $("channelField").hidden = true; return; }
  try {
    fillChannels(await channelsOf($("input").value), keep);
  } catch {
    $("channelField").hidden = true;
  }
}

function chosenChannel() {
  return $("channelField").hidden || $("channel").value === "" ? null : Number($("channel").value);
}

// ---------------------------------------------------------------------- display
function showDisplay(d) {
  if (!d) return;
  if (d.fontScale != null) { $("fontScale").value = d.fontScale; $("sizeOut").textContent = Math.round(d.fontScale * 100) + "%"; }
  if (d.visibleRows != null) $("visibleRows").value = String(d.visibleRows);
  for (const k of ["swap", "labels", "wakeLock", "showSrc"]) if (d[k] != null) $(k).checked = !!d[k];
  if (d.hidden != null) {
    $("hide").textContent = d.hidden ? "Show subtitles" : "Hide subtitles";
    $("hide").setAttribute("aria-pressed", String(!!d.hidden));
    $("hiddenChip").hidden = !d.hidden;
  }
}

function sendDisplay(patch) {
  hub.publish({ type: "display", display: patch });
  const { hidden, ...keep } = hub.display;
  save({ display: keep });
}

function fitPreview() {
  const box = $("preview"), frame = $("previewFrame");
  frame.style.transform = `scale(${box.clientWidth / 1920})`;
}

// ---------------------------------------------------------------------- keep the computer awake while live
let wake = null;
async function holdWake(on) {
  try {
    if (on && !wake && "wakeLock" in navigator && document.visibilityState === "visible") {
      wake = await navigator.wakeLock.request("screen");
      wake.addEventListener("release", () => { wake = null; });
    } else if (!on && wake) {
      const w = wake;
      wake = null;
      await w.release();
    }
  } catch { wake = null; }
}
document.addEventListener("visibilitychange", () => holdWake(running));

// ---------------------------------------------------------------------- models and speed
const cores = navigator.hardwareConcurrency || 4;

function threadPlan() {
  const forced = (params.get("threads") || "").split(",").map(Number).filter((n) => n > 0);
  if (forced.length) return { asr: forced[0], mt: forced[1] || forced[0] };
  if (!self.crossOriginIsolated) return { asr: 1, mt: 1 };       // no shared memory: one thread each
  // Speech is what people wait on: all but one core for it (up to 4), one for the translator
  // (two on 8 cores or more). Measured on a 4-core: 3+1 beat 2+1 on both delays.
  return { asr: Math.max(1, Math.min(4, cores - 1)), mt: cores >= 8 ? 2 : 1 };
}

async function hasGpu() {
  try { return !!(navigator.gpu && await navigator.gpu.requestAdapter()); } catch { return false; }
}

const DEVICE = ["wasm", "webgpu"].includes(params.get("device")) ? params.get("device") : "auto";

// For measuring only: ?dtype=fp32,q8 sets the speech model's precision on the processor (encoder, decoder).
function wasmDtype() {
  const p = (params.get("dtype") || "").split(",").filter((x) => /^(fp32|fp16|q8|int8|uint8|q4|bnb4)$/.test(x));
  return p.length ? { encoder_model: p[0], decoder_model_merged: p[1] || p[0] } : null;
}

// "Standard" is base.en: 1.1% word error on the test sermon and the only one that keeps up on a
// processor (small.en on a 4-core fell 25 s behind and dropped words). small.en is the operator's
// "More accurate" pick for a computer with a strong graphics card. {model, wasmModel}: wasmModel
// is used if the speech model ends up on the processor.
async function chooseModel() {
  const p = params.get("model") || "";
  const forced = SPEECH[p] ? p : SPEECH[p + ".en"] ? p + ".en" : null;
  if (forced) return { model: forced, wasmModel: forced };
  if ($("model").value === "accurate") return { model: "small.en", wasmModel: "small.en" };
  return { model: "base.en", wasmModel: "base.en" };
}

async function bytesToDownload(model, device) {
  const s = SPEECH[model];
  const [a, b, c] = await Promise.all([
    missingBytes(s.id, s.revision, speechFiles(model, device)),
    missingBytes(TRANSLATE.id, TRANSLATE.revision, TRANSLATE.files),
    missingBytes(VAD.id, VAD.revision, VAD.files),
  ]);
  return a + b + c;
}

// Room on this computer for what still has to download (browsers that can say).
async function roomFor(bytes) {
  if (!bytes || !navigator.storage || !navigator.storage.estimate) return true;
  try {
    const { quota, usage } = await navigator.storage.estimate();
    return !quota || quota - (usage || 0) > bytes * 1.1;
  } catch { return true; }
}

function glossary() {
  const hints = joinHints($("hints").value, noteNames);
  if ($("churchWords").checked) return { ...churchGlossary({ hints }), bible: true };
  return { hints, fix: {}, exact: {}, bible: false };
}

// ---------------------------------------------------------------------- start and stop
let startToken = 0;

async function startLive() {
  const go = ++startToken;
  const still = () => go === startToken && state === "loading";
  const src = $("src").value;
  const tgts = chosenTgts();
  sendTgts();
  hub.publish({ type: "status", state: "loading", detail: "Getting ready…" });
  const { model, wasmModel } = await chooseModel();
  const gpu = DEVICE !== "wasm" && await hasGpu();
  const need = await bytesToDownload(gpu ? model : wasmModel, gpu ? "webgpu" : "wasm");
  if (!still()) return;
  if (!(await roomFor(need))) {
    hub.publish({ type: "status", state: "error", detail: plainError(new Error("quota")) });
    return;
  }
  if (!still()) return;
  // The microphone opens now, while this click still counts as the operator's (browsers ask for
  // that), and the level meter moves while the models load.
  hub.publish({ type: "status", state: "loading", detail: "Opening the microphone…" });
  const m = mic = new MicSource({
    deviceId: $("input").value, channel: chosenChannel(), workletUrl: new URL("capture-worklet.js", HERE).href,
    onChunk: (c, lv) => { if (sink) sink(c, lv); else setLevel(lv.rms); },
    onState: (s, why) => micState?.(s, why),
  });
  try {
    await m.start();
  } catch (err) {
    if (mic === m) releaseMic();
    hub.publish({ type: "status", state: "error", detail: plainError(err) });
    return;
  }
  if (mic !== m || !still()) { m.stop().catch(() => {}); return; }   // stopped meanwhile
  const keepChannel = chosenChannel();
  fillInputs().then(() => { if (m.channels > 1) fillChannels(m.channels, keepChannel); }).catch(() => {});
  engine.start({
    loadingText: need > 0 ? "Getting ready. First time: about 1 GB downloads." : "Getting ready…",
    src, tgts, model, wasmModel, device: DEVICE, threads: threadPlan(), wasmDtype: wasmDtype(), glossary: glossary(), drafts: true,
    source: async (onChunk, onEnd, onState) => {
      if (mic !== m) throw new Error("Stopped.");
      sink = onChunk;
      micState = onState;
      feedStart = performance.now();
      return m;
    },
  });
}

// A recording played into the pipeline as if it were live: decoded silently, never to the speakers.
async function rehearse(bytes, { speed = 1, level = true } = {}) {
  if (running) return;
  const src = $("src").value;
  const tgts = chosenTgts();
  sendTgts();
  hub.publish({ type: "status", state: "loading", detail: "Reading the recording…" });
  let audio;
  try {
    audio = await decodeFile(bytes);
  } catch {
    hub.publish({ type: "status", state: "error", detail: "That recording couldn't be read. Try a .wav or .mp3 file." });
    return;
  }
  live.events = [];
  live.summary = null;
  live.notes = [];
  feedStart = 0;
  const { model, wasmModel } = await chooseModel();
  return engine.start({
    src, tgts, model, wasmModel, device: DEVICE, threads: threadPlan(), wasmDtype: wasmDtype(), glossary: glossary(), drafts: true, file: true,
    source: async (onChunk, onEnd) => {
      const fs = new FileSource({ audio, speed, onChunk, onEnd, level });
      feedStart = performance.now();
      await fs.start();
      return fs;
    },
  });
}

live.rehearseUrl = async (url, opts = {}) => {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return rehearse(await r.arrayBuffer(), opts);
};
live.stop = () => engine.stop();
live.info = () => ({ crossOriginIsolated: self.crossOriginIsolated, cores, threads: threadPlan(), device: DEVICE,
  model: engine.asr?.info?.model || null, ran: engine.asr?.info?.device || null, speechSec: engine.asr?.info?.sec || null,
  mtSec: engine.mt?.info?.sec || null });

// ---------------------------------------------------------------------- transcript (saved on this computer only)
function saveTranscript() {
  const names = Object.fromEntries(LANGS.map((l) => [l.code, l.name]));
  const out = [];
  for (const l of transcript) {
    out.push(l.src);
    for (const [code, text] of Object.entries(l.dst)) out.push(`  ${names[code] || code}: ${text}`);
    out.push("");
  }
  const blob = new Blob([out.join("\n")], { type: "text/plain;charset=utf-8" });
  const a = document.createElement("a");
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  a.href = URL.createObjectURL(blob);
  a.download = `Sermon subtitles ${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}.txt`;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 10000);
}

// ---------------------------------------------------------------------- wiring
function wire() {
  for (const id of ["src", "tgt", "tgt2"]) $(id).addEventListener("change", sendTgts);
  $("swapLangs").addEventListener("click", () => {
    const s = $("src"), t = $("tgt");
    if (![...s.options].some((o) => o.value === t.value)) {
      $("detail").textContent = "The browser version listens to English only.";
      return;
    }
    const a = s.value;
    s.value = t.value;
    t.value = a;
    sendTgts();
  });

  $("input").addEventListener("change", () => {
    save({ deviceId: $("input").value, channel: null });
    checkChannels(null);
  });
  $("channel").addEventListener("change", () => save({ channel: chosenChannel() }));
  navigator.mediaDevices?.addEventListener?.("devicechange", () => { if (!running) fillInputs().catch(() => {}); });

  $("fontScale").addEventListener("input", (e) => {
    const v = Number(e.target.value);
    $("sizeOut").textContent = Math.round(v * 100) + "%";
    sendDisplay({ fontScale: v });
  });
  $("visibleRows").addEventListener("change", (e) => sendDisplay({ visibleRows: Number(e.target.value) }));
  for (const k of ["swap", "labels", "wakeLock", "showSrc"]) $(k).addEventListener("change", (e) => sendDisplay({ [k]: e.target.checked }));
  $("hide").addEventListener("click", () => sendDisplay({ hidden: $("hide").getAttribute("aria-pressed") !== "true" }));
  $("clear").addEventListener("click", () => hub.publish({ type: "clear" }));
  $("openScreen").addEventListener("click", () => {
    const w = window.open(new URL("screen/screen.html", HERE).href, "riah-church-screen");
    $("detail").textContent = w ? "Drag that window to the projector, then press F." : "Pop-ups are blocked. Allow them for this page, then try again.";
  });

  let busyUntil = 0;
  $("startStop").addEventListener("click", async () => {
    if (Date.now() < busyUntil) return;     // a quick double-click is one press
    busyUntil = Date.now() + 1200;
    if (running) {
      startToken += 1;
      releaseMic();
      await engine.stop();
      if (state === "loading") hub.publish({ type: "status", state: "stopped", detail: "Stopped." });
    } else {
      startLive().catch((err) => hub.publish({ type: "status", state: "error", detail: plainError(err) }));
    }
  });

  $("saveHints").addEventListener("click", () => {
    save({ hints: $("hints").value });
    $("hintsSaved").textContent = running ? "Saved, used from the next Start." : "Saved.";
  });
  $("model").addEventListener("change", () => save({ model: $("model").value }));
  $("churchWords").addEventListener("change", () => save({ churchWords: $("churchWords").checked }));

  $("notesFile").addEventListener("change", async (e) => {
    const file = e.target.files && e.target.files[0];
    e.target.value = "";
    if (!file) return;
    try {
      const text = await readNotes(file);
      noteNames = await namesFrom(text, new URL("bible_words.json", HERE).href);
      $("notesSaid").textContent = noteNames.length
        ? `${noteNames.length} names found, used from the next Start.`
        : "No names found in those notes.";
      $("forgetNotes").hidden = !noteNames.length;
    } catch (err) {
      noteNames = [];
      $("notesSaid").textContent = err && err.message && /Word file/.test(err.message) ? err.message : "Couldn't read those. Try .docx, .txt or .md.";
      $("forgetNotes").hidden = true;
    }
  });
  $("forgetNotes").addEventListener("click", () => {
    noteNames = [];
    $("notesSaid").textContent = "Forgotten.";
    $("forgetNotes").hidden = true;
  });

  $("saveTranscript").addEventListener("click", saveTranscript);

  if (params.has("rehearse")) {
    $("rehearse").hidden = false;
    $("rehearseGo").addEventListener("click", async () => {
      const f = $("rehearseFile").files && $("rehearseFile").files[0];
      if (!f) { $("detail").textContent = "Pick a recording first."; return; }
      rehearse(await f.arrayBuffer(), { speed: Number($("rehearseSpeed").value) || 1 });
    });
  }

  addEventListener("beforeunload", (e) => {
    if (state === "listening") { e.preventDefault(); e.returnValue = ""; }
  });
  new ResizeObserver(fitPreview).observe($("preview"));
}

// One operator page at a time: two would both send to the church screen.
function oneTab() {
  if (!navigator.locks || !navigator.locks.request) return Promise.resolve(true);
  return new Promise((resolve) => {
    navigator.locks.request("riah-live-operator", { ifAvailable: true }, (lock) => {
      if (!lock) { resolve(false); return undefined; }
      resolve(true);
      return new Promise(() => {});     // held until this page closes
    }).catch(() => resolve(true));
  });
}

async function boot() {
  $("version").textContent = `Browser version · ${document.querySelector('meta[name="riah-version"]')?.content || "dev"}`;
  const missing = missingFeatures();
  if (missing.length) {
    $("unsupported").hidden = false;
    $("unsupportedWhat").textContent = `This browser can't run the live subtitles (it has no ${missing.join(", ")}).`;
    $("startStop").disabled = true;
    hub.publish({ type: "status", state: "error", detail: "Use Chrome or Edge on a computer." });
    return;
  }
  if (!(await oneTab())) {
    $("startStop").disabled = true;
    hub.publish({ type: "status", state: "error",
      detail: "Already open in another tab. Use that one, or close it and reload." });
    return;
  }
  fillLangs();
  $("hints").value = settings.hints || "";
  $("model").value = settings.model === "accurate" ? "accurate" : "";
  $("churchWords").checked = settings.churchWords !== false;
  showDisplay(hub.display);
  fitPreview();
  wire();
  await fillInputs();
  // A sound-board channel picked last time: count the channels again (opens the input for a moment).
  if (settings.channel != null && $("input").value) await checkChannels(settings.channel);
  const { model, wasmModel } = await chooseModel();
  const gpu = DEVICE !== "wasm" && await hasGpu();
  const need = await bytesToDownload(gpu ? model : wasmModel, gpu ? "webgpu" : "wasm").catch(() => 0);
  hub.publish({ type: "status", state: "stopped", detail: need > 0
    ? `Press Start. The first start downloads about ${Math.max(1, Math.round(need / 1e8) / 10)} GB into this browser.`
    : "Press Start." });
}

boot().catch((err) => hub.publish({ type: "status", state: "error", detail: plainError(err) }));
