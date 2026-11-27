// Sermon notes: the preacher's own file (.txt, .md or .docx) read on this computer and boiled down
// to the names in it (people, places, Bible books), which the speech model is told to listen for.
// The notes themselves are never shown, stored or sent anywhere: only the short list of names is
// kept, in memory, until the page is closed.

const MAX_CHARS = 200;

// Capitalised words that are not names.
const STOP = new Set(("I I'm I've I'll I'd Im Ive A An The And But Or So If Then When While Because As At By For From In " +
  "Into Of On Onto Over To Up With Without Is Are Was Were Be Been Being Do Does Did Have Has Had Will Would Shall Should " +
  "Can Could May Might Must This That These Those There Here Where What Who Whom Whose Why How Which We Us Our Ours You " +
  "Your Yours He Him His She Her Hers It Its They Them Their Theirs My Me Mine Not No Yes All Any Some Every Each One Two " +
  "Three Four Five Six Seven Eight Nine Ten First Second Third Last Next Now Today Tonight Tomorrow Yesterday Amen Ok OK " +
  "Monday Tuesday Wednesday Thursday Friday Saturday Sunday January February March April June July August September " +
  "October November December Mr Mrs Ms Dr St Point Points Intro Introduction Conclusion Notes Note Sermon Outline Read " +
  "Pray Prayer Song Songs Verse Verses Chapter Chapters Video Slide Slides Lord God Jesus Christ Holy Spirit Bible Scripture " +
  "Let Let's Lets Just Also Even Still Only Very Much More Most Many Like Love Life Live Go Going Come Coming Know See Look " +
  "Think Say Said Tell Told Ask Give Take Make Made Get Got Thank Thanks Please Welcome Good Great Big Small New Old " +
  "Remember Imagine Notice Consider Application Apply Main Big Idea Question Questions Answer Story Illustration Example " +
  "Turn Open Close Stand Sit Sing").split(/\s+/));

// Words the speech model already writes the right way, so they never take up room in the list.
const COMMON = new Set(["God", "Jesus", "Christ", "Lord", "Holy", "Spirit", "Bible", "Amen"]);

let bibleNames = null;    // Set of Bible names and books (from bible_words.json)

async function bibleList(url) {
  if (bibleNames) return bibleNames;
  bibleNames = new Set();
  try {
    const r = await fetch(url);
    if (!r.ok) return bibleNames;
    const doc = await r.json();
    for (const n of Object.keys(doc.names || {})) bibleNames.add(n);
    for (const b of doc.books || []) {
      if (b.en) bibleNames.add(b.en);
      for (const s of b.say || []) bibleNames.add(s);
    }
  } catch { /* no list: only the capitalised words count */ }
  return bibleNames;
}

// ---------------------------------------------------------------------- reading the file
export async function readNotes(file) {
  const name = (file.name || "").toLowerCase();
  const bytes = new Uint8Array(await file.arrayBuffer());
  if (name.endsWith(".docx") || (bytes[0] === 0x50 && bytes[1] === 0x4b)) return docxText(bytes);
  return new TextDecoder("utf-8").decode(bytes).replace(/^﻿/, "");
}

// A .docx is a zip; the words are in word/document.xml.
export async function docxText(bytes) {
  const xml = await unzipOne(bytes, "word/document.xml");
  if (xml == null) throw new Error("That Word file couldn't be read. Save it as .docx or .txt and try again.");
  return xml
    .replace(/<w:tab\/>/g, " ")
    .replace(/<w:br[^>]*\/>/g, "\n")
    .replace(/<\/w:p>/g, "\n")
    .replace(/<[^>]+>/g, "")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, "\"").replace(/&apos;/g, "'").replace(/&amp;/g, "&");
}

async function unzipOne(bytes, want) {
  const v = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  // End of central directory: the last 0x06054b50 within the final 64 KB.
  let eocd = -1;
  for (let i = bytes.length - 22; i >= Math.max(0, bytes.length - 65557); i--) {
    if (v.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) return null;
  const count = v.getUint16(eocd + 10, true);
  let p = v.getUint32(eocd + 16, true);
  const dec = new TextDecoder();
  for (let n = 0; n < count && p + 46 <= bytes.length; n++) {
    if (v.getUint32(p, true) !== 0x02014b50) return null;
    const method = v.getUint16(p + 10, true);
    const size = v.getUint32(p + 20, true);
    const nameLen = v.getUint16(p + 28, true);
    const extraLen = v.getUint16(p + 30, true);
    const commentLen = v.getUint16(p + 32, true);
    const local = v.getUint32(p + 42, true);
    const fname = dec.decode(bytes.subarray(p + 46, p + 46 + nameLen));
    p += 46 + nameLen + extraLen + commentLen;
    if (fname !== want) continue;
    if (v.getUint32(local, true) !== 0x04034b50) return null;
    const start = local + 30 + v.getUint16(local + 26, true) + v.getUint16(local + 28, true);
    const data = bytes.subarray(start, start + size);
    if (method === 0) return dec.decode(data);
    if (method !== 8 || typeof DecompressionStream === "undefined") return null;
    const stream = new Blob([data]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
    return await new Response(stream).text();
  }
  return null;
}

// ---------------------------------------------------------------------- names
// The names in the notes, most mentioned first, as one comma-separated line of at most ~200 chars.
export async function namesFrom(text, bibleUrl) {
  const bible = await bibleList(bibleUrl);
  const counts = new Map();
  const add = (n, w = 1) => counts.set(n, (counts.get(n) || 0) + w);
  const clean = String(text).replace(/[“”]/g, "\"").replace(/[‘’]/g, "'");
  for (const sentence of clean.split(/(?<=[.!?:;])\s+|\n+/)) {
    const words = sentence.match(/[\p{L}][\p{L}\p{M}'-]*/gu) || [];
    let run = [];
    const flush = () => {
      // "Grace Church", "Chiang Mai": capitalised words in a row are one name.
      while (run.length && STOP.has(run[0].w)) run.shift();
      while (run.length && STOP.has(run[run.length - 1].w)) run.pop();
      if (run.length) {
        const joined = run.map((r) => r.w).join(" ");
        const first = run[0].i === 0;
        // A word that starts a sentence is a name only if it is one we know (or part of a longer name).
        if (run.length > 1 || !first || bible.has(joined)) add(joined, bible.has(joined) ? 2 : 1);
      }
      run = [];
    };
    words.forEach((w, i) => {
      const name = w.replace(/'s$/i, "").replace(/^'+|'+$/g, "");
      const cap = /^\p{Lu}/u.test(name) && !/^\p{Lu}+$/u.test(name.length > 3 ? name : "x");   // skip SHOUTED words
      if (cap && name.length > 1 && !COMMON.has(name)) run.push({ w: name, i });
      else flush();
    });
    flush();
  }
  for (const n of [...counts.keys()]) {
    if (STOP.has(n) || n.split(" ").length > 4) counts.delete(n);
  }
  // A name also counted inside a longer one ("Chiang" in "Chiang Mai") is dropped.
  const all = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const out = [];
  let len = 0;
  for (const [n] of all) {
    if (all.some(([m]) => m !== n && m.split(" ").includes(n))) continue;
    if (len + n.length + 2 > MAX_CHARS) continue;
    out.push(n);
    len += n.length + 2;
  }
  return out;
}

// The operator's own words first (they always fit), then the names from the notes.
export function joinHints(own, names, limit = 240) {
  const mine = String(own || "").trim().replace(/\s+/g, " ");
  const have = new Set(mine.toLowerCase().split(/\s*,\s*/).filter(Boolean));
  let out = mine;
  for (const n of names || []) {
    if (have.has(n.toLowerCase())) continue;
    const next = out ? `${out}, ${n}` : n;
    if (next.length > limit) break;
    out = next;
  }
  return out;
}
