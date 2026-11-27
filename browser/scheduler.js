// Background translation order, a port of the app's engine.py Translations: finished lines go
// first, into every language on screen at once; then only the newest draft (older drafts are
// simply dropped). `mt.translate(text, src, tgts, {glossary, keep, draft})` resolves to
// {tgt: [text, ids|null]}.

export const DRAFT_MIN = 3;     // words agreed before the first draft of a line
export const DRAFT_STEP = 2;    // new words before the next draft
const SLOW_FINAL = 2.5;         // seconds per language a finished line may take before drafts stop

const words = (text) => String(text).toLowerCase().replace(/[^\p{L}\p{N}\p{M}_\s']/gu, " ").split(/\s+/).filter(Boolean);

// Does `line` begin with every word of `start`? (A draft is only kept for the line it belongs to.)
export function covers(line, start) {
  const a = words(line), b = words(start);
  if (!b.length) return false;
  if (a.length === 1 && b.length === 1) return a[0].startsWith(b[0]);   // unspaced scripts: characters
  if (a.length < b.length) return false;
  return b.every((w, i) => a[i] === w);
}

export function size(text, spaced) {
  return spaced ? String(text).split(/\s+/).filter(Boolean).length : Math.floor(String(text).replace(/\s/g, "").length / 3);
}

export class Translations {
  constructor({ mt, src, tgts, glossary, onFinal, onDraft, spaced = true, drafts = true, onError = null,
    staleAfter = () => 3 }) {
    this.mt = mt;
    this.src = src;
    this.tgts = [...tgts];
    this.glossary = glossary || {};
    this.onFinal = onFinal;
    this.onDraft = onDraft;
    this.onError = onError;
    this.spaced = spaced;
    this.drafts = drafts;
    this.finals = [];
    this.draftJob = null;             // [seg, text]: the newest only
    this.asked = [null, 0];           // [seg, size] of the last draft asked for
    this.state = { seg: null, src: "", pieces: {} };
    this.cut = new Map();             // seg -> finished line text
    this.context = [];
    this.closed = false;
    this.running = false;
    this.finalSec = 0;                // running average seconds per language for a finished line
    this.idle = [];
    this.staleAfter = staleAfter;     // lines on the church screen (newer lines than this: it's gone)
    this.skipped = 0;
  }

  setTargets(tgts) {
    this.tgts = [...tgts];
    this.state = { seg: null, src: "", pieces: {} };
  }

  draftCovers(seg, text) {
    return this.state.seg === seg && covers(text, this.state.src);
  }

  // A slow computer: drafts only make the finished lines later, so stop asking for them.
  get draftsOn() {
    return this.drafts && this.finalSec <= SLOW_FINAL;
  }

  addLine(lineId, seg, text) {
    this.cut.set(seg, text);
    for (const k of [...this.cut.keys()]) if (k < seg - 20) this.cut.delete(k);
    this.finals.push([lineId, seg, text, this.context.slice(-3)]);
    this.context.push(text);
    if (this.context.length > 10) this.context.shift();
    if (this.draftJob && this.draftJob[0] <= seg) this.draftJob = null;
    this.kick();
  }

  wantDraft(seg, text) {
    if (!this.draftsOn || !text || !this.tgts.length) return;
    const n = size(text, this.spaced);
    const [lastSeg, lastN] = this.asked;
    if (n < DRAFT_MIN || (lastSeg === seg && n - lastN < DRAFT_STEP)) return;
    this.asked = [seg, n];
    this.draftJob = [seg, text];
    this.kick();
  }

  // Resolves when every finished line has been translated (or after `timeoutMs`).
  async close(timeoutMs = 30000) {
    this.draftJob = null;
    const done = new Promise((resolve) => {
      if (!this.running && !this.finals.length) resolve();
      else this.idle.push(resolve);
    });
    await Promise.race([done, new Promise((r) => setTimeout(r, timeoutMs))]);
    this.closed = true;
  }

  get busy() {
    return this.running;
  }

  kick() {
    if (this.running || this.closed) return;
    this.running = true;
    this.loop().finally(() => {
      this.running = false;
      for (const r of this.idle.splice(0)) r();
    });
  }

  async loop() {
    while (!this.closed) {
      let kind, job;
      // On a slow computer the translator can fall behind. A line with `staleAfter` newer lines has
      // left the church screen, so translating it would only hold up the lines people can read.
      const newest = this.finals.length ? this.finals[this.finals.length - 1][1] : 0;
      while (this.finals.length > 1 && newest - this.finals[0][1] >= this.staleAfter()) {
        this.finals.shift();
        this.skipped += 1;
      }
      if (this.finals.length) { kind = "final"; job = this.finals.shift(); }
      else if (this.draftJob) { kind = "draft"; job = this.draftJob; this.draftJob = null; }
      else return;
      const tgts = [...this.tgts];
      const st = this.state;
      if (!tgts.length) continue;
      if (kind === "final") await this.final(job, tgts, st);
      else await this.draft(job, tgts, st);
    }
  }

  async final([lineId, seg, text, ctx], tgts, st) {
    const keep = st.seg === seg && covers(text, st.src) ? st.pieces : null;
    const t0 = performance.now();
    let res;
    try {
      res = await this.mt.translate(text, this.src, tgts, { glossary: this.glossary, keep, context: ctx });
    } catch (err) {
      // The translator is still downloading or restarting: this line shows in the original only.
      if (err && err.notReady) return;
      this.onError?.(err);       // a real failure: show the original rather than nothing
      res = Object.fromEntries(tgts.map((t) => [t, [text, null]]));
    }
    const secs = (performance.now() - t0) / 1000;
    this.finalSec = 0.7 * this.finalSec + 0.3 * (secs / tgts.length);
    for (const t of tgts) if (res[t]) this.onFinal(lineId, t, res[t][0], secs);
  }

  async draft([seg, text], tgts, st) {
    if (this.cut.has(seg)) return;               // the line is finished; its final is on the way
    const keep = st.seg === seg && covers(text, st.src) ? st.pieces : null;
    let res;
    try {
      res = await this.mt.translate(text, this.src, tgts, { glossary: this.glossary, keep, draft: true });
    } catch (err) {
      if (!(err && err.notReady)) this.onError?.(err);
      return;
    }
    const line = this.cut.get(seg);
    if (line !== undefined && !covers(line, text)) return;   // the line ended before these words; drop it
    const pieces = {};
    for (const [t, v] of Object.entries(res)) if (v && v[1]) pieces[t] = v[1];
    this.state = { seg, src: text, pieces };
    for (const t of tgts) if (res[t]) this.onDraft(seg, t, res[t][0], text);
  }
}
