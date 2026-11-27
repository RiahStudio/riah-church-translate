// Fast checks for the browser version's logic (browser/*.js), with no models and no browser: the
// same cases as tests/test_logic.py where the app has them, so the two stay in step.
//
//     node tests/browser_port_test.mjs

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { deflateRawSync } from "node:zlib";
import { fileURLToPath } from "node:url";

const ROOT = new URL("../", import.meta.url);
const B = (f) => new URL(`browser/${f}`, ROOT).href;

const { covers, Translations } = await import(B("scheduler.js"));
const { Hub } = await import(B("hub.js"));
const { churchGlossary, phraseKey } = await import(B("church-words.js"));
const { tidy, exactPhrase, applyFixes, tooShort, halves, joinHalves } = await import(B("text-fixes.js"));
const { BibleWords } = await import(B("bible-words.js"));
const { StreamingTranscriber, SR, dropEchoes, dropRepeats, soundsLike, silentTailByLoudness } = await import(B("streaming.js"));
const { speechSegments, collectChunks, SpeechMap } = await import(B("vad.js"));
const { splitOnSpaces, mergePunctuations, wordTimings } = await import(B("whisper-words.js"));
const { docxText, namesFrom, joinHints } = await import(B("notes.js"));
const { publicList, lang } = await import(B("langs.js"));

const tests = [];
const test = (name, fn) => tests.push([name, fn]);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitFor(cond, ms = 3000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (cond()) return true;
    await sleep(5);
  }
  return false;
}

// Upper-cases the text; remembers what it was asked (tests/test_logic.py FakeMt).
class FakeMt {
  constructor(delay = 0) { this.calls = []; this.delay = delay; }
  async translate(text, src, tgts, { keep = null, draft = false } = {}) {
    this.calls.push([draft ? "draft" : "final", text, [...tgts], !!keep]);
    if (this.delay) await sleep(this.delay);
    return Object.fromEntries(tgts.map((t) => [t, [`${t}:${text.toUpperCase()}`, [...text.toUpperCase()]]]));
  }
}

// ---------------------------------------------------------------------------- scheduler
test("covers", () => {
  assert.ok(covers("God so loved the world.", "God so loved"));
  assert.ok(!covers("God so loved the world.", "God so loves"));
  assert.ok(!covers("God", "God so loved"));
  assert.ok(!covers("Anything", ""));
});

test("drafts then final", async () => {
  const finals = [], drafts = [];
  const mt = new FakeMt();
  const w = new Translations({ mt, src: "en", tgts: ["th", "zh"], glossary: {},
    onFinal: (...a) => finals.push(a), onDraft: (...a) => drafts.push(a) });
  w.wantDraft(0, "God so");                     // too short: no draft
  w.wantDraft(0, "God so loved");
  assert.ok(await waitFor(() => drafts.length === 2));
  assert.deepEqual(new Set(drafts.map((d) => d[1])), new Set(["th", "zh"]));
  assert.equal(drafts[0][0], 0);
  w.wantDraft(0, "God so loved the");           // only one new word: skipped
  await sleep(50);
  assert.equal(drafts.length, 2);
  assert.ok(w.draftCovers(0, "God so loved the world."));
  w.addLine(1, 0, "God so loved the world.");
  assert.ok(await waitFor(() => finals.length === 2));
  assert.equal(finals[0][0], 1);
  assert.equal(finals[0][2], "th:GOD SO LOVED THE WORLD.");
  const last = mt.calls[mt.calls.length - 1];
  assert.equal(last[0], "final");
  assert.equal(last[3], true);                  // kept the draft's start
  await w.close(100);
});

test("a stale draft never reaches the screen", async () => {
  const finals = [], drafts = [];
  const w = new Translations({ mt: new FakeMt(200), src: "en", tgts: ["th"], glossary: {},
    onFinal: (...a) => finals.push(a), onDraft: (...a) => drafts.push(a) });
  w.wantDraft(0, "He went up the");
  await sleep(50);                              // draft is being made...
  w.addLine(7, 0, "He walked home.");           // ...but the line ended differently
  assert.ok(await waitFor(() => finals.length === 1));
  await sleep(100);
  assert.deepEqual(drafts, []);
  await w.close(100);
});

test("languages change while live", async () => {
  const finals = [];
  const w = new Translations({ mt: new FakeMt(), src: "en", tgts: ["th"], glossary: {},
    onFinal: (...a) => finals.push(a), onDraft: () => {} });
  w.setTargets(["th", "zh"]);
  w.addLine(1, 0, "Amen.");
  assert.ok(await waitFor(() => finals.length === 2));
  await w.close(100);
});

test("a translator that falls behind skips lines that have left the screen", async () => {
  const finals = [];
  const mt = new FakeMt(100);
  const w = new Translations({ mt, src: "en", tgts: ["th"], glossary: {}, onFinal: (...a) => finals.push(a),
    onDraft: () => {}, staleAfter: () => 3 });
  for (let seg = 0; seg < 6; seg++) w.addLine(100 + seg, seg, `Line ${seg}.`);   // all at once: line 0 is being done
  await w.close(3000);
  // Line 0 was already being translated; 1 and 2 had 3+ newer lines by then; 3, 4, 5 are still on screen.
  assert.deepEqual(finals.map((f) => f[0]), [100, 103, 104, 105]);
  assert.equal(w.skipped, 2);
});

test("a translator that isn't ready yet: the line shows in the original only, quietly", async () => {
  const finals = [], errors = [];
  const mt = { translate: async () => { const e = new Error("not ready"); e.notReady = true; throw e; } };
  const w = new Translations({ mt, src: "en", tgts: ["th"], glossary: {}, onFinal: (...a) => finals.push(a),
    onDraft: () => {}, onError: (e) => errors.push(e) });
  w.addLine(1, 0, "Amen.");
  await w.close(500);
  assert.deepEqual(finals, []);
  assert.deepEqual(errors, []);
});

// ---------------------------------------------------------------------------- hub
test("hub: lines, drafts and translations", () => {
  const h = new Hub({ langs: { src: "en", tgts: ["th", "zh"] } });
  try {
    h.publish({ type: "partial", text: "God so loved", seg: 0 });
    h.publish({ type: "draft", seg: 0, lang: "th", text: "พระเจ้าทรงรัก", src: "God so loved" });
    assert.deepEqual(h.snapshot().drafts, { th: "พระเจ้าทรงรัก" });
    h.publish({ type: "line", id: 10, seg: 0, src: "God so loved the world.", keepDraft: true });
    let snap = h.snapshot();
    assert.deepEqual(snap.lines[0].draft, { th: "พระเจ้าทรงรัก" });
    assert.deepEqual(snap.drafts, {});
    assert.equal(snap.seg, 1);
    h.publish({ type: "draft", seg: 0, lang: "zh", text: "神爱", src: "God so loved" });   // late draft for that line
    h.publish({ type: "translation", id: 10, lang: "th", dst: "พระเจ้าทรงรักโลก" });
    const line = h.snapshot().lines[0];
    assert.deepEqual(line.dst, { th: "พระเจ้าทรงรักโลก" });
    assert.equal(line.draft.zh, "神爱");
    h.publish({ type: "partial", text: "", seg: 1 });
    h.publish({ type: "draft", seg: 5, lang: "th", text: "x", src: "x" });     // not the current line: ignored
    assert.deepEqual(h.snapshot().drafts, {});
  } finally { h.close(); }
});

test("hub: old one-language events", () => {
  const h = new Hub({ langs: { src: "en", tgt: "th" } });
  try {
    assert.deepEqual(h.langs, { src: "en", tgts: ["th"] });
    h.publish({ type: "line", id: 1, src: "Hello.", lag: 1.0 });
    h.publish({ type: "translation", id: 1, dst: "สวัสดี", lag: 2.0 });     // no `lang`
    assert.deepEqual(h.snapshot().lines[0].dst, { th: "สวัสดี" });
    h.publish({ type: "langs", src: "en", tgt: "zh" });
    assert.deepEqual(h.langs.tgts, ["zh"]);
  } finally { h.close(); }
});

test("hub: a screen that opens late gets the names and everything on screen", async () => {
  const h = new Hub({ langs: { src: "en", tgts: ["th"] }, names: publicList() });
  const screen = new BroadcastChannel("riah-live");
  const got = [];
  screen.onmessage = (m) => got.push(m.data);
  try {
    h.publish({ type: "line", id: 1, seg: 0, src: "Amen." });
    screen.postMessage({ type: "hello" });
    assert.ok(await waitFor(() => got.some((m) => m.type === "snapshot")));
    assert.ok(got.some((m) => m.type === "names" && m.names.length));
    assert.equal(got.find((m) => m.type === "snapshot").lines[0].src, "Amen.");
    h.publish({ type: "level", rms: 0.1 });       // operator-only: never sent to the screen
    await sleep(30);
    assert.ok(!got.some((m) => m.type === "level"));
  } finally { screen.close(); h.close(); }
});

test("languages", () => {
  assert.equal(lang("th").name, "Thai");
  assert.ok(publicList().some((l) => l.code === "zh-TW"));
});

// ---------------------------------------------------------------------------- church words
test("church words", () => {
  const g = churchGlossary({ hints: "Pastor Joel", fix: { th: [["ก", "ข"]] } });
  assert.equal(g.hints, "Pastor Joel");
  assert.deepEqual(g.fix.th[g.fix.th.length - 1], ["ก", "ข"]);
  assert.equal(phraseKey("In Jesus' name, amen!"), "in jesus name amen");
  assert.equal(exactPhrase("Amen.", "zh", g), "阿们");
  assert.equal(exactPhrase("Church.", "th", g), "คริสตจักร");
  assert.equal(exactPhrase("Hallelujah!", "th", g), "ฮาเลลูยา");
  assert.equal(exactPhrase("Amen, brothers and sisters.", "th", g), null);
  const th = churchGlossary().fix.th;
  assert.equal(applyFixes("พระเยซูพูดกับนิโกรดมัส", th), "พระเยซูพูดกับนิโคเดมัส");
  assert.ok(!applyFixes("นิโกร", th).includes("นิโกร"));
  assert.equal(applyFixes("อรุณสวัสดิ์ ชอร์ช", th), "อรุณสวัสดิ์ คริสตจักร");
});

test("tidy", () => {
  assert.equal(tidy("เปาโล เป็น อัครสาวก", "th"), "เปาโลเป็นอัครสาวก");
  const phrase = "สุภาษิตบอกว่า ความกลัวพระเจ้า คือจุดเริ่มต้นของความฉลาด";
  assert.equal(tidy(phrase, "th"), phrase);
  assert.equal(tidy("因为众人都得罪,脱离了神的荣耀.", "zh"), "因为众人都得罪，脱离了神的荣耀。");
  assert.equal(tidy("约翰福音3.16", "zh"), "约翰福音3.16");
});

test("half translations", () => {
  assert.ok(tooShort("For God so loved the world, that he gave his only Son.", "เพราะพระเจ้า", "th"));
  assert.deepEqual(halves("For God so loved the world, that he gave his only Son."),
    ["For God so loved the world,", "that he gave his only Son."]);
  assert.equal(halves("Amen, amen."), null);
  assert.equal(joinHalves("神爱世人。", "甚至将他的独生子赐给他们", "zh"), "神爱世人，甚至将他的独生子赐给他们");
});

// ---------------------------------------------------------------------------- Bible names
test("Bible names (made-up list)", () => {
  const bw = new BibleWords({
    names: { Zacchaeus: { th: "ศักเคียส", zh: "撒该", "zh-TW": "撒該" },
      Jesus: { th: "พระเยซู", zh: "耶稣" }, Jews: { th: "ชาวยิว" },
      Lot: { th: "โลท" }, John: { th: "ยอห์น", zh: "约翰" },
      Jordan: { zh: "约旦河" }, Galatians: { th: "ชาวกาลาเทีย" } },
    books: [{ code: "JHN", en: "John", num: null, say: ["John"], th: "ยอห์น", zh: "约翰福音" },
      { code: "1CO", en: "Corinthians", num: "1", say: ["Corinthians"], th: "1 โครินธ์", zh: "哥林多前书" },
      { code: "GAL", en: "Galatians", num: null, say: ["Galatians"], th: "กาลาเทีย", zh: "加拉太书" }],
    miss: { th: { Zacchaeus: ["แซคเคียส"], Lot: ["ล็อต"] },
      zh: { Zacchaeus: ["扎凯"], JHN: ["约翰"], "1CO": ["哥林多书"] } },
  });
  const fix = (a, b, t) => bw.fix(a, b, t);
  assert.equal(fix("Zacchaeus climbed a tree.", "แซคเคียสปีนต้นไม้", "th"), "ศักเคียสปีนต้นไม้");
  assert.equal(fix("Zacchaeus climbed a tree.", "扎凯爬上了树。", "zh"), "撒该爬上了树。");
  assert.equal(fix("Zacchaeus climbed a tree.", "Zacchaeus ปีนต้นไม้", "th"), "ศักเคียส ปีนต้นไม้");
  assert.equal(fix("He climbed a tree.", "แซคเคียสปีนต้นไม้", "th"), "แซคเคียสปีนต้นไม้");
  assert.equal(fix("Jesus wept.", "เยซูร้องไห้", "th"), "พระเยซูร้องไห้");
  assert.equal(fix("Jesus wept.", "พระเยซูร้องไห้", "th"), "พระเยซูร้องไห้");
  assert.equal(fix("The Jews came.", "คนยิวมา", "th"), "คนยิวมา");
  assert.equal(fix("Lot's wife looked back.", "ภรรยาของล็อตมองกลับไป", "th"), "ภรรยาของโลทมองกลับไป");
  assert.equal(fix("a lot of people", "ล็อตของคน", "th"), "ล็อตของคน");
  assert.equal(fix("They crossed the Jordan.", "他们过了约旦。", "zh"), "他们过了约旦。");
  assert.equal(fix("Open to John 3:16.", "打开约翰3:16。", "zh"), "打开约翰福音3:16。");
  assert.equal(fix("John said this.", "约翰说了这话。", "zh"), "约翰说了这话。");
  assert.equal(fix("Read First Corinthians 13.", "读哥林多书13章。", "zh"), "读哥林多前书13章。");
  assert.equal(fix("Galatians 2:20 says this.", "กาลาเทีย 2:20 กล่าวว่า", "th"), "กาลาเทีย 2:20 กล่าวว่า");
  assert.equal(fix("Paul wrote to the Galatians.", "เปาโลเขียนถึงกาลาเทีย", "th"), "เปาโลเขียนถึงชาวกาลาเทีย");
  assert.equal(fix("Let us pray.", "让我们祈祷吧。", "zh"), "让我们祷告吧。");
  assert.equal(fix("We worship Him.", "เราบูชาพระองค์", "th"), "เรานมัสการพระองค์");
  assert.equal(fix("They offered a sacrifice.", "พวกเขาถวายบูชายัญ", "th"), "พวกเขาถวายบูชายัญ");
  assert.equal(fix("Jesus wept.", "Jesus wept.", "en"), "Jesus wept.");
  assert.deepEqual(bw.hints("Zacchaeus met Jesus.", "th"), [["Zacchaeus", "ศักเคียส"], ["Jesus", "พระเยซู"]]);
});

test("Bible names (the real list)", () => {
  const doc = JSON.parse(readFileSync(fileURLToPath(new URL("riah_translate/bible_words.json", ROOT)), "utf8"));
  const real = new BibleWords(doc);
  const f = (a, b, t) => real.fix(a, b, t);
  assert.equal(real.names.Jesus.th, "พระเยซู");
  assert.equal(real.names.Moses.zh, "摩西");
  assert.equal(f("Then Moses went home.", "แล้วมูซาก็กลับบ้าน", "th"), "แล้วโมเสสก็กลับบ้าน");
  assert.equal(f("Nicodemus had questions.", "尼哥底摩有问题，", "zh"), "尼哥德慕有问题，");
  assert.equal(f("A man named Nicodemus.", "一个叫尼哥底马的老师", "zh"), "一个叫尼哥德慕的老师");
  assert.equal(f("Then Hebron went home.", "然后希伯回家了。", "zh"), "然后希伯回家了。");
  assert.equal(f("David went home.", "大为回家了。", "zh"), "大为回家了。");
  assert.equal(f("In verse 16, Jesus says this.", "ในบทที่ 16 พระเยซูตรัสว่า", "th"), "ในข้อ 16 พระเยซูตรัสว่า");
  assert.equal(f("In verse 16, Jesus says", "ในบทความ 16 พระเยซูบอกว่า", "th"), "ในข้อ 16 พระเยซูบอกว่า");
  assert.equal(f("Chapter 16 says this.", "บทที่ 16 กล่าวว่า", "th"), "บทที่ 16 กล่าวว่า");
  assert.equal(f("John chapter 3 verse 3.", "ยอห์น บทที่ 3 ข้อ 3", "th"), "ยอห์น บทที่ 3 ข้อ 3");
  assert.equal(f("In verse 5 we see it.", "在第5章我们看到。", "zh"), "在第5节我们看到。");
  assert.equal(f("Open to the Gospel of John, chapter 3.", "เปิดไปยังพระวจนะของยอห์น บทที่ 3", "th"),
    "เปิดไปยังพระกิตติคุณยอห์น บทที่ 3");
  assert.equal(f("Please open your Bibles with me to the Gospel of John.",
    "โปรดเปิดพระคัมภีร์ของคุณกับผมในวัสดุยอห์น", "th"), "โปรดเปิดพระคัมภีร์ของคุณกับผมในพระกิตติคุณยอห์น");
  assert.equal(f("Turn to the Gospel of Luke.", "เปิดไปที่ลูกา", "th"), "เปิดไปที่พระกิตติคุณลูกา");
  assert.equal(f("John wrote the Gospel of John.", "ยอห์นเขียนข่าวประเสริฐของยอห์น", "th"),
    "ยอห์นเขียนข่าวประเสริฐของยอห์น");
  assert.equal(f("Carry that love into Chiang Mai.", "นำความรักนั้น เข้าสู่เมืองชางไม", "th"),
    "นำความรักนั้น เข้าสู่เมืองเชียงใหม่");
  assert.equal(f("Into Chiang Mai.", "เข้าสู่เชียงใหม่", "th"), "เข้าสู่เชียงใหม่");
  assert.equal(f("Nicodemus had questions.", "นิโกรธีโมสมีคำถาม", "th"), "นิโคเดมัสมีคำถาม");
  assert.equal(f("Nicodemus came.", "นิโคเดมัสมา", "th"), "นิโคเดมัสมา");
  assert.equal(f("Chapter 3 3.", "บทที่ 33", "th"), "บทที่ 3");
  assert.equal(f("Please open your Bibles with me to the Gospel of John.",
    "กรุณาเปิดพระคัมภีร์ ของคุณกับผม ไปยังพระวจนะของจอห์น", "th"),
    "กรุณาเปิดพระคัมภีร์ ของคุณกับผม ไปยังพระกิตติคุณยอห์น");
  assert.equal(f("Jesus is talking with a man named Nicodemus, a teacher who came to him at night.",
    "耶稣正在和一个叫尼哥底马的老师说话，尼哥德慕是一个晚上来见他的老师。", "zh"),
    "耶稣正在和一个叫尼哥德慕的老师说话，尼哥德慕是一个晚上来见他的老师。");
  assert.equal(f("Notice that the verse begins with love.", "สังเกตว่าบทนี้เริ่มด้วยความรัก", "th"),
    "สังเกตว่าข้อนี้เริ่มด้วยความรัก");
  assert.equal(f("Let us pray together.", "ขอให้เราภาวนาด้วยกัน", "th"), "ขอให้เราอธิษฐานด้วยกัน");
  assert.equal(f("Chapter 33.", "บทที่ 33", "th"), "บทที่ 33");
  const line = "So when we leave this building today, let us carry that love into Chiang Mai.";
  assert.equal(f(line, "所以当我们今天离开这楼时，让我们把爱带到迈。", "zh"), "所以当我们今天离开这楼时，让我们把爱带到清迈。");
  assert.equal(f(line, "让我们把爱带到长迈", "zh"), "让我们把爱带到清迈");
  assert.equal(f(line, "让我们把爱带到清迈。", "zh"), "让我们把爱带到清迈。");
  assert.equal(f("Step out into Chiang Mai.", "迈出去，到清迈。", "zh"), "迈出去，到清迈。");
  assert.equal(f("Mike went to Chiang Mai.", "迈克去了迈。", "zh"), "迈克去了清迈。");
  assert.equal(f("Step into Chiang Mai.", "迈入清迈", "zh"), "迈入清迈");
  assert.equal(f("Wright drove to Chiang Rai.", "莱特开车去了莱。", "zh"), "莱特开车去了清莱。");
});

// ---------------------------------------------------------------------------- speech
test("whisper word helpers", () => {
  const prev = { text: " son.", start: 1.0, end: 1.4 };
  assert.deepEqual(dropEchoes([{ text: " on.", start: 1.45, end: 1.6 }, { text: " .", start: 1.6, end: 1.7 }], prev), []);
  assert.ok(soundsLike("night", "right"));
  assert.ok(!soundsLike("son", "sun"));       // short words must match exactly
  const w = (t) => ({ text: t, start: 0, end: 0 });
  assert.deepEqual(dropRepeats([" good", " people.", " people."].map(w)).map((x) => x.text), [" good", " people."]);
});

test("speech spans from VAD probabilities (faster-whisper's rules)", () => {
  // 1 s silence, 2 s speech, 3 s silence, 1 s speech: windows of 512 samples.
  const win = (s) => Math.round(s * SR / 512);
  const probs = [...Array(win(1)).fill(0.05), ...Array(win(2)).fill(0.9), ...Array(win(3)).fill(0.05), ...Array(win(1)).fill(0.9)];
  const len = probs.length * 512;
  const spans = speechSegments(probs, { length: len, minSilenceMs: 500, speechPadMs: 300 });
  assert.equal(spans.length, 2);
  assert.ok(Math.abs(spans[0].start / SR - (1 - 0.3)) < 0.05, JSON.stringify(spans));
  assert.ok(Math.abs(spans[0].end / SR - (3 + 0.3)) < 0.05, JSON.stringify(spans));
  const glued = collectChunks(new Float32Array(len), spans);
  assert.equal(glued.length, spans.reduce((n, s) => n + s.end - s.start, 0));
  // A word in the second span maps back past the silence that was cut out.
  const map = new SpeechMap(spans, SR);
  const firstLen = (spans[0].end - spans[0].start) / SR;
  const back = map.word({ text: " amen", start: firstLen + 0.4, end: firstLen + 0.6 });
  assert.ok(Math.abs(back.start - (spans[1].start / SR + 0.4)) < 0.02, JSON.stringify(back));
});

test("word timings from token timings", () => {
  // Tokens: " God", " so", " loved", " the", " world", "."; eot = 100.
  const vocab = { 1: " God", 2: " so", 3: " loved", 4: " the", 5: " world", 6: "." };
  const decode = (ts) => ts.map((t) => vocab[t] || "").join("");
  const [words] = splitOnSpaces([1, 2, 3, 4, 5, 6, 100], decode, 100);
  assert.deepEqual(words, [" God", " so", " loved", " the", " world", ".", ""]);
  const jumps = [0.0, 0.3, 0.5, 0.9, 1.1, 1.5, 1.52];
  const out = wordTimings([1, 2, 3, 4, 5, 6], (k) => jumps[k], decode, 100);
  assert.deepEqual(out.map((w) => w.text), [" God", " so", " loved", " the", " world."]);
  assert.equal(out[4].start, 1.1);
  const a = [{ word: " \"", tokens: [1] }, { word: "Hi", tokens: [2] }, { word: ",", tokens: [3] }];
  mergePunctuations(a);
  assert.deepEqual(a.map((x) => x.word), ["", " \"Hi,", ""]);
});

// A pretend Whisper: says the scripted words that fall inside the audio it is given.
function fakeSpeaker(script) {
  let st = null;
  const transcribe = async (audio) => {
    const from = st.offset, to = from + audio.length / SR;
    return script.filter((w) => w.start >= from - 0.01 && w.end <= to + 0.01)
      .map((w) => ({ text: w.text, start: w.start - from, end: w.end - from }));
  };
  return { transcribe, attach: (s) => { st = s; } };
}

function scriptAndAudio() {
  const script = [];
  let t = 0.5;
  for (const word of "Good morning, everyone.".split(" ")) { script.push({ text: " " + word, start: t, end: t + 0.3 }); t += 0.35; }
  t += 1.6;                                     // a pause
  const long = Array.from({ length: 26 }, (_, i) => ["grace", "and", "peace", "to", "you", "from", "God", "our", "Father"][i % 9]);
  long[long.length - 1] = "Father.";
  for (const word of long) { script.push({ text: " " + word, start: t, end: t + 0.25 }); t += 0.3; }
  t += 1.5;
  const total = t;
  const audio = new Float32Array(Math.ceil(total * SR));
  for (const w of script) {
    for (let i = Math.floor(w.start * SR); i < Math.floor(w.end * SR); i++) audio[i] = 0.2 * Math.sin(i / 7);
  }
  return { script, audio };
}

test("streaming: lines end at a full stop, a pause or 22 words", async () => {
  const { script, audio } = scriptAndAudio();
  const sp = fakeSpeaker(script);
  const st = new StreamingTranscriber({ transcribe: sp.transcribe });
  sp.attach(st);
  const lines = [], partials = [];
  const step = SR / 2;
  for (let i = 0; i < audio.length; i += step) {
    st.push(audio.subarray(i, Math.min(audio.length, i + step)));
    const r = await st.step(false);
    lines.push(...r.lines);
    if (r.partial) partials.push(r.partial);
  }
  lines.push(...(await st.step(true)).lines);
  const texts = lines.map((l) => l.text);
  assert.equal(texts[0], "Good morning, everyone.");
  assert.ok(partials.length > 0, "partials shown while words are heard");
  const counts = texts.slice(1).map((s) => s.split(" ").length);
  assert.ok(Math.max(...counts) <= 22, texts.join(" | "));
  assert.equal(counts.reduce((a, b) => a + b, 0), 26, texts.join(" | "));
  assert.ok(texts[texts.length - 1].endsWith("Father."));
  for (const l of lines) assert.ok(l.end >= l.start);
});

test("silence alone never reaches Whisper", async () => {
  let calls = 0;
  const st = new StreamingTranscriber({ transcribe: async () => { calls++; return []; } });
  st.push(new Float32Array(3 * SR));
  const r = await st.step(false);
  assert.equal(calls, 0);
  assert.deepEqual(r.lines, []);
  assert.ok(silentTailByLoudness(new Float32Array(2 * SR)) >= 1.9);
});

test("the operator's words go first in Whisper's prompt", () => {
  const st = new StreamingTranscriber({ transcribe: async () => [], hints: "Pastor Somchai, Nicodemus" });
  assert.ok(st.prompt().startsWith("Pastor Somchai, Nicodemus"));
  st.history = ["x".repeat(600)];
  assert.ok(st.prompt().length <= 400 + 30);
  assert.ok(st.prompt().startsWith("Pastor Somchai"));
});

// ---------------------------------------------------------------------------- sermon notes
function zip(name, text) {
  // One deflated file in a zip, the way Word saves word/document.xml.
  const data = Buffer.from(text, "utf8");
  const comp = deflateRawSync(data);
  const nameB = Buffer.from(name);
  const local = Buffer.alloc(30);
  local.writeUInt32LE(0x04034b50, 0); local.writeUInt16LE(20, 4); local.writeUInt16LE(8, 8);
  local.writeUInt32LE(comp.length, 18); local.writeUInt32LE(data.length, 22); local.writeUInt16LE(nameB.length, 26);
  const central = Buffer.alloc(46);
  central.writeUInt32LE(0x02014b50, 0); central.writeUInt16LE(20, 4); central.writeUInt16LE(20, 6);
  central.writeUInt16LE(8, 10); central.writeUInt32LE(comp.length, 20); central.writeUInt32LE(data.length, 24);
  central.writeUInt16LE(nameB.length, 28); central.writeUInt32LE(0, 42);
  const cdOffset = 30 + nameB.length + comp.length;
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0); end.writeUInt16LE(1, 8); end.writeUInt16LE(1, 10);
  end.writeUInt32LE(46 + nameB.length, 12); end.writeUInt32LE(cdOffset, 16);
  return new Uint8Array(Buffer.concat([local, nameB, comp, central, nameB, end]));
}

test("sermon notes: names from a Word file", async () => {
  const xml = '<w:document><w:body><w:p><w:r><w:t>Welcome to Grace Church in Chiang Mai.</w:t></w:r></w:p>' +
    "<w:p><w:r><w:t>Read John 3 with Pastor Somchai. Nicodemus came at night.</w:t></w:r></w:p>" +
    "<w:p><w:r><w:t>Point 1: Nicodemus asks. Jesus answers &amp; loves.</w:t></w:r></w:p></w:body></w:document>";
  const text = await docxText(zip("word/document.xml", xml));
  assert.ok(text.includes("Grace Church in Chiang Mai."));
  assert.ok(text.includes("answers & loves"));
  const bible = fileURLToPath(new URL("riah_translate/bible_words.json", ROOT));
  const realFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(readFileSync(bible));
  let names;
  try { names = await namesFrom(text, "bible_words.json"); } finally { globalThis.fetch = realFetch; }
  assert.equal(names[0], "Nicodemus");          // a Bible name, said twice
  for (const n of ["Grace Church", "Chiang Mai", "Pastor Somchai", "John"]) assert.ok(names.includes(n), names.join(", "));
  for (const n of ["Welcome", "Read", "Point", "Jesus", "Chiang", "Mai"]) assert.ok(!names.includes(n), names.join(", "));
  assert.ok(names.join(", ").length <= 200);
});

test("sermon notes: the operator's words always come first", () => {
  assert.equal(joinHints("Pastor Joel, Chiang Mai", ["Chiang Mai", "Nicodemus"]), "Pastor Joel, Chiang Mai, Nicodemus");
  assert.equal(joinHints("", ["Nicodemus"]), "Nicodemus");
  assert.ok(joinHints("a".repeat(230), ["Nicodemus", "Zacchaeus"]).length <= 240);
});

// ---------------------------------------------------------------------------- run
let failed = 0;
for (const [name, fn] of tests) {
  try {
    await fn();
    console.log(`ok    ${name}`);
  } catch (e) {
    failed++;
    console.log(`FAIL  ${name}\n      ${String(e && e.stack || e).split("\n").slice(0, 4).join("\n      ")}`);
  }
}
console.log(failed ? `${failed} of ${tests.length} failed` : `all ${tests.length} passed`);
process.exit(failed ? 1 : 0);
