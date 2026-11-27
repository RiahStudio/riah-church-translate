// Church screen: listens to the app and keeps the newest lines at the bottom, one column per language.
// A spoken line and its translations share one row, so they always sit level with each other.
// While a sentence is still being spoken, its translation already shows as a "draft" that grows and
// is then settled when the sentence ends.
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const stage = $("stage"), rowsEl = $("rows"), labelsEl = $("labels"), guidesEl = $("guides"), dot = $("linkDot");
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const NARROW = new Set(["zh", "zh-TW", "ja"]);   // dense scripts need less width for the same words
  // The public demo (riahstudio.com/churchtranslate) plays a recorded run into this screen itself,
  // so there is no app to listen to. tools/build_demo.py marks its copy with data-feed="demo".
  const DEMO = document.documentElement.dataset.feed === "demo";
  // ?layout=bar: a bar along the bottom of the church screen, over the slides. ?layout=side: a column on
  // the right. The app opens these as a small see-through window on top of the slideshow (app.py).
  const LAYOUT = new URLSearchParams(location.search).get("layout");
  if (LAYOUT === "bar" || LAYOUT === "side") {
    document.documentElement.dataset.layout = LAYOUT;
    // A window of its own must stay see-through: a dark color scheme would give it a dark backdrop.
    // (The operator's miniature is inside a dark page, so it keeps the dark scheme.)
    if (window.top === window) document.documentElement.style.colorScheme = "normal";
  }

  let names = {};          // code -> label shown above each column
  let langs = { src: "en", tgts: ["th"] };
  let display = { fontScale: 1, swap: false, labels: true, visibleRows: 3, wakeLock: false, showSrc: true, hidden: false };
  let cols = [];           // [{code, role: "src" | "dst"}] in screen order
  // What is on screen, kept so the columns can be rebuilt when the languages change.
  let lines = [];          // {id, seg, src, dst: {lang: text}, draft: {lang: text}}
  let partial = { text: "", seg: null, drafts: {} };
  let partialRow = null;   // the row for words still being spoken
  let wake = null;

  // ------------------------------------------------------------------ columns
  function columns() {
    const tgts = (langs.tgts || []).filter((t) => t !== langs.src);
    let out = tgts.map((code) => ({ code, role: "dst" }));
    if (display.showSrc !== false || !out.length) {
      const src = { code: langs.src, role: "src" };
      out = display.swap ? [...out, src] : [src, ...out];
    }
    return out;
  }

  function layout() {
    cols = columns();
    const tmpl = cols.map((c) => (NARROW.has(c.code) ? "0.85fr" : "1fr")).join(" ");
    const root = document.documentElement.style;
    root.setProperty("--cols", tmpl);
    root.setProperty("--n", String(cols.length));
    stage.dataset.cols = String(cols.length);
    labelsEl.textContent = "";
    guidesEl.textContent = "";
    for (const c of cols) {
      const lab = document.createElement("span");
      lab.className = "lab " + c.role;
      lab.lang = c.code;
      lab.textContent = label(c.code);
      labelsEl.append(lab);
      guidesEl.append(document.createElement("span"));
    }
  }

  // ------------------------------------------------------------------ rows
  function makeRow() {
    const row = document.createElement("div");
    row.className = "row";
    for (const c of cols) {
      const cell = document.createElement("div");
      cell.className = "cell " + c.role;
      cell.lang = c.code;
      cell.dataset.lang = c.code;
      if (c.role === "dst") cell.classList.add("waiting");
      row.append(cell);
    }
    return row;
  }

  function cells(row, role) {
    return [...row.children].filter((c) => c.classList.contains(role));
  }

  function cellFor(row, code) {
    return [...row.children].find((c) => c.classList.contains("dst") && c.dataset.lang === code) || null;
  }

  function setSrc(row, text) {
    for (const c of cells(row, "src")) c.textContent = text || "";
  }

  // A translation cell shows the final text, else the draft, else nothing yet. A final that is empty
  // stays empty: the line was the rest of a Bible verse already shown whole on the line before.
  function fillDst(row, dst, draft) {
    for (const cell of cells(row, "dst")) {
      const code = cell.dataset.lang;
      const settled = !!dst && typeof dst[code] === "string";
      const text = settled ? dst[code] : (draft && draft[code]) || "";
      cell.textContent = text;
      cell.classList.toggle("draft", !settled && !!text);
      cell.classList.toggle("waiting", !text);
    }
  }

  function updateEmpty(row) {
    row.classList.toggle("empty", ![...row.children].some((c) => c.textContent));
  }

  function ensurePartial() {
    if (!partialRow || !partialRow.isConnected) {
      partialRow = makeRow();
      partialRow.classList.add("partial");
      rowsEl.append(partialRow);
      setSrc(partialRow, partial.text);
      fillDst(partialRow, null, partial.drafts);
      updateEmpty(partialRow);
    }
    return partialRow;
  }

  function rowFor(line) {
    return rowsEl.querySelector(`.row.done[data-id="${CSS.escape(String(line.id))}"]`);
  }

  // FLIP: remember where rows were, change the DOM, then glide them from old to new spots.
  function glide(change) {
    if (reduceMotion) { change(); trim(); return; }
    const before = new Map();
    for (const r of rowsEl.children) {
      const box = r.getBoundingClientRect();
      before.set(r, box.height ? box.top : null);   // null: hidden (an empty partial row)
    }
    change();
    for (const r of rowsEl.children) {
      const was = before.get(r);
      if (was === undefined) continue;
      if (was === null) {
        // It was hidden, so it has no old spot to slide from. Fade it in where it belongs once the
        // rows above have glided out of its way (same 420 ms), so it never shows on top of them.
        if (r.getBoundingClientRect().height)
          r.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 260, delay: 400, easing: "ease-out", fill: "backwards" });
        continue;
      }
      const dy = was - r.getBoundingClientRect().top;
      if (Math.abs(dy) < 0.5) continue;
      r.animate([{ transform: `translateY(${dy}px)` }, { transform: "none" }],
        { duration: 420, easing: "cubic-bezier(.22,.8,.3,1)" });
    }
    trim();
  }

  // Keep a few finished rows (the oldest fades away at the top), drop the rest.
  function trim() {
    const keep = Math.max(1, Number(display.visibleRows) || 3) + 1;
    const done = [...rowsEl.querySelectorAll(".row.done")];
    for (const r of done.slice(0, Math.max(0, done.length - keep))) r.remove();
    if (lines.length > 40) lines = lines.slice(-20);
  }

  function addLine(ev) {
    const keep = ev.keepDraft && ev.seg != null && ev.seg === partial.seg ? { ...partial.drafts } : {};
    const line = { id: ev.id, seg: ev.seg, src: ev.src, dst: {}, draft: keep };
    if (typeof ev.dst === "string" && langs.tgts[0]) line.dst[langs.tgts[0]] = ev.dst;
    lines.push(line);
    partial = { text: "", seg: ev.seg == null ? null : ev.seg + 1, drafts: {} };
    glide(() => {
      const row = ensurePartial();          // the words already on screen become the finished line
      row.classList.remove("partial", "empty");
      row.classList.add("done");
      row.dataset.id = ev.id;
      setSrc(row, ev.src);
      fillDst(row, line.dst, line.draft);
      partialRow = null;
      ensurePartial();
    });
  }

  function setTranslation(ev) {
    const code = ev.lang || langs.tgts[0];   // older recordings had one language and no `lang`
    const line = lines.find((l) => l.id === ev.id);
    if (!line) return;
    line.dst[code] = ev.dst;
    const row = rowFor(line);
    if (row) glide(() => fillDst(row, line.dst, line.draft));
  }

  function setDraft(ev) {
    const line = ev.seg == null ? null : lines.find((l) => l.seg === ev.seg);
    if (line) {
      // The sentence ended while this draft was being made: it still belongs to that line.
      if (typeof line.dst[ev.lang] === "string") return;
      line.draft[ev.lang] = ev.text;
      const row = rowFor(line);
      if (row) glide(() => fillDst(row, line.dst, line.draft));
      return;
    }
    if (partial.seg != null && ev.seg !== partial.seg) return;
    partial.seg = ev.seg;
    partial.drafts[ev.lang] = ev.text;
    glide(() => {
      const row = ensurePartial();
      fillDst(row, null, partial.drafts);
      updateEmpty(row);
    });
  }

  function setPartial(ev) {
    if (ev.seg != null && ev.seg !== partial.seg) partial.drafts = {};
    if (ev.seg !== undefined) partial.seg = ev.seg;
    partial.text = ev.text || "";
    glide(() => {
      const row = ensurePartial();
      setSrc(row, partial.text);
      fillDst(row, null, partial.drafts);
      updateEmpty(row);
    });
  }

  function renderAll() {
    layout();
    rowsEl.textContent = "";
    partialRow = null;
    for (const l of lines) {
      const row = makeRow();
      row.classList.add("done");
      row.dataset.id = l.id;
      setSrc(row, l.src);
      fillDst(row, l.dst, l.draft);
      rowsEl.append(row);
    }
    ensurePartial();
    trim();
  }

  function loadSnapshot(snap) {
    lines = (snap.lines || []).map((l) => ({
      id: l.id, seg: l.seg, src: l.src,
      dst: typeof l.dst === "string" ? { [langs.tgts[0]]: l.dst } : { ...(l.dst || {}) },
      draft: { ...(l.draft || {}) },
    }));
    partial = { text: snap.partial || "", seg: snap.seg == null ? null : snap.seg, drafts: { ...(snap.drafts || {}) } };
    renderAll();
  }

  // ------------------------------------------------------------------ look
  function applyDisplay(d) {
    const before = `${display.swap}|${display.showSrc}`;
    const rowsBefore = Number(display.visibleRows) || 3;
    Object.assign(display, d || {});
    document.documentElement.style.setProperty("--scale", String(Number(display.fontScale) || 1));
    stage.classList.toggle("no-labels", !display.labels);
    stage.classList.toggle("is-hidden", !!display.hidden);
    setWake(!!display.wakeLock);
    // More lines asked for: rebuild, since trim() already removed the older rows.
    if (`${display.swap}|${display.showSrc}` !== before || (Number(display.visibleRows) || 3) > rowsBefore) renderAll();
    else trim();
  }

  function label(code) {
    const n = names[code];
    if (!n) return code;
    return n.native && n.native !== n.name ? `${n.native} · ${n.name}` : n.name;
  }

  function applyLangs(l) {
    if (l) {
      const tgts = l.tgts || (l.tgt ? [l.tgt] : []);
      const changed = l.src !== langs.src || tgts.join() !== (langs.tgts || []).join();
      langs = { src: l.src, tgts };
      if (changed) partial.drafts = {};
    }
    renderAll();
  }

  // Off unless the operator turns it on: keeps the projector laptop's display from sleeping mid-sermon.
  async function setWake(on) {
    try {
      if (on && !wake && "wakeLock" in navigator && document.visibilityState === "visible") {
        wake = await navigator.wakeLock.request("screen");
        wake.addEventListener("release", () => { wake = null; });
      } else if (!on && wake) {
        await wake.release();
        wake = null;
      }
    } catch (_) { wake = null; }
  }
  document.addEventListener("visibilitychange", () => setWake(!!display.wakeLock));

  // ------------------------------------------------------------------ live feed
  function handle(ev) {
    switch (ev.type) {
      case "snapshot":
        Object.assign(display, ev.display || {});
        if (ev.langs) langs = { src: ev.langs.src, tgts: ev.langs.tgts || (ev.langs.tgt ? [ev.langs.tgt] : []) };
        applyDisplay({});
        loadSnapshot(ev);
        break;
      case "line": addLine(ev); break;
      case "translation": setTranslation(ev); break;
      case "draft": setDraft(ev); break;
      case "partial": setPartial(ev); break;
      case "display": applyDisplay(ev.display); break;
      case "langs": applyLangs(ev); break;
      case "clear":
        lines = [];
        partial = { text: "", seg: partial.seg, drafts: {} };
        renderAll();
        break;
      case "status": live = LIVE.has(ev.state); break;
    }
    if (ev.type === "snapshot" && ev.status) live = LIVE.has(ev.status.state);
    standby();
  }

  // The Riah card shows only while the app is stopped and nothing is on screen.
  const LIVE = new Set(["listening", "loading", "reconnecting"]);
  let live = DEMO;      // the demo plays a recording, so it never shows the stopped card
  function standby() {
    const empty = !rowsEl.querySelector(".row.done") && !rowsEl.querySelector(".row.partial:not(.empty)");
    stage.classList.toggle("standby-on", !live && empty);
    stage.classList.toggle("has-words", !empty);   // the bar's dark band shows only behind words
  }

  function connect() {
    const es = new EventSource("/events");
    es.onopen = () => dot.classList.remove("on");
    es.onerror = () => dot.classList.add("on");   // the browser retries on its own
    es.onmessage = (m) => {
      try { handle(JSON.parse(m.data)); } catch (e) { console.warn("bad event", e); }
    };
  }

  function setNames(list) {
    for (const l of list) names[l.code] = l;
    renderAll();
  }
  if (!DEMO) fetch("/api/langs").then((r) => r.json()).then(setNames).catch(() => {});

  // ------------------------------------------------------------------ screen controls
  function toggleFullscreen() {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else document.documentElement.requestFullscreen().catch(() => {});
  }
  addEventListener("keydown", (e) => {
    if (e.key === "f" || e.key === "F") toggleFullscreen();
  });
  addEventListener("dblclick", toggleFullscreen);

  let idleTimer = 0;
  function wakeCursor() {
    document.body.classList.remove("idle");
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => document.body.classList.add("idle"), 2500);
  }
  addEventListener("mousemove", wakeCursor);
  wakeCursor();

  renderAll();
  standby();
  window.riahScreen = { handle, setNames };   // lets tests and the demo drive the screen without a live feed
  if (!DEMO) connect();
})();
