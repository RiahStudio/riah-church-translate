// Bible names, books and church words, spelled the way the Thai and Chinese Bibles spell them.
// A port of riah_translate/bible_words.py: it only ever touches a word when the English line says
// that name. The word list (bible_words.json) holds names and the translator's known wrong
// spellings, never Bible text.

const LANGS = ["th", "zh", "zh-TW"];
const TH_LEAD = ["พระ", "ชาว"];
const TH_LEAD_ALREADY = ["พระ", "ชาว", "คน", "พวก", "ชน", "ศาสนา", "องค์"];
const ZH_TAIL = ["河", "溪", "人"];

// Church words NLLB gets in the Catholic or Buddhist form: [English words, wrong, right].
const TERM_FIX = {
  th: [
    [/\bpray(s|ed|ing)?\b|\bprayers?\b/i, "สวดมนต์|ภาวนา", "อธิษฐาน"],
    [/\b(?:the|this) verse\b/i, "บทนี้", "ข้อนี้"],
    [/\bpastors?\b/i, "บาทหลวง", "ศิษยาภิบาล"],
    [/\bworship(s|ped|ping)?\b/i, "บูชา(?!ยัญ)", "นมัสการ"],
    [/\bnicodemus\b/i, "นิโ?[กค]ร?[ก-์]{0,5}?(?:มัส|โมส|มุส)", "นิโคเดมัส"],
    [/\bchiang mai\b/i, "(?:ชาง|เชียง|เจียง|ชิง)\\s?(?:ใหม|ไหม|ไม)่?", "เชียงใหม่"],
    [/\bchiang rai\b/i, "(?:ชาง|เชียง|เจียง|ชิง)\\s?(?:ราย|ไร|ไหร)", "เชียงราย"],
  ],
  zh: [
    [/\bpray(s|ed|ing)?\b|\bprayers?\b/i, "祈祷", "祷告"],
    [/\bchiang mai\b/i, "[长昌张江蒋强]迈|(?<!清)迈(?=[，。！？、；：,.!?;:\\s]|$)", "清迈"],
    [/\bchiang rai\b/i, "[长昌张江蒋强]莱|(?<!清)莱(?=[，。！？、；：,.!?;:\\s]|$)", "清莱"],
  ],
  "zh-TW": [[/\bpray(s|ed|ing)?\b|\bprayers?\b/i, "祈禱", "禱告"]],
};

const NUM = { "1": "1", "2": "2", "3": "3", first: "1", second: "2", third: "3",
  "1st": "1", "2nd": "2", "3rd": "3", i: "1", ii: "2", iii: "3" };
const REF_AFTER = "(?=\\s*(?:[0-9๐-๙]|第|บท))";
const VERSE = /\bverses?\s+(\d+)/gi;
const CHAPTER = /\bchapters?\s+(\d+)/gi;
const GOSPEL = /\bgospel\s+(?:of|according\s+to)\s+(?:saint\s+|st\.?\s+)?(matthew|mark|luke|john)\b/i;
const TH_GOSPEL = { matthew: "มัทธิว", mark: "มาระโก", luke: "ลูกา", john: "ยอห์น" };
const TH_GOSPEL_ALSO = { matthew: "แมทธิว|แมทธิว", mark: "มาร์ค|มาร์ก", luke: "ลุค|ลูค", john: "จอห์น" };
const TH_GOSPEL_WRONG = "(?:(?:พระวจนะ|ข่าวประเสริฐ|กิตติคุณ|พระธรรม|วัสดุ)(?:ของ)?)?";
const TWICE = /\b(\d+)\s+\1\b/g;
const VERSE_FIX = { th: ["บท(?:ที่|ความ)?\\s*{n}(?![0-9])", "ข้อ {n}"],
  zh: ["第\\s*{n}\\s*章", "第{n}节"], "zh-TW": ["第\\s*{n}\\s*章", "第{n}節"] };
const CJK_RUN = /[㐀-鿿豈-﫿]+/g;
const ZH_GRAMMAR = new Set("的了是在和与對对把被就也都很这這们們我你她它说說到给給向从從为為有要会會能吗嗎呢吧啊");

const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const replaceAll = (s, a, b) => s.split(a).join(b);
const replaceFirst = (s, a, b) => { const i = s.indexOf(a); return i < 0 ? s : s.slice(0, i) + b + s.slice(i + a.length); };
const byLenDesc = (a, b) => b.length - a.length;
const count = (s, c) => { let n = 0; for (const x of s) if (x === c) n++; return n; };
const allMatches = (re, s) => [...s.matchAll(re)].map((m) => m[1]);

export class BibleWords {
  constructor(doc) {
    this.names = doc.names || {};
    this.miss = doc.miss || {};
    this.books = {};
    for (const b of doc.books || []) this.books[b.code] = b;
    const words = Object.keys(this.names).sort(byLenDesc);
    this.nameRe = words.length ? new RegExp("\\b(" + words.map(esc).join("|") + ")\\b", "g") : null;
    this.bookOf = new Map();
    for (const b of Object.values(this.books)) {
      for (const say of (b.say && b.say.length ? b.say : [b.en])) this.bookOf.set(`${b.num || ""}|${say.toLowerCase()}`, b.code);
    }
    const says = [...new Set([...this.bookOf.keys()].map((k) => k.slice(k.indexOf("|") + 1)))].sort(byLenDesc);
    const num = Object.keys(NUM).sort(byLenDesc).map(esc).join("|");
    const name = says.map(esc).join("|");
    this.bookRe = new RegExp(
      `(?:\\b(?<num>${num})\\s+)?\\b(?<name>${name})\\b(?=\\s*(?:chapter\\b|ch\\.|\\d))` +
      `|\\b(?:book|gospel|letter|epistle) of (?:the )?(?:(?<num2>${num})\\s+)?(?<name2>${name})\\b`, "gi");
    this.spelled = {};
    for (const t of LANGS) this.spelled[t] = new Set(Object.values(this.names).map((e) => e[t]).filter(Boolean));
    this.chars = {};
  }

  // -- what the English line mentions
  namesIn(src) {
    if (!this.nameRe || !src) return [];
    return [...new Set(allMatches(this.nameRe, src.replaceAll("’", "'")))];
  }

  booksIn(src, spans) {
    const out = [];
    for (const m of (src || "").matchAll(this.bookRe)) {
      const g = m.groups;
      const n = (g.num || g.num2 || "").toLowerCase();
      const said = (g.name || g.name2).toLowerCase();
      const code = this.bookOf.get(`${NUM[n] || ""}|${said}`);
      if (code) {
        out.push([code, m[0]]);
        if (spans) spans.push([m.index, m.index + m[0].length]);
      }
    }
    return out;
  }

  split(src) {
    const spans = [];
    const books = this.booksIn(src, spans);
    let rest = src;
    for (const [a, b] of spans) rest = rest.slice(0, a) + " ".repeat(b - a) + rest.slice(b);
    return [books, this.namesIn(rest)];
  }

  hints(src, tgt) {
    const [books, names] = this.split(src);
    const out = names.filter((w) => this.names[w][tgt]).map((w) => [w, this.names[w][tgt]]);
    for (const [c, said] of books) if (this.books[c][tgt]) out.push([said, this.books[c][tgt]]);
    return out;
  }

  // -- the fix
  fix(src, out, tgt) {
    if (!LANGS.includes(tgt) || !src || !out) return out;
    const [books, found] = this.split(src);
    const wants = found.map((w) => this.names[w][tgt]).filter(Boolean);
    for (const w of found) {
      const want = this.names[w][tgt];
      if (want) out = this.fixName(w, want, out, tgt, wants.filter((x) => x !== want));
    }
    for (const [code, said] of books) {
      const want = this.books[code][tgt];
      if (want) out = this.fixBook(code, said, want, out, tgt);
    }
    for (const [pat, wrong, right] of TERM_FIX[tgt] || []) {
      if (pat.test(src)) out = out.replace(new RegExp(wrong, "g"), () => right);
    }
    const chapters = new Set([...allMatches(CHAPTER, src), ...allMatches(/\b(\d+):\d/g, src)]);
    const verses = [...new Set(allMatches(VERSE, src))].filter((n) => !chapters.has(n)).sort();
    for (const n of verses) {
      const [wrong, right] = VERSE_FIX[tgt];
      out = out.replace(new RegExp(wrong.replaceAll("{n}", n), "g"), () => right.replaceAll("{n}", n));
    }
    const gospel = tgt === "th" ? src.match(GOSPEL) : null;
    if (gospel && !out.includes("พระกิตติคุณ")) {
      const name = gospel[1].toLowerCase();
      const book = TH_GOSPEL[name], said = `(?:${TH_GOSPEL[name]}|${TH_GOSPEL_ALSO[name]})`;
      if ((out.match(new RegExp(said, "g")) || []).length === 1) {   // with two, which one is the Gospel isn't sure
        out = out.replace(new RegExp(TH_GOSPEL_WRONG + said, "g"), () => "พระกิตติคุณ" + book);
      }
    }
    // A number said twice, run together in the translation ("chapter 3 3" became "chapter 33").
    for (const n of new Set(allMatches(TWICE, src))) {
      const twice = new RegExp(`(?<![0-9])${n}${n}(?![0-9])`, "g");
      if (!new RegExp(twice.source).test(src)) out = out.replace(twice, () => n);
    }
    return out;
  }

  ok(w, want, out, tgt) {
    if (out.includes(want)) return true;
    if (tgt === "th" && (this.names[w].th_also || []).some((a) => out.includes(a))) return true;
    return tgt !== "th" && ZH_TAIL.some((t) => want.endsWith(t)) && want.length > 2 && out.includes(want.slice(0, -1));
  }

  fixName(w, want, out, tgt, others) {
    if (this.ok(w, want, out, tgt)) return this.alsoWrong(w, want, out, tgt, others);
    // Left in English letters.
    const latin = new RegExp(`(?<![A-Za-z])${esc(w)}(?![A-Za-z])`, "g");
    if (latin.test(out)) return out.replace(latin, () => want);
    // พระเยซู written as bare เยซู.
    if (tgt === "th") {
      for (const lead of TH_LEAD) {
        const bare = want.slice(lead.length);
        if (want.startsWith(lead) && bare.length > 1 && out.includes(bare)) {
          const i = out.indexOf(bare);
          if (!TH_LEAD_ALREADY.some((x) => out.slice(0, i).endsWith(x))) return out.slice(0, i) + want + out.slice(i + bare.length);
          return out;
        }
      }
    }
    // A spelling the translator is known to use for this name.
    for (const m of [...((this.miss[tgt] || {})[w] || [])].sort(byLenDesc)) {
      if (out.includes(m) && !others.some((o) => o.includes(m) && out.includes(o))) return replaceAll(out, m, want);
    }
    if (tgt !== "th") {
      const near = this.near(w, want, out, tgt, others);
      if (near) return replaceAll(out, near, want);
    }
    return out;
  }

  // The right spelling and a wrong one in the same line.
  alsoWrong(w, want, out, tgt, others) {
    for (const m of (this.miss[tgt] || {})[w] || []) {
      if (out.includes(m) && !want.includes(m) && !m.includes(want) && !others.some((o) => o.includes(m) && out.includes(o))) {
        out = replaceAll(out, m, want);
      }
    }
    if (tgt !== "th" && out.includes(want)) {
      const near = this.near(w, want, out, tgt, others);
      if (near && !want.includes(near) && !near.includes(want)) out = replaceAll(out, near, want);
    }
    return out;
  }

  nameChars(tgt) {
    if (!this.chars[tgt]) {
      const got = new Set([...this.spelled[tgt] || []].join(""));
      for (const ms of Object.values(this.miss[tgt] || {})) for (const c of ms.join("")) got.add(c);
      for (const c of ZH_GRAMMAR) got.delete(c);
      this.chars[tgt] = got;
    }
    return this.chars[tgt];
  }

  // A whole sounded-out name in a Chinese line that is a near spelling of this one.
  near(w, want, out, tgt, others) {
    if ([...want].length < 3) return null;
    const forms = [want, ...((this.miss[tgt] || {})[w] || [])];
    const chars = this.nameChars(tgt);
    const tails = new RegExp(`[${ZH_TAIL.join("")}]+$`);
    const wantLen = [...want].length;
    let best = null, bestKey = null;
    for (const run of out.match(CJK_RUN) || []) {
      // runs of name characters and other characters, like itertools.groupby
      const groups = [];
      for (const c of run) {
        const isName = chars.has(c);
        if (groups.length && groups[groups.length - 1][0] === isName) groups[groups.length - 1][1] += c;
        else groups.push([isName, c]);
      }
      for (const [isName, piece] of groups) {
        const len = [...piece].length;
        if (!isName || Math.abs(len - wantLen) > 1 || this.spelled[tgt].has(piece)
          || this.spelled[tgt].has(piece.replace(tails, ""))
          || others.some((o) => piece.includes(o) || o.includes(piece))) continue;
        for (const f of forms) {
          let same = 0;
          for (const c of new Set(piece)) same += Math.min(count(piece, c), count(f, c));
          const key = [same, -Math.abs(len - wantLen)];
          const better = !bestKey || key[0] > bestKey[0] || (key[0] === bestKey[0] && key[1] > bestKey[1]);
          if ([...piece][0] === [...f][0] && same >= 2 && same * 2 >= [...f].length && better) { best = piece; bestKey = key; }
        }
      }
    }
    return best;
  }

  fixBook(code, said, want, out, tgt) {
    if (out.includes(want)) return out;
    if (out.includes(said)) return replaceAll(out, said, want);
    for (const m of [...((this.miss[tgt] || {})[code] || [])].sort(byLenDesc)) {
      if (!out.includes(m)) continue;
      if (this.spelled[tgt] && this.spelled[tgt].has(m)) {
        // Another name's spelling (约翰 for the Gospel of John): only right before a chapter.
        const next = out.replace(new RegExp(esc(m) + REF_AFTER), () => want);
        if (next !== out) return next;
        continue;
      }
      return replaceFirst(out, m, want);
    }
    return out;
  }
}

let shared = null;
export async function loadBibleWords(url) {
  if (!shared) {
    shared = fetch(url).then((r) => (r.ok ? r.json() : null)).then((doc) => (doc ? new BibleWords(doc) : null)).catch(() => null);
  }
  return shared;
}
