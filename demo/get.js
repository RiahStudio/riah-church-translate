// The download button: picks the right file for the computer that opened the page, so nobody has to
// know their chip. `pick` is a plain function (tested in tools/get_check.mjs); the rest wires it up.
(() => {
  "use strict";
  const BASE = "https://github.com/RiahStudio/riah-church-translate/releases/latest/download/";
  const FILES = {
    windows: BASE + "Riah-Church-Translate-windows.zip",
    mac: BASE + "Riah-Church-Translate-mac.zip",
    macIntel: BASE + "Riah-Church-Translate-mac-intel.zip",
  };

  // env: { ua, platform (the browser's own platform hint), touch (max touch points),
  //        arch ("arm" | "x86" | "" from the browser), gpu (graphics name, "" if hidden) }
  function pick(env) {
    const ua = String(env.ua || "").toLowerCase();
    const plat = String(env.platform || "").toLowerCase();
    const touch = Number(env.touch || 0);
    const isMac = /mac/.test(plat) || /macintosh|mac os x/.test(ua);
    // Phones and tablets (an iPad says it is a Mac, but has a touch screen) can't run the app.
    if (/iphone|ipad|ipod|android|cros|mobile/.test(ua) || (isMac && touch > 1)) return { os: "other" };
    if (/win/.test(plat) || /windows/.test(ua)) return { os: "windows", file: FILES.windows };
    if (isMac) {
      const arch = String(env.arch || "").toLowerCase();
      const gpu = String(env.gpu || "").toLowerCase();
      let chip = "unknown";
      if (arch === "arm" || /apple m\d/.test(gpu)) chip = "apple";
      else if (arch === "x86" || /intel|amd|radeon/.test(gpu)) chip = "intel";
      // When the browser won't say (Safari hides it), the Apple chip is right for any Mac from 2020 on.
      return { os: "mac", chip, file: chip === "intel" ? FILES.macIntel : FILES.mac, alt: FILES.macIntel };
    }
    return { os: "other" };
  }

  if (typeof module !== "undefined" && module.exports) { module.exports = { pick, FILES }; return; }

  const $ = (id) => document.getElementById(id);
  const main = $("get-main"), note = $("get-note"), other = $("get-other");
  if (!main || !note) return;

  function gpuName() {
    try {
      const gl = document.createElement("canvas").getContext("webgl");
      const ext = gl && gl.getExtension("WEBGL_debug_renderer_info");
      return ext ? String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) || "") : "";
    } catch (e) { return ""; }
  }

  async function chipHint() {
    try {
      const h = await navigator.userAgentData.getHighEntropyValues(["architecture"]);
      return h.architecture || "";
    } catch (e) { return ""; }
  }

  async function show() {
    const uad = navigator.userAgentData;
    const r = pick({
      ua: navigator.userAgent,
      platform: (uad && uad.platform) || navigator.platform,
      touch: navigator.maxTouchPoints,
      arch: await chipHint(),
      gpu: gpuName(),
    });
    note.replaceChildren();
    const say = (text) => note.append(document.createTextNode(text));
    const link = (href, text) => { const a = document.createElement("a"); a.href = href; a.textContent = text; note.append(a); };
    if (r.os === "other") {
      main.textContent = "Try it in your browser";
      main.href = "/churchtranslate/live/";
      say("The app is for the church's Mac or Windows PC. Open this page on that computer to download it.");
    } else if (r.os === "windows") {
      main.textContent = "Download for Windows";
      main.href = r.file;
      say("Free. Windows 10 or 11, about 100 MB. ");
    } else {
      main.textContent = "Download for Mac";
      main.href = r.file;
      if (r.chip === "intel") say("Free. For this Intel Mac, about 100 MB. ");
      else if (r.chip === "apple") say("Free. For a Mac with an Apple chip, about 70 MB. ");
      else {
        say("Free. For a Mac with an Apple chip (2020 or newer). Older Intel Mac? ");
        link(r.alt, "Download that one");
        say(". ");
      }
    }
    if (r.os !== "other") {
      say("It is not signed yet, so the first open asks you to confirm: ");
      link("https://github.com/RiahStudio/riah-church-translate/blob/main/SUNDAY-CHECKLIST.md", "the steps are here");
      say(".");
    }
    main.hidden = false;
    if (other) { other.open = false; other.querySelector("summary").textContent = "Another computer"; }
  }
  show();
})();
