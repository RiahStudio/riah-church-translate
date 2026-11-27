// The church screen's feed in the browser version: the operator page (same computer, same browser)
// sends every change over a BroadcastChannel, and this hands it to the app's own screen.js through
// window.riahScreen. Nothing goes over the network. If the operator page goes quiet (closed,
// reloading, asleep) the small dot in the corner shows until it is back.
(() => {
  "use strict";
  const CHANNEL = "riah-live";
  const QUIET_MS = 6000;
  const scr = window.riahScreen;
  const dot = document.getElementById("linkDot");
  if (!scr || typeof BroadcastChannel === "undefined") return;

  scr.handle({ type: "status", state: "stopped" });      // the Riah card until the operator page answers

  let ch = null;
  let heardAt = 0;
  let asked = 0;

  function open() {
    try { ch?.close(); } catch (_) { /* already closed */ }
    ch = new BroadcastChannel(CHANNEL);
    ch.onmessage = (m) => heard(m.data);
    hello();
  }

  function hello() {
    asked = Date.now();
    try { ch.postMessage({ type: "hello" }); } catch (_) { open(); }
  }

  function heard(m) {
    if (!m || typeof m !== "object") return;
    heardAt = Date.now();
    dot?.classList.remove("on");
    if (m.type === "beat" || m.type === "hello") return;
    if (m.type === "names") {
      if (Array.isArray(m.names)) scr.setNames(m.names);
      return;
    }
    try { scr.handle(m); } catch (e) { console.warn("bad event", e); }
  }

  setInterval(() => {
    const now = Date.now();
    if (now - heardAt > QUIET_MS) {
      dot?.classList.add("on");
      if (now - asked > 3000) hello();       // ask for everything again once the page is back
    }
  }, 1000);

  open();
})();
