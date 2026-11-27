// The public demo: plays a recorded run (run.json, made by tools/build_demo.py) into the real
// church screen (screen/screen.html in the frame), with its real timing, over and over.
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const frame = $("screen"), player = $("player"), fullBtn = $("full"), exitBtn = $("exit");
  const NAMES = [
    { code: "en", name: "English", native: "English" },
    { code: "th", name: "Thai", native: "ไทย" },
    { code: "zh", name: "Chinese", native: "中文" },
  ];
  const on = { en: true, th: true, zh: false };
  let run = null, screen = null, timer = 0, t0 = 0, next = 0, hiddenAt = 0;

  const send = (ev) => { if (screen) screen.handle(ev); };

  function showLangs() {
    send({ type: "langs", src: "en", tgts: ["th", "zh"].filter((c) => on[c]) });
    send({ type: "display", display: { showSrc: on.en } });
  }

  function start() {
    clearTimeout(timer);
    send({ type: "clear" });
    showLangs();
    send({ type: "status", state: "listening" });
    t0 = performance.now();
    next = 0;
    tick();
  }

  function tick() {
    const now = (performance.now() - t0) / 1000;
    const evs = run.events;
    while (next < evs.length && evs[next].at <= now) send(evs[next++]);
    if (next < evs.length) {
      timer = setTimeout(tick, Math.max(15, (evs[next].at - now) * 1000));
    } else {
      send({ type: "partial", text: "", seg: null });
      timer = setTimeout(start, 6000);     // a pause on the last lines, then from the top
    }
  }

  // A hidden tab: hold the clock, carry on where it was when the tab comes back.
  document.addEventListener("visibilitychange", () => {
    if (!run || !screen) return;
    if (document.hidden) {
      hiddenAt = performance.now();
      clearTimeout(timer);
    } else if (hiddenAt) {
      t0 += performance.now() - hiddenAt;
      hiddenAt = 0;
      tick();
    }
  });

  // ---------------------------------------------------------------- language buttons
  for (const b of document.querySelectorAll("[data-lang]")) {
    b.addEventListener("click", () => {
      const code = b.dataset.lang;
      const want = !on[code];
      // At least one translation stays on.
      if (!want && code !== "en" && !["th", "zh"].some((c) => c !== code && on[c])) return;
      on[code] = want;
      b.setAttribute("aria-pressed", String(want));
      showLangs();
    });
  }
  $("restart").addEventListener("click", () => { if (run && screen) start(); });

  // ---------------------------------------------------------------- full screen
  // The real Full Screen where the browser allows it on a frame; otherwise (iPhone) the frame fills
  // the window with an Exit button.
  // Everything outside the player goes inert while it fills the window, so Tab stays on the screen.
  function outside() {
    const out = [];
    for (let el = player; el && el !== document.body; el = el.parentElement) {
      for (const sib of el.parentElement.children) if (sib !== el && sib.tagName !== "SCRIPT") out.push(sib);
    }
    return out;
  }
  function fill(onOff) {
    player.classList.toggle("is-filled", onOff);
    for (const el of outside()) el.inert = onOff;
    for (const el of player.children) if (!el.classList.contains("frame")) el.inert = onOff;
    document.body.classList.toggle("no-scroll", onOff);
    if (onOff) exitBtn.focus(); else fullBtn.focus();
  }
  fullBtn.addEventListener("click", () => {
    if (frame.requestFullscreen && document.fullscreenEnabled) frame.requestFullscreen().catch(() => fill(true));
    else fill(true);
  });
  exitBtn.addEventListener("click", () => fill(false));
  const onKey = (e) => { if (e.key === "Escape" && player.classList.contains("is-filled")) fill(false); };
  addEventListener("keydown", onKey);

  // ---------------------------------------------------------------- go
  function ready() {
    screen = frame.contentWindow && frame.contentWindow.riahScreen;
    if (!screen || !run) return;
    frame.contentWindow.addEventListener("keydown", onKey);   // Esc with the focus inside the screen
    screen.setNames(NAMES);
    start();
  }
  frame.addEventListener("load", ready);
  fetch("run.json").then((r) => r.json()).then((data) => {
    run = data;
    if (frame.contentDocument && frame.contentDocument.readyState === "complete") ready();
  }).catch(() => { $("caption").textContent = "The recording didn't load. Reload the page to try again."; });
  window.riahDemo = { state: () => ({ on: { ...on }, next, total: run ? run.events.length : 0 }) };
})();
