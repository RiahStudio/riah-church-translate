// Shared live state, a port of the app's hub.py. The church screen (a second window, or the preview
// on this page) listens on a BroadcastChannel: same computer, same browser, nothing goes over the
// network. A screen that opens late says "hello" and gets everything that is on screen now.

export const CHANNEL = "riah-live";
const BEAT_MS = 2000;
const QUIET = new Set(["level", "beat"]);     // operator-only events: the screen never needs them

export function normLangs(d) {
  let tgts = d && d.tgts;
  if (tgts == null) tgts = d && d.tgt ? [d.tgt] : [];
  return { src: (d && d.src) || "en", tgts: [...tgts] };
}

export class Hub {
  constructor({ display = null, langs = null, names = [] } = {}) {
    this.lines = [];             // {id, seg, src, dst: {lang: text}, draft: {lang: text}, lag, t}
    this.partial = "";
    this.seg = null;             // line number the partial row will become
    this.drafts = {};            // lang -> draft translation of the partial row
    this.status = { state: "stopped", detail: "Press Start when the service begins." };
    this.display = { fontScale: 1, swap: false, labels: true, visibleRows: 3, wakeLock: false, showSrc: true,
      hidden: false, ...(display || {}) };
    this.langs = normLangs(langs || { src: "en", tgts: ["th"] });
    this.stats = { lag: null, mtLag: null, level: 0 };
    this.names = names;
    this.taps = [];              // callables that also get every event (this page, the transcript, tests)
    this.ch = null;
    try {
      this.ch = new BroadcastChannel(CHANNEL);
      this.ch.onmessage = (m) => this.heard(m.data);
    } catch { this.ch = null; }
    this.lastBeat = 0;
    this.beat = setInterval(() => this.alive(), BEAT_MS);
  }

  // A heartbeat so the church screen knows this page is still there. Also called for every chunk
  // of sound, because a background tab's timers can be slowed to once a minute.
  alive() {
    const now = Date.now();
    if (now - this.lastBeat < BEAT_MS - 250) return;
    this.lastBeat = now;
    this.send({ type: "beat" });
  }

  heard(m) {
    if (m && m.type === "hello") {
      this.send({ type: "names", names: this.names });
      this.send(this.snapshot());
    }
  }

  send(m) {
    try { this.ch?.postMessage(m); } catch { /* a closed channel: the screen asks again */ }
  }

  close() {
    clearInterval(this.beat);
    try { this.ch?.close(); } catch {}
    this.ch = null;
  }

  snapshot() {
    return {
      type: "snapshot",
      lines: this.lines.map((l) => ({ ...l, dst: { ...l.dst }, draft: { ...l.draft } })),
      partial: this.partial, seg: this.seg, drafts: { ...this.drafts }, status: { ...this.status },
      display: { ...this.display }, langs: { ...this.langs, tgts: [...this.langs.tgts] }, stats: { ...this.stats },
    };
  }

  publish(ev) {
    if (ev.t == null) ev.t = Date.now() / 1000;
    this.apply(ev);
    if (!QUIET.has(ev.type)) this.send(ev);
    for (const tap of this.taps) {
      try { tap(ev); } catch (e) { console.warn(e); }
    }
  }

  line(seg) {
    if (seg == null) return null;
    for (let i = this.lines.length - 1; i >= 0; i--) if (this.lines[i].seg === seg) return this.lines[i];
    return null;
  }

  apply(ev) {
    switch (ev.type) {
      case "line": {
        const seg = ev.seg;
        const keep = ev.keepDraft && seg === this.seg ? { ...this.drafts } : {};
        this.lines.push({ id: ev.id, seg, src: ev.src, dst: {}, draft: keep, lag: ev.lag, t: ev.t });
        if (this.lines.length > 80) this.lines.shift();
        this.partial = "";
        this.drafts = {};
        this.seg = seg == null ? null : seg + 1;
        if (ev.lag != null) this.stats.lag = ev.lag;
        break;
      }
      case "translation": {
        const code = ev.lang || this.langs.tgts[0];
        for (let i = this.lines.length - 1; i >= 0; i--) {
          if (this.lines[i].id === ev.id) { this.lines[i].dst[code] = ev.dst; break; }
        }
        if (ev.lag != null) this.stats.mtLag = ev.lag;
        break;
      }
      case "draft": {
        const line = this.line(ev.seg);
        if (line) {
          if (!(ev.lang in line.dst)) line.draft[ev.lang] = ev.text;
        } else if (this.seg == null || ev.seg === this.seg) {
          this.seg = ev.seg;
          this.drafts[ev.lang] = ev.text;
        }
        break;
      }
      case "partial":
        this.partial = ev.text;
        if (ev.seg != null) {
          if (ev.seg !== this.seg) this.drafts = {};
          this.seg = ev.seg;
        }
        break;
      case "status":
        this.status = { state: ev.state, detail: ev.detail || "" };
        break;
      case "display":
        Object.assign(this.display, ev.display);
        break;
      case "langs":
        this.langs = normLangs(ev);
        this.drafts = {};
        break;
      case "clear":
        this.lines = [];
        this.partial = "";
        this.drafts = {};
        break;
      case "level":
        this.stats.level = ev.level;
        break;
    }
  }
}
