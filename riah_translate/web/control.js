// Operator window: start/stop, languages, input, and how the church screen looks.
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  // The bar and column take this much of the church screen (app.py BAR, SIDE).
  const BAR = 0.28, SIDE = 0.30;
  const STATUS = {
    stopped: ["", "Stopped"],
    loading: ["riah-status--revise", "Getting ready"],
    listening: ["riah-status--keep", "Live"],
    // Something broke mid-service (the sound input went away): the app is putting itself back together.
    reconnecting: ["riah-status--revise", "Reconnecting"],
    error: ["riah-status--discard", "Problem"],
  };
  let running = false;
  let langs = [];
  let settings = {};
  let state = "stopped";

  async function post(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}) });
    const out = await r.json().catch(() => ({}));
    if (!r.ok || out.error) throw new Error(out.error || `HTTP ${r.status}`);
    return out;
  }

  // ------------------------------------------------------------------ status
  function setStatus(s, detail) {
    state = s;
    const [cls, text] = STATUS[s] || STATUS.stopped;
    const chip = $("status");
    chip.className = "riah-status " + cls;
    chip.textContent = text;
    if (detail !== undefined) $("detail").textContent = detail;
    running = s === "loading" || s === "listening" || s === "reconnecting";
    $("startStop").textContent = running ? "Stop" : "Start";
    $("restart").hidden = !(s === "listening" || s === "reconnecting");
    for (const id of ["src", "input", "channel", "swapLangs", "mt", "model", "churchWords"]) $(id).disabled = running;
    if (!running) setLevel(0);
  }

  // Just the level. The app evens out a quiet or loud feed on its own, so there is nothing to fix here.
  function setLevel(rms) {
    const pct = Math.min(100, Math.round(Math.sqrt(Math.max(0, rms) / 0.25) * 100));
    $("meterFill").style.width = pct + "%";
    $("meter").setAttribute("aria-valuenow", String(pct));
  }

  // Pick one channel of a sound board (its own USB input, or one side of a stereo cable).
  let devices = [];
  function fillChannels(keep) {
    const v = $("input").value;
    const d = devices.find((x) => String(x.id) === v) || (v === "" ? devices.find((x) => x.default) : null);
    const n = d ? d.channels : 0;
    const sel = $("channel");
    sel.textContent = "";
    sel.add(new Option(n > 2 ? "Channels 1 and 2 together" : "Both channels together", ""));
    for (let i = 0; i < n; i++)
      sel.add(new Option(n === 2 ? (i ? "Channel 2 (right)" : "Channel 1 (left)") : `Channel ${i + 1}`, String(i)));
    sel.value = keep != null && [...sel.options].some((o) => o.value === String(keep)) ? String(keep) : "";
    $("channelField").hidden = n < 2;
  }
  function chosenChannel() {
    return $("channelField").hidden || $("channel").value === "" ? null : Number($("channel").value);
  }

  function setLag(stats) {
    if (!stats || stats.lag == null) { $("lag").textContent = ""; return; }
    let t = `Words reach the screen ${stats.lag.toFixed(1)} s after they are said`;
    if (stats.mtLag != null) t += `, the translation ${stats.mtLag.toFixed(1)} s.`;
    else t += ".";
    $("lag").textContent = t;
  }

  // ------------------------------------------------------------------ languages
  function fillLangs() {
    const src = $("src"), tgt = $("tgt"), tgt2 = $("tgt2");
    src.textContent = tgt.textContent = "";
    tgt2.length = 1;
    for (const l of langs) {
      const label = l.native && l.native !== l.name ? `${l.name} · ${l.native}` : l.name;
      if (l.can_listen) src.add(new Option(label, l.code));
      tgt.add(new Option(label, l.code));
      tgt2.add(new Option(label, l.code));
    }
    const tgts = settings.tgts || (settings.tgt ? [settings.tgt] : ["th"]);
    src.value = settings.src || "en";
    tgt.value = tgts[0] || "th";
    tgt2.value = tgts[1] || "";
  }

  function chosenTgts() {
    const out = [];
    for (const v of [$("tgt").value, $("tgt2").value]) if (v && v !== $("src").value && !out.includes(v)) out.push(v);
    return out;
  }

  // Translations can change mid-service; the screen follows at once.
  // ---------------------------------------------------------------- spoken translation
  let outputs = [], voices = {}, voicesAvailable = false;

  function voicePlan() {
    const plan = {};
    const saved = (settings && settings.voice) || {};
    for (const lang of chosenTgts()) {
      const box = $("vOn-" + lang), dev = $("vDev-" + lang), vx = $("vVoice-" + lang);
      if (!box) continue;
      plan[lang] = { on: box.checked, device: dev && dev.value !== "" ? Number(dev.value) : null,
                     voice: vx && vx.value ? vx.value : null };
    }
    return plan;
  }

  function renderVoiceRows() {
    const host = $("voiceRows");
    const panelEl = $("voicePanel");
    const tgts = chosenTgts();
    const names = Object.fromEntries((langs || []).map((l) => [l.code, l.name]));
    if (!voicesAvailable || !outputs.length) {
      panelEl.hidden = true;
      return;
    }
    panelEl.hidden = false;
    const saved = (settings && settings.voice) || {};
    host.innerHTML = "";
    for (const lang of tgts) {
      const s = saved[lang] || {};
      const has = (voices[lang] || []).length > 0;
      const row = document.createElement("div");
      row.className = "pair voice-row";
      const box = document.createElement("input");
      box.type = "checkbox"; box.id = "vOn-" + lang; box.checked = !!s.on && has; box.disabled = !has;
      const boxLab = document.createElement("label");
      boxLab.className = "field";
      boxLab.append(box, document.createTextNode(" " + (names[lang] || lang)));
      const dev = document.createElement("select");
      dev.className = "riah-input"; dev.id = "vDev-" + lang;
      dev.add(new Option("Default output", ""));
      for (const o of outputs) dev.add(new Option(o.name + (o.default ? " (default)" : ""), String(o.id)));
      dev.title = [...dev.options].find((o) => o.selected)?.text || "";
      dev.addEventListener("change", () => { dev.title = dev.options[dev.selectedIndex].text; });
      if (s.device != null) dev.value = String(s.device);
      const vx = document.createElement("select");
      vx.className = "riah-input"; vx.id = "vVoice-" + lang;
      if (!has) vx.add(new Option("No voice installed", ""));
      for (const v of voices[lang] || []) vx.add(new Option(v, v));
      if (s.voice) vx.value = s.voice;
      const test = document.createElement("button");
      test.type = "button"; test.className = "riah-button"; test.textContent = "Test";
      test.addEventListener("click", async () => {
        try {
          const r = await post("/api/voice-test", { lang, voice: vx.value || null,
            device: dev.value === "" ? null : Number(dev.value) });
          $("voiceNote").textContent = r.ok ? "That was " + (names[lang] || lang) + " out of that output."
                                            : (r.error || "Nothing came out.");
        } catch (e) { $("voiceNote").textContent = e.message; }
      });
      row.append(boxLab, dev, vx, test);
      host.append(row);
      if (!has) {
        const miss = document.createElement("p");
        miss.className = "hint";
        miss.textContent = "This Mac has no " + (names[lang] || lang) + " voice installed yet " +
          "(System Settings → Accessibility → Spoken Content → System Voice → Manage Voices). Lines stay on the screen only.";
        host.append(miss);
      }
    }
    $("voiceNote").textContent = tgts.length ? "" : "Pick a translation language first.";
  }

  async function sendTgts() {
    const src = $("src").value;
    if ($("tgt").value === src && !$("tgt2").value)
      $("detail").textContent = "That's the spoken language, so only the spoken words will show.";
    try { await post("/api/langs", { src, tgts: chosenTgts() }); } catch (e) { showError(e); }
    renderVoiceRows();
  }
  $("src").addEventListener("change", sendTgts);
  $("tgt").addEventListener("change", sendTgts);
  $("tgt2").addEventListener("change", sendTgts);

  function modelFor(choice, src) {
    if (!choice) return null;
    if (choice === "fast") return src === "en" ? "base.en" : "small";
    return src === "en" ? "small.en" : "large-v3-turbo";
  }

  function choiceFor(model) {
    if (!model) return "";
    return /^(base|small)$|^base\.en$/.test(model) ? "fast" : "accurate";
  }

  // ------------------------------------------------------------------ preview
  // Whole screen: the page fills the miniature. Bar or column: it sits where its window goes.
  let previewLayout = "full";
  function fitPreview() {
    const box = $("preview"), frame = $("previewFrame");
    const k = box.clientWidth / 1920;
    let w = 1920, h = 1080, x = 0, y = 0;
    if (previewLayout === "bar") { h = Math.round(1080 * BAR); y = 1080 - h; }
    if (previewLayout === "side") { w = Math.round(1920 * SIDE); x = 1920 - w; }
    frame.style.width = w + "px";
    frame.style.height = h + "px";
    frame.style.transform = `translate(${x * k}px, ${y * k}px) scale(${k})`;
  }
  function setPreviewLayout(l) {
    if (l === previewLayout) return;
    previewLayout = l;
    $("preview").dataset.layout = l;
    $("previewFrame").src = l === "full" ? "/screen" : `/screen?layout=${l}`;
    fitPreview();
  }
  new ResizeObserver(fitPreview).observe($("preview"));

  // ------------------------------------------------------------------ display
  function showDisplay(d) {
    if (!d) return;
    if (d.fontScale != null) { $("fontScale").value = d.fontScale; $("sizeOut").textContent = Math.round(d.fontScale * 100) + "%"; }
    if (d.visibleRows != null) $("visibleRows").value = String(d.visibleRows);
    for (const k of ["swap", "labels", "wakeLock", "showSrc"]) if (d[k] != null) $(k).checked = !!d[k];
    if (d.layout) { $("layout").value = d.layout; setPreviewLayout(d.layout); }
    if (d.hidden != null) {
      $("hide").textContent = d.hidden ? "Show subtitles" : "Hide subtitles";
      $("hide").setAttribute("aria-pressed", String(!!d.hidden));
      $("hiddenChip").hidden = !d.hidden;   // stays in view so a song's "Hide" isn't forgotten
    }
  }

  // Changes made close together go out as one, so a quick slider drag can't swallow a checkbox.
  let sendTimer = 0;
  let pending = {};
  function sendDisplay(patch, delay = 0) {
    Object.assign(pending, patch);
    clearTimeout(sendTimer);
    sendTimer = setTimeout(() => {
      const out = pending;
      pending = {};
      post("/api/display", out).catch(showError);
    }, delay);
  }

  $("fontScale").addEventListener("input", (e) => {
    const v = Number(e.target.value);
    $("sizeOut").textContent = Math.round(v * 100) + "%";
    sendDisplay({ fontScale: v }, 60);
  });
  $("visibleRows").addEventListener("change", (e) => sendDisplay({ visibleRows: Number(e.target.value) }));
  $("layout").addEventListener("change", (e) => sendDisplay({ layout: e.target.value }));
  for (const k of ["swap", "labels", "wakeLock", "showSrc"]) {
    $(k).addEventListener("change", (e) => sendDisplay({ [k]: e.target.checked }));
  }
  $("clear").addEventListener("click", () => post("/api/clear").catch(showError));
  $("openTranscripts").addEventListener("click", () => post("/api/open-transcripts").catch(showError));

  // Problem report: the person sends it (Captain, 2026-11-27). It shows exactly what goes, then one
  // press sends it, from every build (no key). A send that doesn't go through saves the same text
  // as a file to email or message. Its messages stay in this box: the status up top is for the
  // service.
  const NOT_SENT = "It couldn't be sent from here, so it's saved instead. ";
  let canSend = false;
  function say(text) { $("problemResult").textContent = text; }
  function showProblemBox(open) {
    $("problemBox").hidden = !open;
    $("problemOpen").hidden = open;
    $("problemOpen").setAttribute("aria-expanded", String(open));
  }
  $("problemOpen").addEventListener("click", async () => {
    say("");
    $("problemFile").hidden = true;
    $("problemSend").disabled = false;
    try {
      const r = await fetch("/api/problem-report").then((x) => {
        if (!x.ok) throw new Error(`HTTP ${x.status}`);
        return x.json();
      });
      canSend = !!r.canSend;
      $("problemText").textContent = r.text || "";
      $("problemSend").textContent = canSend ? "Send to Riah Studio" : "Save as a file";
      $("problemLead").textContent = (canSend ? "This is everything the report holds." :
        "This copy of the app can't send it by itself, so it saves this as a file for you to email or " +
        "message to Riah Studio.") + " Nothing else goes: no sound, no words from the service, no notes, nothing typed.";
      showProblemBox(true);
      $("problemText").focus();
    } catch (e) {
      showProblemBox(true);
      say(plain(e.message));
    }
  });
  $("problemCancel").addEventListener("click", () => { showProblemBox(false); $("problemOpen").focus(); });
  $("problemCopy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("problemText").textContent);
      say("Copied. Paste it into an email or a message to Riah Studio.");
    } catch (e) {
      const range = document.createRange();          // no clipboard here: select it for the person
      range.selectNodeContents($("problemText"));
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      say(`Selected. Press ${/Mac/i.test(navigator.userAgent) ? "⌘C" : "Ctrl+C"} to copy it.`);
    }
  });
  $("problemSend").addEventListener("click", async () => {
    const btn = $("problemSend");
    btn.disabled = true;
    say(canSend ? "Sending…" : "Saving…");
    try {
      const r = await post("/api/problem-report");
      if (r.text) $("problemText").textContent = r.text;
      const lead = r.why === "failed" ? NOT_SENT : "";
      if (r.sent) {
        say("Sent to Riah Studio. Thank you.");
        $("problemCancel").focus();                 // Send is done with; keep the keyboard in the box
      } else if (r.saved) {
        say(`${lead}It's in your ${r.folder} folder as “${r.file}”. Email or message that file to Riah Studio.`);
        $("problemFile").hidden = false;
        $("problemFile").focus();
      } else {
        say((lead ? "It couldn't be sent or saved from here. " : "It couldn't be saved as a file. ") +
          "Press Copy and paste it into an email or a message to Riah Studio.");
        btn.disabled = false;
      }
    } catch (e) {
      const m = plain(e.message);
      say(m.startsWith("Can't reach") ? m :
        "It couldn't be sent or saved. Press Copy and paste it into an email or a message to Riah Studio.");
      btn.disabled = false;
    }
  });
  $("problemFile").addEventListener("click", () => post("/api/show-problem-file").catch((e) => say(plain(e.message))));
  $("hide").addEventListener("click", () => {
    sendDisplay({ hidden: $("hide").getAttribute("aria-pressed") !== "true" });
  });

  // ------------------------------------------------------------------ actions
  // Plain words for the problems an operator can actually fix.
  function plain(msg) {
    const m = String(msg || "");
    if (/Failed to fetch|NetworkError|Load failed/i.test(m))
      return "Can't reach Riah Church Translate. Is the app still open?";
    if (/^HTTP \d+$/.test(m) || /^(forbidden|bad json|bad length|too large)$/.test(m))
      return "Something went wrong. Close the app and open it again.";
    return m;
  }

  function showError(e) {
    setStatus("error", plain(e && e.message ? e.message : e));
  }

  let busyUntil = 0;
  $("startStop").addEventListener("click", async () => {
    const btn = $("startStop");
    if (Date.now() < busyUntil) return;     // a quick double-click is one press
    busyUntil = Date.now() + 1500;
    btn.disabled = true;
    try {
      if (running) {
        await post("/api/stop");
      } else {
        const src = $("src").value;
        await post("/api/start", {
          src, tgts: chosenTgts(), input: $("input").value === "" ? null : Number($("input").value),
          channel: chosenChannel(),
          mt: $("mt").value, model: modelFor($("model").value, src), churchWords: $("churchWords").checked,
          voice: voicePlan(),
        });
      }
    } catch (e) { showError(e); }
    btn.disabled = false;
  });

  // Restart: for the sound person. A new sound input and a fresh hearing in about a second; the
  // church screen keeps what it shows.
  let restartUntil = 0;
  $("restart").addEventListener("click", async () => {
    if (Date.now() < restartUntil) return;
    restartUntil = Date.now() + 1000;
    try { await post("/api/restart"); } catch (e) { showError(e); }
  });

  $("swapLangs").addEventListener("click", () => {
    const s = $("src"), t = $("tgt");
    const a = s.value, b = t.value;
    if (![...s.options].some((o) => o.value === b)) {
      $("detail").textContent = "The app can't listen to that language yet, so the two can't swap.";
      return;
    }
    s.value = b; t.value = a;
    if ($("tgt2").value === b) $("tgt2").value = "";
    sendTgts();
  });

  $("openScreen").addEventListener("click", async () => {
    try {
      const r = await post("/api/open-screen");
      if (r.opened === "browser")
        window.open(r.layout && r.layout !== "full" ? `/screen?layout=${r.layout}` : "/screen", "riah-church-screen");
    } catch (e) { showError(e); }
  });

  $("saveHints").addEventListener("click", async () => {
    try {
      await post("/api/glossary", { hints: $("hints").value });
      $("hintsSaved").textContent = running ? "Saved. Used from the next Start." : "Saved.";
    } catch (e) { showError(e); }
  });

  // ------------------------------------------------------------------ sermon notes
  // The file goes to the app on this computer, which keeps only the names and Bible passages in it.
  const NOTES_MAX = 25 * 1024 * 1024;
  function plural(n, one) { return `${n} ${one}${n === 1 ? "" : "s"}`; }
  function showNotes(n) {
    $("notesInfo").textContent = n
      ? `Using ${n.name}: ${plural(n.names, "name")}, ${plural(n.refs, "Bible passage")}.` : "";
    $("removeNotes").hidden = !n;
  }
  function asBase64(file) {
    return new Promise((ok, no) => {
      const r = new FileReader();
      r.onload = () => ok(String(r.result).split(",", 2)[1] || "");
      r.onerror = () => no(new Error("Couldn't open that file."));
      r.readAsDataURL(file);
    });
  }
  $("notesFile").addEventListener("change", async (e) => {
    const file = e.target.files && e.target.files[0];
    e.target.value = "";
    if (!file) return;
    if (file.size > NOTES_MAX) {
      $("notesInfo").textContent = "That file is too big for sermon notes (over 25 MB). Try saving just the notes as a Word file or PDF.";
      return;
    }
    $("notesInfo").textContent = "Reading the notes…";
    try {
      showNotes((await post("/api/notes", { name: file.name, data: await asBase64(file) })).notes);
    } catch (err) { $("notesInfo").textContent = plain(err.message); }
  });
  $("removeNotes").addEventListener("click", async () => {
    try { showNotes((await post("/api/notes", { clear: true })).notes); } catch (err) { showError(err); }
  });

  // ------------------------------------------------------------------ live feed
  function handle(ev) {
    switch (ev.type) {
      case "snapshot":
        setStatus(ev.status.state, ev.status.detail);
        showDisplay(ev.display);
        setLag(ev.stats);
        break;
      case "status": setStatus(ev.state, ev.detail); break;
      case "level": setLevel(ev.level); break;
      case "display": showDisplay(ev.display); break;
      case "line": if (ev.lag != null) setLag({ lag: ev.lag, mtLag: lastMt }); break;
      case "translation": if (ev.lag != null) { lastMt = ev.lag; } break;
    }
  }
  let lastMt = null;

  function connect() {
    const es = new EventSource("/events");
    es.onmessage = (m) => { try { handle(JSON.parse(m.data)); } catch (e) { console.warn(e); } };
    es.onerror = () => { if (state !== "error") $("detail").textContent = "Reconnecting to the app…"; };
  }

  async function boot() {
    const [st, ls, devs, outs, vox] = await Promise.all([
      fetch("/api/state").then((r) => r.json()),
      fetch("/api/langs").then((r) => r.json()),
      fetch("/api/devices").then((r) => r.json()).catch(() => []),
      fetch("/api/outputs").then((r) => r.json()).catch(() => []),
      fetch("/api/voices").then((r) => r.json()).catch(() => ({ available: false, voices: {} })),
    ]);
    settings = st.settings || {};
    langs = ls;
    fillLangs();
    devices = devs;
    outputs = Array.isArray(outs) ? outs : [];
    voices = (vox && vox.voices) || {};
    voicesAvailable = !!(vox && vox.available);
    for (const d of devs) $("input").add(new Option(d.name + (d.default ? " (default)" : ""), String(d.id)));
    if (settings.input != null) $("input").value = String(settings.input);
    fillChannels(settings.channel);
    $("input").addEventListener("change", () => fillChannels(null));
    $("mt").value = settings.mt || "nllb";
    $("model").value = choiceFor(settings.model);
    $("churchWords").checked = settings.churchWords !== false;
    $("crashReports").checked = settings.crashReports === true;
    $("crashReports").addEventListener("change", (e) => {
      const want = e.target.checked;
      post("/api/reports", { on: want }).catch(() => { e.target.checked = !want; });
    });
    $("hints").value = (st.glossary && st.glossary.hints) || "";
    showNotes(st.notes);
    showDisplay(st.display);
    setStatus(st.status.state, st.status.detail);
    setLag(st.stats);
    $("version").textContent = `Riah Church Translate ${st.version}`;
    if (st.lanUrl) $("lanNote").textContent = `Another computer can show the screen at ${st.lanUrl}`;
    fitPreview();
    renderVoiceRows();
    connect();
  }

  boot().catch(showError);
})();
