// The browser version's clip test: plays a recording into the real page in headless Chromium (as a
// "rehearsal", decoded silently, never played out loud) and measures the same things as
// tests/clip_test.py: word error rate, how long after a line ends the words are on screen, when
// each translation first shows (draft) and when it settles. It runs the models, so run it on the
// a fast test machine, not the church computer.
//
//   python3 tools/build_browser.py /tmp/rct-live
//   node tools/browser_clip_test.mjs run /tmp/rct-live --clip tests/clips/sermon-en.wav \
//        --script tests/clips/sermon-en.txt --model base --device wasm --to th \
//        --pw ~/cowork/scratch/node_modules/playwright-core --chrome <chromium> --profile ~/.cache/rct-profile
//
// Or just serve a built folder with the headers the site sends (for looking at the page):
//
//   node tools/browser_clip_test.mjs serve /tmp/rct-live --port 8765

import { createServer } from "node:http";
import { readFile, stat, writeFile } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".json": "application/json",
  ".svg": "image/svg+xml", ".woff2": "font/woff2", ".wav": "audio/wav", ".mp3": "audio/mpeg",
  ".txt": "text/plain; charset=utf-8", ".png": "image/png" };

function args(argv) {
  const a = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i].startsWith("--")) {
      const k = argv[i].slice(2);
      a[k] = argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[++i] : true;
    } else a._.push(argv[i]);
  }
  return a;
}

// The page's response headers, straight from tools/build_browser.py so the two never drift.
function siteHeaders() {
  for (const py of process.platform === "win32" ? ["python", "py"] : ["python3", "python"]) {
    try {
      return JSON.parse(execFileSync(py, [path.join(ROOT, "tools", "build_browser.py"), "--headers"], { encoding: "utf8" }));
    } catch { /* try the next name */ }
  }
  throw new Error("python is needed to read the headers from tools/build_browser.py");
}

// A static server for the built folder, mounted at /churchtranslate/live/ like the site, plus
// /clips/ for the recording (same origin, so the page may fetch it).
function serve(dir, clipsDir, port, headers) {
  const base = "/churchtranslate/live/";
  const server = createServer(async (req, res) => {
    const url = new URL(req.url, "http://x");
    let file = null;
    if (url.pathname === "/" || url.pathname === "/churchtranslate/live") {
      res.writeHead(302, { Location: base + url.search });
      return res.end();
    }
    if (url.pathname.startsWith(base)) file = path.join(dir, decodeURIComponent(url.pathname.slice(base.length)) || "index.html");
    else if (clipsDir && url.pathname.startsWith("/clips/")) file = path.join(clipsDir, path.basename(decodeURIComponent(url.pathname)));
    if (file && file.endsWith(path.sep)) file = path.join(file, "index.html");
    try {
      if (!file || !(await stat(file)).isFile()) throw new Error("missing");
      const body = await readFile(file);
      res.writeHead(200, { ...headers, "Content-Type": TYPES[path.extname(file).toLowerCase()] || "application/octet-stream",
        "Content-Length": body.length, "Cache-Control": "no-cache" });
      res.end(body);
    } catch {
      res.writeHead(404, { ...headers, "Content-Type": "text/plain" });
      res.end("not found");
    }
  });
  return new Promise((resolve) => server.listen(port, "127.0.0.1", () => resolve(server)));
}

// ------------------------------------------------------------------------ the measurements
// Same definitions as tests/clip_test.py.
const words = (t) => t.toLowerCase().replaceAll("-", " ").replace(/[^a-z0-9' ]+/g, " ").split(/\s+/).filter(Boolean);
function wer(ref, hyp) {
  const r = words(ref), h = words(hyp);
  const d = Array.from({ length: h.length + 1 }, (_, j) => j);
  for (let i = 1; i <= r.length; i++) {
    let prev = d[0];
    d[0] = i;
    for (let j = 1; j <= h.length; j++) {
      const cur = d[j];
      d[j] = Math.min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] !== h[j - 1] ? 1 : 0));
      prev = cur;
    }
  }
  return d[h.length] / Math.max(1, r.length);
}
const med = (xs) => {
  const s = xs.filter((x) => x != null).sort((a, b) => a - b);
  return s.length ? { median: +s[s.length >> 1].toFixed(2), worst: +s[s.length - 1].toFixed(2), n: s.length } : null;
};

function measure(ev, tgts, ref) {
  const lines = new Map(ev.filter((e) => e.type === "line").map((e) => [e.id, e]));
  const trans = new Map(ev.filter((e) => e.type === "translation").map((e) => [`${e.id}|${e.lang || tgts[0]}`, e]));
  const firstDraft = new Map();
  for (const e of ev) {
    if (e.type !== "draft") continue;
    const k = `${e.seg}|${e.lang}`;
    firstDraft.set(k, Math.min(firstDraft.get(k) ?? e.at, e.at));
  }
  const said = [...lines.values()].map((l) => l.src).join(" ");
  const out = { lines: lines.size, said, wer: ref ? +wer(ref, said).toFixed(4) : null,
    onScreen: med([...lines.values()].map((l) => l.lag)), targets: {} };
  for (const t of tgts) {
    const starts = [], settled = [];
    let missing = 0;
    for (const [id, l] of lines) {
      const g = trans.get(`${id}|${t}`);
      if (!g || !String(g.dst || "").trim()) { missing++; continue; }
      const first = Math.min(firstDraft.get(`${l.seg}|${t}`) ?? g.at, g.at);
      starts.push(first - l.start);
      settled.push(g.lag);
    }
    out.targets[t] = { firstShows: med(starts), settled: med(settled), missing,
      draftsKept: [...lines.values()].filter((l) => l.keepDraft).length };
  }
  return out;
}

// ------------------------------------------------------------------------ one run in Chromium
async function run(a) {
  const dir = path.resolve(a._[1]);
  const clip = path.resolve(a.clip || path.join(ROOT, "tests/clips/sermon-en.wav"));
  const script = a.script === undefined ? clip.replace(/\.wav$/, ".txt") : a.script;
  const ref = script ? await readFile(script, "utf8").catch(() => null) : null;
  const tgts = String(a.to || "th").split(",");
  const pw = a.pw ? createRequire(path.resolve(a.pw, "package.json"))(path.resolve(a.pw)) : await import("playwright-core");
  const chromium = pw.chromium || pw.default?.chromium;
  const port = Number(a.port || 8770);
  const server = await serve(dir, path.dirname(clip), port, siteHeaders());

  const flags = ["--autoplay-policy=user-gesture-required", "--mute-audio"];
  if (a.device === "webgpu" || a.webgpu) flags.push("--enable-unsafe-webgpu", "--enable-features=Vulkan", "--use-angle=vulkan", "--ignore-gpu-blocklist");
  if (a["fake-mic"]) flags.push("--use-fake-device-for-media-stream", `--use-file-for-fake-audio-capture=${clip}`, "--use-fake-ui-for-media-stream");
  const ctx = await chromium.launchPersistentContext(path.resolve(a.profile || path.join(ROOT, ".browser-profile")), {
    executablePath: a.chrome || undefined, headless: true, args: flags, viewport: { width: 1280, height: 900 } });
  const hosts = new Map(), csp = [], errors = [];
  let downloaded = 0;
  ctx.on("request", (r) => { const h = new URL(r.url()).host; hosts.set(h, (hosts.get(h) || 0) + 1); });
  ctx.on("response", async (r) => {
    if (!/huggingface|hf\.co|jsdelivr/.test(new URL(r.url()).host) || r.status() !== 200) return;
    downloaded += Number(r.headers()["content-length"] || 0);
  });
  await ctx.addInitScript(() => {
    document.addEventListener("securitypolicyviolation", (e) => {
      (window.__csp ||= []).push(`${e.violatedDirective} ${e.blockedURI} ${e.sourceFile || ""}:${e.lineNumber || ""}`);
    });
    // Never let a page make a sound, even by accident.
    const mute = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function () { this.muted = true; this.volume = 0; return mute.call(this); };
  });
  const page = ctx.pages()[0] || await ctx.newPage();
  page.on("console", (m) => {
    const t = m.text();
    if (/Content Security Policy|Refused to/.test(t)) csp.push(t);
    else if (m.type() === "error") errors.push(t);
  });
  page.on("pageerror", (e) => errors.push(String(e)));

  const q = new URLSearchParams({ rehearse: "1" });
  if (a.model) q.set("model", a.model);
  if (a.device) q.set("device", a.device);
  if (a.threads) q.set("threads", a.threads);
  if (a.dtype) q.set("dtype", a.dtype);
  const t0 = Date.now();
  await page.goto(`http://127.0.0.1:${port}/churchtranslate/live/?${q}`);
  await page.waitForFunction(() => window.riahLive && document.querySelector("#tgt option"));
  const gpu = await page.evaluate(async () => {
    try { return !!(navigator.gpu && await navigator.gpu.requestAdapter()); } catch { return false; }
  });
  await page.evaluate((t) => {
    const sel = document.getElementById("tgt");
    sel.value = t[0];
    sel.dispatchEvent(new Event("change"));
    const also = document.getElementById("tgt2");
    also.value = t[1] || "";
    also.dispatchEvent(new Event("change"));
  }, tgts);

  let result;
  if (a["fake-mic"]) {
    await page.click("#startStop");
    await page.waitForFunction(() => window.riahLive.state() === "running", null, { timeout: 30 * 60e3 });
    await page.waitForTimeout(Number(a.seconds || 90) * 1000);
    await page.click("#startStop");
    await page.waitForFunction(() => window.riahLive.summary, null, { timeout: 5 * 60e3 }).catch(() => {});
  } else {
    await page.evaluate((url) => { window.riahLive.rehearseUrl(url, { speed: 1 }); }, `/clips/${path.basename(clip)}`);
    await page.waitForFunction(() => window.riahLive.summary, null, { timeout: 60 * 60e3, polling: 1000 });
  }
  const got = await page.evaluate(() => ({ events: window.riahLive.events, summary: window.riahLive.summary,
    notes: window.riahLive.notes, info: window.riahLive.info(), csp: window.__csp || [],
    frameCsp: [...document.querySelectorAll("iframe")].flatMap((f) => { try { return f.contentWindow.__csp || []; } catch { return []; } }),
    screenRows: (() => { try { return document.getElementById("previewFrame").contentDocument.body.innerText.slice(0, 400); } catch { return null; } })() }));
  result = { when: new Date().toISOString(), wallSec: Math.round((Date.now() - t0) / 1000), gpuAdapter: gpu,
    browser: `${chromium.name()} ${ctx.browser()?.version() || ""}`, clip: path.basename(clip),
    ...measure(got.events, tgts, ref), summary: got.summary, info: got.info,
    downloadedMB: +(downloaded / 1e6).toFixed(1), hosts: Object.fromEntries(hosts),
    csp: [...csp, ...got.csp, ...got.frameCsp], errors: errors.slice(0, 20), notes: got.notes.slice(-20),
    screenSample: got.screenRows, events: got.events.filter((e) => e.type !== "partial") };
  await ctx.close();
  server.close();

  const show = (m) => (m ? `median ${m.median}s, worst ${m.worst}s` : "none");
  console.log(`${result.lines} lines; model ${result.summary?.model} on ${result.summary?.device}; ` +
    `Whisper x${result.summary?.realtimeFactor} of real time; threads ${JSON.stringify(result.info?.threads)}`);
  console.log(`speech passes ${result.summary?.passes}; seconds per stage ${JSON.stringify(result.summary?.asrParts)}; ` +
    `translator ${result.summary?.finalSecPerLang}s per line per language`);
  if (result.wer != null) console.log(`word error rate vs script: ${(result.wer * 100).toFixed(1)}%`);
  console.log(`original on screen: ${show(result.onScreen)} after the line ended`);
  for (const t of tgts) {
    const x = result.targets[t];
    console.log(`${t}: first shows ${show(x.firstShows)} after the line STARTED; settled ${show(x.settled)} after it ended; ` +
      `missing ${x.missing}; drafts kept on ${x.draftsKept}/${result.lines}`);
  }
  console.log(`downloaded ${result.downloadedMB} MB this run; hosts: ${Object.keys(result.hosts).join(", ")}`);
  console.log(`CSP violations: ${result.csp.length}; page errors: ${result.errors.length}`);
  if (a.out) await writeFile(a.out, JSON.stringify(result, null, 1));
  return result;
}

const a = args(process.argv.slice(2));
if (a._[0] === "serve" && a._[1]) {
  const port = Number(a.port || 8765);
  await serve(path.resolve(a._[1]), a.clips ? path.resolve(a.clips) : path.join(ROOT, "tests", "clips"), port, siteHeaders());
  console.log(`serving http://127.0.0.1:${port}/churchtranslate/live/`);
} else if (a._[0] === "run" && a._[1]) {
  await run(a);
} else {
  console.log("usage: node tools/browser_clip_test.mjs serve <built dir> [--port 8765]\n" +
    "       node tools/browser_clip_test.mjs run <built dir> --clip <wav> [--script <txt>] [--model base|small] " +
    "[--device wasm|webgpu|auto] [--threads 2,1] [--to th,zh] [--pw <playwright-core dir>] [--chrome <path>] " +
    "[--profile <dir>] [--out result.json] [--fake-mic --seconds 90]");
  process.exit(2);
}
