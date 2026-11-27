"""Fast checks with no models: the screen state (hub), the draft/final translation worker, church
words and script tidy-ups. Safe to run anywhere:

    python tests/test_logic.py
"""
import re
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from riah_translate.church_words import church_glossary, phrase_key  # noqa: E402
from riah_translate.engine import Translations, covers  # noqa: E402
from riah_translate.hub import Hub  # noqa: E402
from riah_translate.mt import EchoTranslator, exact_phrase, tidy  # noqa: E402


class FakeMt:
    """Upper-cases the text; remembers what it was asked."""
    drafts = True

    def __init__(self, delay=0.0):
        self.calls, self.delay = [], delay

    def translate_multi(self, text, src, tgts, context=None, glossary=None, keep=None, draft=False):
        self.calls.append(("draft" if draft else "final", text, tuple(tgts), bool(keep)))
        time.sleep(self.delay)
        return {t: (f"{t}:{text.upper()}", list(text.upper())) for t in tgts}


def wait_for(cond, secs=3):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_covers():
    assert covers("God so loved the world.", "God so loved")
    assert not covers("God so loved the world.", "God so loves")
    assert not covers("God", "God so loved")
    assert not covers("Anything", "")


def test_worker_drafts_then_final():
    finals, drafts = [], []
    mt = FakeMt()
    w = Translations(mt, "en", ["th", "zh"], {}, lambda *a: finals.append(a), lambda *a: drafts.append(a))
    w.want_draft(0, "God so")                    # too short: no draft
    w.want_draft(0, "God so loved")
    assert wait_for(lambda: len(drafts) == 2)
    assert {d[1] for d in drafts} == {"th", "zh"} and drafts[0][0] == 0
    w.want_draft(0, "God so loved the")          # only one new word: skipped
    time.sleep(0.05)
    assert len(drafts) == 2
    assert w.draft_covers(0, "God so loved the world.")
    w.add_line(1, 0, "God so loved the world.")
    assert wait_for(lambda: len(finals) == 2)
    assert finals[0][0] == 1 and finals[0][2] == "th:GOD SO LOVED THE WORLD."
    assert mt.calls[-1][0] == "final" and mt.calls[-1][3] is True      # kept the draft's start
    w.close()


def test_worker_drops_stale_draft():
    finals, drafts = [], []
    mt = FakeMt()
    started, go_on = threading.Event(), threading.Event()
    real = mt.translate_multi

    def held(text, src, tgts, draft=False, **kw):  # events, not sleeps: a slow CI Mac oversleeps
        if draft:
            started.set()
            go_on.wait(5)
        return real(text, src, tgts, draft=draft, **kw)

    mt.translate_multi = held
    w = Translations(mt, "en", ["th"], {}, lambda *a: finals.append(a), lambda *a: drafts.append(a))
    w.want_draft(0, "He went up the")
    assert started.wait(5)                        # draft is being made...
    w.add_line(7, 0, "He walked home.")          # ...but the line ended differently
    go_on.set()
    assert wait_for(lambda: len(finals) == 1)
    w.close()
    assert drafts == []                           # the wrong draft never reached the screen


def test_worker_set_targets():
    finals = []
    w = Translations(FakeMt(), "en", ["th"], {}, lambda *a: finals.append(a), lambda *a: None)
    w.set_targets(["th", "zh"])
    w.add_line(1, 0, "Amen.")
    assert wait_for(lambda: len(finals) == 2)
    w.close()


def test_hub_lines_drafts_translations():
    h = Hub(langs=dict(src="en", tgts=["th", "zh"]))
    h.publish(dict(type="partial", text="God so loved", seg=0))
    h.publish(dict(type="draft", seg=0, lang="th", text="พระเจ้าทรงรัก", src="God so loved"))
    assert h.snapshot()["drafts"] == {"th": "พระเจ้าทรงรัก"}
    h.publish(dict(type="line", id=10, seg=0, src="God so loved the world.", keepDraft=True))
    snap = h.snapshot()
    assert snap["lines"][0]["draft"] == {"th": "พระเจ้าทรงรัก"} and snap["drafts"] == {} and snap["seg"] == 1
    h.publish(dict(type="draft", seg=0, lang="zh", text="神爱", src="God so loved"))   # late draft for that line
    h.publish(dict(type="translation", id=10, lang="th", dst="พระเจ้าทรงรักโลก"))
    line = h.snapshot()["lines"][0]
    assert line["dst"] == {"th": "พระเจ้าทรงรักโลก"} and line["draft"]["zh"] == "神爱"
    h.publish(dict(type="partial", text="", seg=1))
    h.publish(dict(type="draft", seg=5, lang="th", text="x", src="x"))     # not the current line: ignored
    assert h.snapshot()["drafts"] == {}


def test_hub_old_recordings():
    h = Hub(langs=dict(src="en", tgt="th"))
    assert h.langs == dict(src="en", tgts=["th"])
    h.publish(dict(type="line", id=1, src="Hello.", lag=1.0))
    h.publish(dict(type="translation", id=1, dst="สวัสดี", lag=2.0))    # no `lang`
    assert h.snapshot()["lines"][0]["dst"] == {"th": "สวัสดี"}
    h.publish(dict(type="langs", src="en", tgt="zh"))
    assert h.langs["tgts"] == ["zh"]


def test_church_words():
    g = church_glossary(dict(hints="Pastor Joel", fix={"th": [["ก", "ข"]]}))
    assert g["hints"] == "Pastor Joel" and g["fix"]["th"][-1] == ["ก", "ข"]
    assert phrase_key("In Jesus' name, amen!") == "in jesus name amen"
    assert exact_phrase("Amen.", "zh", g) == "阿们"
    assert exact_phrase("Church.", "th", g) == "คริสตจักร"           # the people, not the building (โบสถ์)
    assert exact_phrase("Hallelujah!", "th", g) == "ฮาเลลูยา"
    assert exact_phrase("Amen, brothers and sisters.", "th", g) is None
    from riah_translate.mt import apply_fixes
    assert apply_fixes("พระเยซูพูดกับนิโกรดมัส", church_glossary()["fix"]["th"]) == "พระเยซูพูดกับนิโคเดมัส"
    assert "นิโกร" not in apply_fixes("นิโกร", church_glossary()["fix"]["th"])
    assert apply_fixes("อรุณสวัสดิ์ ชอร์ช", church_glossary()["fix"]["th"]) == "อรุณสวัสดิ์ คริสตจักร"
    out = EchoTranslator().translate_multi("Amen.", "en", ["th"], glossary=g)
    assert out["th"][0] == "Amen."               # echo shows the original, untouched


def test_bible_words():
    import json
    import tempfile
    from riah_translate.bible_words import BibleWords
    doc = dict(
        names={"Zacchaeus": dict(th="ศักเคียส", zh="撒该", **{"zh-TW": "撒該"}),
               "Jesus": dict(th="พระเยซู", zh="耶稣"), "Jews": dict(th="ชาวยิว"),
               "Lot": dict(th="โลท"), "John": dict(th="ยอห์น", zh="约翰"),
               "Jordan": dict(zh="约旦河"), "Galatians": dict(th="ชาวกาลาเทีย")},
        books=[dict(code="JHN", en="John", num=None, say=["John"], th="ยอห์น", zh="约翰福音"),
               dict(code="1CO", en="Corinthians", num="1", say=["Corinthians"], th="1 โครินธ์", zh="哥林多前书"),
               dict(code="GAL", en="Galatians", num=None, say=["Galatians"], th="กาลาเทีย", zh="加拉太书")],
        miss={"th": {"Zacchaeus": ["แซคเคียส"], "Lot": ["ล็อต"]},
              "zh": {"Zacchaeus": ["扎凯"], "JHN": ["约翰"], "1CO": ["哥林多书"]}})
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "w.json"
        p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        bw = BibleWords(str(p))
    fix = bw.fix
    assert fix("Zacchaeus climbed a tree.", "แซคเคียสปีนต้นไม้", "th") == "ศักเคียสปีนต้นไม้"
    assert fix("Zacchaeus climbed a tree.", "扎凯爬上了树。", "zh") == "撒该爬上了树。"
    assert fix("Zacchaeus climbed a tree.", "Zacchaeus ปีนต้นไม้", "th") == "ศักเคียส ปีนต้นไม้"  # left in English
    assert fix("He climbed a tree.", "แซคเคียสปีนต้นไม้", "th") == "แซคเคียสปีนต้นไม้"   # name not said
    assert fix("Jesus wept.", "เยซูร้องไห้", "th") == "พระเยซูร้องไห้"
    assert fix("Jesus wept.", "พระเยซูร้องไห้", "th") == "พระเยซูร้องไห้"
    assert fix("The Jews came.", "คนยิวมา", "th") == "คนยิวมา"                 # คนยิว is fine as it is
    assert fix("Lot's wife looked back.", "ภรรยาของล็อตมองกลับไป", "th") == "ภรรยาของโลทมองกลับไป"
    assert fix("a lot of people", "ล็อตของคน", "th") == "ล็อตของคน"           # lower-case "lot" isn't the name
    assert fix("They crossed the Jordan.", "他们过了约旦。", "zh") == "他们过了约旦。"  # 约旦 = 约旦河
    assert fix("Open to John 3:16.", "打开约翰3:16。", "zh") == "打开约翰福音3:16。"
    assert fix("John said this.", "约翰说了这话。", "zh") == "约翰说了这话。"   # the man, not the book
    assert fix("Read First Corinthians 13.", "读哥林多书13章。", "zh") == "读哥林多前书13章。"
    assert fix("Galatians 2:20 says this.", "กาลาเทีย 2:20 กล่าวว่า", "th") == "กาลาเทีย 2:20 กล่าวว่า"  # the book
    assert fix("Paul wrote to the Galatians.", "เปาโลเขียนถึงกาลาเทีย", "th") == "เปาโลเขียนถึงชาวกาลาเทีย"
    assert fix("Let us pray.", "让我们祈祷吧。", "zh") == "让我们祷告吧。"
    assert fix("We worship Him.", "เราบูชาพระองค์", "th") == "เรานมัสการพระองค์"
    assert fix("They offered a sacrifice.", "พวกเขาถวายบูชายัญ", "th") == "พวกเขาถวายบูชายัญ"
    assert fix("Jesus wept.", "Jesus wept.", "en") == "Jesus wept."
    assert bw.hints("Zacchaeus met Jesus.", "th") == [("Zacchaeus", "ศักเคียส"), ("Jesus", "พระเยซู")]
    # The real word list loads and knows the big names.
    from riah_translate.bible_words import bible_words
    real = bible_words()
    assert real and real.names["Jesus"]["th"] == "พระเยซู" and real.names["Moses"]["zh"] == "摩西"
    # Its known misspellings are names only: never words from the test sentences, which a swap would drop.
    frame = re.compile(r"回家|圣经|聖經|关于|關於|故事|อ่าน|กลับบ้าน|พระคัมภีร์|เรื่อง|[，、。]")
    stray = [(t, w, m) for t, d in real.miss.items() for w, ms in d.items() for m in ms if frame.search(m)]
    assert not stray, stray[:5]
    assert real.fix("Then Moses went home.", "แล้วมูซาก็กลับบ้าน", "th") == "แล้วโมเสสก็กลับบ้าน"
    # A Chinese name sounded out a little off (seen on the test sermon), and only the whole name swapped.
    assert real.fix("Nicodemus had questions.", "尼哥底摩有问题，", "zh") == "尼哥德慕有问题，"
    assert real.fix("A man named Nicodemus.", "一个叫尼哥底马的老师", "zh") == "一个叫尼哥德慕的老师"
    assert real.fix("Then Hebron went home.", "然后希伯回家了。", "zh") == "然后希伯回家了。"  # 希伯 is Heber
    assert real.fix("David went home.", "大为回家了。", "zh") == "大为回家了。"       # two characters: left alone
    # "verse 16" that came out as "chapter 16", and "the Gospel of John" as "the Word of John".
    assert real.fix("In verse 16, Jesus says this.", "ในบทที่ 16 พระเยซูตรัสว่า", "th") == "ในข้อ 16 พระเยซูตรัสว่า"
    assert real.fix("In verse 16, Jesus says", "ในบทความ 16 พระเยซูบอกว่า", "th") == "ในข้อ 16 พระเยซูบอกว่า"
    assert real.fix("Chapter 16 says this.", "บทที่ 16 กล่าวว่า", "th") == "บทที่ 16 กล่าวว่า"
    assert real.fix("John chapter 3 verse 3.", "ยอห์น บทที่ 3 ข้อ 3", "th") == "ยอห์น บทที่ 3 ข้อ 3"
    assert real.fix("In verse 5 we see it.", "在第5章我们看到。", "zh") == "在第5节我们看到。"
    assert real.fix("Open to the Gospel of John, chapter 3.", "เปิดไปยังพระวจนะของยอห์น บทที่ 3", "th") == \
        "เปิดไปยังพระกิตติคุณยอห์น บทที่ 3"
    # Seen on the Mac run of the test sermon.
    assert real.fix("Please open your Bibles with me to the Gospel of John.",
                    "โปรดเปิดพระคัมภีร์ของคุณกับผมในวัสดุยอห์น", "th") == "โปรดเปิดพระคัมภีร์ของคุณกับผมในพระกิตติคุณยอห์น"
    assert real.fix("Turn to the Gospel of Luke.", "เปิดไปที่ลูกา", "th") == "เปิดไปที่พระกิตติคุณลูกา"
    assert real.fix("John wrote the Gospel of John.", "ยอห์นเขียนข่าวประเสริฐของยอห์น", "th") == \
        "ยอห์นเขียนข่าวประเสริฐของยอห์น"                                  # two Johns: left alone
    assert real.fix("Carry that love into Chiang Mai.", "นำความรักนั้น เข้าสู่เมืองชางไม", "th") == \
        "นำความรักนั้น เข้าสู่เมืองเชียงใหม่"
    assert real.fix("Into Chiang Mai.", "เข้าสู่เชียงใหม่", "th") == "เข้าสู่เชียงใหม่"
    assert real.fix("Nicodemus had questions.", "นิโกรธีโมสมีคำถาม", "th") == "นิโคเดมัสมีคำถาม"
    assert real.fix("Nicodemus came.", "นิโคเดมัสมา", "th") == "นิโคเดมัสมา"
    assert real.fix("Chapter 3 3.", "บทที่ 33", "th") == "บทที่ 3"
    # Seen on the Intel Mac run.
    assert real.fix("Please open your Bibles with me to the Gospel of John.",
                    "กรุณาเปิดพระคัมภีร์ ของคุณกับผม ไปยังพระวจนะของจอห์น", "th") == \
        "กรุณาเปิดพระคัมภีร์ ของคุณกับผม ไปยังพระกิตติคุณยอห์น"
    assert real.fix("Jesus is talking with a man named Nicodemus, a teacher who came to him at night.",
                    "耶稣正在和一个叫尼哥底马的老师说话，尼哥德慕是一个晚上来见他的老师。", "zh") == \
        "耶稣正在和一个叫尼哥德慕的老师说话，尼哥德慕是一个晚上来见他的老师。"
    assert real.fix("Notice that the verse begins with love.", "สังเกตว่าบทนี้เริ่มด้วยความรัก", "th") == \
        "สังเกตว่าข้อนี้เริ่มด้วยความรัก"
    assert real.fix("Let us pray together.", "ขอให้เราภาวนาด้วยกัน", "th") == "ขอให้เราอธิษฐานด้วยกัน"
    assert real.fix("Chapter 33.", "บทที่ 33", "th") == "บทที่ 33"
    # Seen on the Windows and Mac runs: "Chiang Mai" losing half its Chinese name.
    line = "So when we leave this building today, let us carry that love into Chiang Mai."
    assert real.fix(line, "所以当我们今天离开这楼时，让我们把爱带到迈。", "zh") == "所以当我们今天离开这楼时，让我们把爱带到清迈。"
    assert real.fix(line, "让我们把爱带到长迈", "zh") == "让我们把爱带到清迈"
    assert real.fix(line, "让我们把爱带到清迈。", "zh") == "让我们把爱带到清迈。"
    assert real.fix("Step out into Chiang Mai.", "迈出去，到清迈。", "zh") == "迈出去，到清迈。"
    assert real.fix("Mike went to Chiang Mai.", "迈克去了迈。", "zh") == "迈克去了清迈。"
    assert real.fix("Step into Chiang Mai.", "迈入清迈", "zh") == "迈入清迈"
    assert real.fix("Wright drove to Chiang Rai.", "莱特开车去了莱。", "zh") == "莱特开车去了清莱。"


def test_tidy():
    assert tidy("เปาโล เป็น อัครสาวก", "th") == "เปาโลเป็นอัครสาวก"
    phrase = "สุภาษิตบอกว่า ความกลัวพระเจ้า คือจุดเริ่มต้นของความฉลาด"
    assert tidy(phrase, "th") == phrase           # real phrase spaces stay
    assert tidy("因为众人都得罪,脱离了神的荣耀.", "zh") == "因为众人都得罪，脱离了神的荣耀。"
    assert tidy("约翰福音3.16", "zh") == "约翰福音3.16"


def test_transcript():
    import tempfile
    from riah_translate.transcript import Transcript
    with tempfile.TemporaryDirectory() as d:
        tr = Transcript(Path(d))
        h = Hub(langs=dict(src="en", tgts=["th"]))
        h.taps.append(tr.event)
        h.publish(dict(type="line", id=1, seg=0, src="Before start."))      # not recording yet
        path = tr.begin("en", ["th", "zh"])
        h.publish(dict(type="line", id=2, seg=1, src="God so loved the world."))
        h.publish(dict(type="draft", seg=2, lang="th", text="x", src="x"))  # drafts aren't kept
        h.publish(dict(type="translation", id=2, lang="th", dst="พระเจ้าทรงรักโลก"))
        tr.close()
        text = path.read_text(encoding="utf-8")
        assert "Before start." not in text and "God so loved the world." in text
        assert "Thai: พระเจ้าทรงรักโลก" in text and "Thai, Chinese" in text and "x" not in text.split()


def test_slow_computer():
    """Finished lines that keep coming late narrow the translator's search (4 -> 2 -> 1), and the
    speech check falls back small.en -> base.en -> tiny.en."""
    from riah_translate import models
    from riah_translate.mt import NllbTranslator
    mt = NllbTranslator.__new__(NllbTranslator)
    mt.final_beam, mt._slow = 4, 0
    for secs in (2, 2, 0.4, 2, 2):        # a quick line in between resets the count
        mt._pace(secs)
    assert mt.final_beam == 4
    for _ in range(3):
        mt._pace(2)
    assert mt.final_beam == 2
    for _ in range(9):
        mt._pace(2)
    assert mt.final_beam == 1

    # The draft's start is kept unless it reads clearly worse, or the line stopped early with it.
    free, short = list("abcdefghij"), list("abcde")
    assert mt._keep_wins(free, list("abcdefghiz"), (-0.5, -0.55))
    assert not mt._keep_wins(free, list("abcdefghiz"), (-0.5, -0.8))
    assert not mt._keep_wins(free, short, (-0.5, -0.3))
    assert mt._keep_wins(free, free, None)

    # Half a sentence translated: spotted, split at the comma, joined back.
    from riah_translate.mt import too_short, halves, join_halves
    line = "So when we leave this building today, let us carry that love into Chiang Mai."
    assert too_short(line, "让我们把爱带到清迈。", "zh")
    assert not too_short(line, "所以当我们今天离开这栋楼时，让我们把爱带到清迈。", "zh")
    assert too_short("Nicodemus had questions, and Jesus did not send him away.", "นิโคเดมัสมีคำถาม", "th")
    assert not too_short("Good morning, church.", "สวัสดี", "th")              # short lines: never
    assert not too_short(line, "x", "ko")                                     # only where measured
    assert halves(line) == ["So when we leave this building today,", "let us carry that love into Chiang Mai."]
    assert halves("Hear this clearly, church.") is None
    assert join_halves("所以当我们今天离开这楼时，", "让我们把爱带到清迈。", "zh") == "所以当我们今天离开这楼时，让我们把爱带到清迈。"
    assert join_halves("นิโคเดมัสมีคำถาม", "และพระเยซูไม่ได้ขับไล่เขา", "th") == "นิโคเดมัสมีคำถาม และพระเยซูไม่ได้ขับไล่เขา"

    speed = {}
    old = models.load_whisper, models._pass_seconds
    models.load_whisper = lambda name, *a, **k: name
    models._pass_seconds = lambda name: speed[name]
    try:
        for small, base, want in ((0.5, 9, "small.en"), (1.5, 9, "base.en"), (3.0, 0.6, "base.en"), (3.0, 1.2, "tiny.en")):
            speed.update({"small.en": small, "base.en": base})
            assert models.choose_whisper("en") == want, (small, base)
        assert models.choose_whisper("th") != "tiny.en"
    finally:
        models.load_whisper, models._pass_seconds = old


def test_hidden_not_saved():
    from riah_translate.app import DISPLAY_KEYS
    assert "hidden" in DISPLAY_KEYS
    assert Hub().display["hidden"] is False


def test_sound_board_channel():
    """A sound board's USB input: listen to one channel (the preacher's mic) or both of a stereo pair."""
    import types
    import numpy as np
    from riah_translate.app import clean_channel
    from riah_translate.audio import MicSource
    assert [clean_channel(c) for c in (None, True, "x", -1, 64, 0, "3", 5.0)] == [None, None, None, None, None, 0, 3, 5]

    opened = {}

    class FakeStream:
        def __init__(self, **kw):
            opened.update(kw)

        def start(self):
            pass

    fake = types.SimpleNamespace(InputStream=FakeStream,
                                 query_devices=lambda dev, kind: dict(max_input_channels=8, default_samplerate=16000))
    real = sys.modules.get("sounddevice")
    sys.modules["sounddevice"] = fake
    try:
        frames = np.array([[0.1, 0.3, 0.5, 0.7]] * 4, np.float32)   # four channels, four samples
        for channel, chans, want in ((None, 2, 0.2), (2, 3, 0.5), (3, 4, 0.7), (11, 2, 0.2)):
            m = MicSource(None, channel).start()
            assert opened["channels"] == chans, (channel, opened["channels"])
            opened["callback"](frames[:, :chans], 4, None, None)
            got = m.read(0.01)[0]
            assert np.allclose(got, want), (channel, got)
    finally:
        if real is None:
            sys.modules.pop("sounddevice", None)
        else:
            sys.modules["sounddevice"] = real


def test_auto_level():
    """A feed sent far too quiet is brought up to a level Whisper likes; silence is not pumped into hiss."""
    import numpy as np
    from riah_translate.audio import SR, AutoLevel
    lv = AutoLevel()
    t = np.arange(SR // 5) / SR
    quiet = (0.004 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)      # about 30 dB too low
    for _ in range(25):                                                     # five seconds of speech
        out = lv(quiet)
    rms = float(np.sqrt(np.mean(out ** 2)))
    assert 0.03 < rms < 0.09, rms
    gain = lv.gain
    hush = np.full(SR // 5, 1e-5, np.float32)
    for _ in range(25):
        out = lv(hush)
    assert lv.gain == gain and float(np.abs(out).max()) < 0.001
    loud = np.full(SR // 5, 0.9, np.float32)
    for _ in range(25):
        out = lv(loud)
    assert float(np.abs(out).max()) <= 1.0 and lv.gain >= AutoLevel.MIN_GAIN


def test_number_scrap_dropped():
    """A turned-up feed rings on after "three" and Whisper adds a bare "8." right after the sentence."""
    from riah_translate.asr import Word, _drop_echoes
    before = Word(10.5, 10.95, " 3.")
    assert _drop_echoes([Word(11.03, 11.49, " 8.")], before) == []
    kept = _drop_echoes([Word(11.6, 12.0, " 8.")], before)              # a real pause: keep it
    assert [w.text for w in kept] == [" 8."]
    kept = _drop_echoes([Word(11.0, 11.4, " 16")], Word(10.5, 10.95, " verse"))   # "verse 16" stays
    assert [w.text for w in kept] == [" 16"]


def test_mic_reconnects():
    """The sound input drops out mid-service: the app keeps trying to open it again until it's back."""
    import types
    import numpy as np
    from riah_translate.audio import MicSource
    state = dict(back=False, opened=0)

    class FakeStream:
        def __init__(self, **kw):
            if state["opened"] and not state["back"]:
                raise OSError("device unavailable")
            state["opened"] += 1
            self.cb = kw["callback"]

        def start(self):
            pass

        def abort(self, ignore_errors=True):
            pass

        def close(self, ignore_errors=True):
            pass

    fake = types.SimpleNamespace(
        InputStream=FakeStream, _terminate=lambda: None, _initialize=lambda: None,
        query_devices=lambda *a: ([dict(name="Board", max_input_channels=2)] if not a or a[0] is None and len(a) == 1
                                  else dict(name="Board", max_input_channels=2, default_samplerate=16000)))
    real = sys.modules.get("sounddevice")
    sys.modules["sounddevice"] = fake
    try:
        m = MicSource(None, None).start()
        assert m.read(0.01)[0].size == 0 and not m.reconnecting
        m.last = time.monotonic() - 3                      # nothing for three seconds: the cable came out
        m.read(0.01)
        assert m.reconnecting and m.stream is None         # tried, not back yet
        state["back"] = True
        m.next_try = 0
        m.read(0.01)
        assert m.stream is not None and state["opened"] == 2
        m.stream.cb(np.full((4, 2), 0.25, np.float32), 4, None, None)
        assert m.read(0.01)[0].size == 4 and not m.reconnecting
    finally:
        if real is None:
            sys.modules.pop("sounddevice", None)
        else:
            sys.modules["sounddevice"] = real


def test_engine_starts_again_after_a_problem():
    from riah_translate.engine import Engine
    hub = Hub()
    seen = []
    hub.taps.append(lambda ev: seen.append(ev) if ev.get("type") == "status" else None)
    e = Engine(hub)
    e.stop_flag.wait = lambda secs=None: False          # no real waiting in a test
    e._sleep = lambda secs: False
    calls = []

    def session(cfg):
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("the sound input went away")
    e._session = session
    e._run(dict(src="en", tgts=["th"]))
    assert len(calls) == 3
    assert [s["state"] for s in seen] == ["reconnecting", "reconnecting"], seen
    assert "again" in seen[0]["detail"].lower() or "reconnect" in seen[0]["detail"].lower(), seen[0]

    seen.clear()
    e._session = lambda cfg: (_ for _ in ()).throw(ValueError("can't listen to that language"))
    e._run(dict(src="en", tgts=["th"]))
    assert [s["state"] for s in seen] == ["error"], seen     # a choice that can't work stops, with a message


def test_notes_and_layout_api():
    """Sermon notes keep only names and Bible passages; the layout only takes the three known values."""
    import base64
    import types
    from riah_translate.app import App
    got = {}
    fake = types.SimpleNamespace(engine=types.SimpleNamespace(set_notes=lambda n: got.update(n=n)), notes_info=None)
    text = "This week our guest Somchai talks about the night visit.\nRead John 3:16-18 and Romans 8:28 together.\n"
    out = App.notes(fake, dict(name="C:\\Users\\x\\week 12.txt", data=base64.b64encode(text.encode()).decode()))
    assert out["notes"]["name"] == "week 12.txt" and out["notes"]["refs"] == 2, out
    assert set(got["n"]) == {"hints", "refs"} and "Somchai" in got["n"]["hints"], got
    try:
        App.notes(fake, dict(name="x.txt", data="not base64!"))
        raise AssertionError("bad data accepted")
    except ValueError:
        pass
    assert App.notes(fake, dict(clear=True))["notes"] is None and got["n"] is None

    moved = []
    hub = Hub()
    fake = types.SimpleNamespace(hub=hub, relayout_hook=moved.append,
                                 settings=types.SimpleNamespace(data={}, save=lambda: None))
    App.display(fake, dict(layout="sideways"))
    App.display(fake, dict(layout="bar"))
    App.display(fake, dict(layout="bar", fontScale=1.2))
    assert moved == ["bar"] and hub.display["layout"] == "bar" and fake.settings.data["display"]["layout"] == "bar"


def test_hostile_notes():
    """A notes file built to hang or swamp the computer is read quickly or turned away."""
    import io
    import zipfile
    from riah_translate.notes import NotesError, notes_from_text, read_notes
    t = time.monotonic()
    notes_from_text("Dr. " * 8000)                                          # one 32 KB line of abbreviations
    notes_from_text(("Dr. " * 500 + "\n") * 2000)                           # many long lines
    assert time.monotonic() - t < 8, time.monotonic() - t           # bounded by the size caps

    def odt(xml):
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            z.writestr("mimetype", "application/vnd.oasis.opendocument.text")
            z.writestr("content.xml", xml)
        return b.getvalue()
    head = ('<?xml version="1.0" encoding="UTF-8"?><office:document-content '
            'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:text>')
    for c in ("2000000000", "abc", "-5"):
        out = read_notes("x.odt", odt(f'{head}<text:p>This week our guest<text:s text:c="{c}"/>Somchai talks about the night visit.'
                                       '</text:p></office:text></office:body></office:document-content>'))
        assert "Somchai" in out["hints"] and len(out["hints"]) < 1000, (c, out)
    try:
        read_notes("x.odt", odt((head + "<text:p>Hi</text:p></office:text></office:body>"
                                 "</office:document-content>").replace("UTF-8", "UTF-16").encode("utf-16")))
        raise AssertionError("UTF-16 part accepted")
    except NotesError:
        pass


def test_lan_host_check():
    """With --lan, other devices must name this computer by address or local name (no DNS rebinding)."""
    from riah_translate.server import make_handler
    ok = make_handler(Hub(), None)._host_ok
    for h in ("192.168.1.20:8765", "[fe80::1]:8765", "ChurchMac.local:8765", "10.0.0.5"):
        assert ok(h), h
    for h in ("evil.example.com:8765", "", None, "192.168.1.20.evil.com"):
        assert not ok(h), h


def _fail_first(k, exc=RuntimeError):
    """A fault that hits the first k calls and then stops."""
    def fault(n):
        if n <= k:
            raise exc("injected fault")
    return fault


class _rig:
    """A whole live session with the real engine and the real StreamingTranscriber, and fakes only
    where the real thing needs a model, a microphone or a network: a stand-in Whisper that says
    "w0 w1 w2 ..." in step with the audio clock, a microphone that plays loud audio, a quiet
    translator. Each stage takes an optional fault function that may raise, so a test can break
    any step of the live loop on purpose."""

    AMP = dict(speech=0.3, silence=0.0, offmic=0.003, mumble=0.05, blast=1.0)

    def __init__(self, fault=None, chunks=200, read_fault=None, level_fault=None, mt_fault=None,
                 publish_fault=None, load_fault=None, script=None, open_fault=None):
        self.open_fault, self.opened, self.stops, self.hearings, self.kinds = open_fault, 0, 0, 0, []
        self.reader_log = []              # which sound input (by number) each read came from   # sound inputs opened and closed; every event type seen
        self.fault, self.read_fault, self.level_fault = fault, read_fault, level_fault
        self.mt_fault, self.publish_fault, self.load_fault = mt_fault, publish_fault, load_fault
        # script: [(seconds, "speech" | "silence" | "offmic" | "mumble" | "blast"), ...] plays real
        # odd audio levels, and the stand-in Whisper and VAD answer the way the real ones do to them
        self.amps = [self.AMP[m] for secs, m in script for _ in range(int(secs / 0.2))] if script else None
        self.left = len(self.amps) if script else chunks
        self.total = self.left
        self.tr = None
        self.lines, self.states, self.details = [], [], []
        self.reads = self.levels = self.mt_calls = self.publishes = 0
        self.loads = self.forgets = 0
        self.recovers = []
        self.cfg = dict(src="en", tgts=["th"], input=None, channel=None, model="small.en")

    def __enter__(self):
        import contextlib
        import io
        import math
        import types
        import numpy as np
        import riah_translate.audio as A
        import riah_translate.engine as E
        from riah_translate.asr import StreamingTranscriber
        r = self
        self._saved = (A.MicSource, A.AutoLevel, E.StreamingTranscriber, E.load_whisper, E.choose_whisper,
                       E.Translations, E.forget_whisper)
        self._quiet = contextlib.ExitStack()
        self._quiet.enter_context(contextlib.redirect_stderr(io.StringIO()))
        self._quiet.enter_context(contextlib.redirect_stdout(io.StringIO()))

        class Whisper:
            calls = 0
            owner = None

            def transcribe(self, audio, **kw):
                Whisper.calls += 1
                if r.fault:
                    r.fault(Whisper.calls)
                t0 = self.owner.offset
                t1 = t0 + len(audio) / 16000
                if r.amps:
                    a = float(np.abs(audio[-16000:]).mean())
                    if a > 0.9 or (0.02 <= a < 0.2 and Whisper.calls % 4 == 0):
                        return [], None                                   # a blast, or mumbling it can't make out
                    if a < 0.2:                                           # mumbling, or silence it was asked about
                        said = ("Thank you.", "you", "uh")[Whisper.calls % 3]
                        low = types.SimpleNamespace(start=0.1, end=0.9, word=f" {said}")
                        return [types.SimpleNamespace(no_speech_prob=0.9 if said != "uh" else 0.95,
                                                      avg_logprob=-1.5, text=said, words=[low])], None
                words =[types.SimpleNamespace(start=k * 0.5 - t0, end=k * 0.5 + 0.4 - t0, word=f" w{k}")
                         for k in range(math.ceil(t0 / 0.5 - 1e-9), int((t1 - 0.4) / 0.5 + 1e-9) + 1)]
                return [types.SimpleNamespace(no_speech_prob=0.0, avg_logprob=-0.1, text="w", words=words)], None

        class Source:
            def start(self): return self
            def stop(self): r.stops += 1
            def read(self, secs):
                r.reader_log.append(self.n)
                r.reads += 1
                if r.read_fault:
                    r.read_fault(r.reads)
                if r.left <= 0:
                    return np.zeros(0, dtype=np.float32), True
                amp = r.amps[r.total - r.left] if r.amps else 0.3
                r.left -= 1
                return np.full(int(0.2 * 16000), amp, dtype=np.float32), False

        class Level:
            def __call__(self, chunk):
                r.levels += 1
                if r.level_fault:
                    r.level_fault(r.levels)
                return chunk

        class Mt:
            def __init__(self, *a, **k): self.thread = types.SimpleNamespace(is_alive=lambda: False)
            def draft_covers(self, seg, text): return False
            def add_line(self, *a):
                r.mt_calls += 1
                if r.mt_fault:
                    r.mt_fault("add", r.mt_calls)
            def want_draft(self, *a):
                if r.mt_fault:
                    r.mt_fault("draft", r.mt_calls)
            def close(self, timeout=30): pass
            def set_targets(self, t): pass

        self.model = Whisper()

        def make_transcriber(*a, **k):
            r.hearings += 1
            tr = StreamingTranscriber(r.model, "en", max_words=6)
            r.model.owner = tr
            r.tr = tr
            if r.amps:       # the VAD's plain loudness fallback: seconds of near-silence at the end
                def silent_tail():
                    tail = np.abs(tr.audio[-int(4 * 16000):])
                    quiet = 0
                    for i in range(len(tail) - 800, -1, -800):
                        if tail[i:i + 800].max() >= 0.01:
                            break
                        quiet += 800
                    return quiet / 16000
                tr._silent_tail_sec = silent_tail
            else:
                tr._silent_tail_sec = lambda: 0.0
            real = tr.recover

            def spy(hard=False):
                r.recovers.append(hard)
                real(hard)
            tr.recover = spy
            return tr

        def load(*a, **k):
            r.loads += 1
            if r.load_fault:
                r.load_fault(r.loads)
            return r.model

        def forget(*a, **k):
            r.forgets += 1

        def open_mic(*a, **k):
            r.opened += 1
            if r.open_fault:
                r.open_fault(r.opened)
            s = Source()
            s.n = r.opened - 1
            return s
        A.MicSource = open_mic
        A.AutoLevel = Level
        E.StreamingTranscriber = make_transcriber
        E.load_whisper = load
        E.choose_whisper = lambda *a, **k: "small.en"
        E.Translations = Mt
        E.forget_whisper = forget
        hub = Hub()

        def tap(ev):
            r.kinds.append(ev.get("type"))
            if ev.get("type") == "status":
                r.states.append(ev["state"])
                r.details.append(ev.get("detail", ""))
            elif ev.get("type") == "line":
                r.lines.append(ev)
        hub.taps.append(tap)
        publish = hub.publish

        def flaky_publish(ev):
            if r.publish_fault and ev.get("type") != "status":
                r.publishes += 1
                r.publish_fault(r.publishes)
            return publish(ev)
        hub.publish = flaky_publish
        self.eng = E.Engine(hub)
        self.eng._translator = lambda cfg, threads: object()
        self.waits = []
        self.eng.stop_flag.wait = lambda secs=None: False
        self.eng._sleep = lambda secs: False        # no real pause before a retry in a test
        self.eng._spawn = lambda fn: fn()           # Restart's new sound input opens at once, not on a thread
        return self

    def __exit__(self, *exc):
        import riah_translate.audio as A
        import riah_translate.engine as E
        (A.MicSource, A.AutoLevel, E.StreamingTranscriber, E.load_whisper, E.choose_whisper,
         E.Translations, E.forget_whisper) = self._saved
        self._quiet.close()


def test_one_bad_pass_does_not_end_the_service():
    """A failed Whisper pass costs only that pass's words (2026-11-26: on a Mac one bad pass
    mid-sermon dropped the operator from the live screen back to setup). A run of them climbs a
    ladder - tidy, start the hearing afresh, load the model again - and the sound input and the
    translations keep running the whole time. Only ten in a row rebuild the session."""
    import riah_translate.engine as E
    for fails, soft_then_hard, reloaded in ((1, [False], 0), (2, [False, False], 0),
                                            (4, [False, False, True, True], 0),
                                            (7, [False, False, True, True, True, True, True], 1)):
        with _rig(fault=_fail_first(fails)) as r:
            r.eng._session(r.cfg)
        assert r.recovers == soft_then_hard, (fails, r.recovers)
        assert r.forgets == reloaded and r.loads == 1 + reloaded, (fails, r.forgets, r.loads)
        assert "error" not in r.states and "reconnecting" not in r.states, (fails, r.states)
        assert len(r.lines) >= 5, (fails, len(r.lines))          # words came back after the bad passes
    with _rig(fault=_fail_first(E.FAILS_RESTART + 2)) as r:
        try:
            r.eng._session(r.cfg)
            raise AssertionError("ten bad passes in a row should rebuild the session")
        except E.PassesFailed:
            pass
        assert r.eng.mt is None and r.eng.tr is None            # and it cleaned up after itself


def test_every_stage_can_fail_without_ending_the_service():
    """The level meter, the auto-level, a translation job, a draft, a screen update, the sound
    input's reconnect check: each failing now and then costs that step, never the service, and
    every finished line still reaches the translator under its own number."""
    def every(n):
        return lambda i, *a: (_ for _ in ()).throw(OSError("injected fault")) if i % n == 0 else None

    def mt_fault(which, i):
        if i % 5 == 0 or (which == "draft" and i % 2 == 0):
            raise RuntimeError("injected fault")
    with _rig(level_fault=every(7), mt_fault=mt_fault, publish_fault=every(11),
              fault=lambda n: (_ for _ in ()).throw(ValueError("injected")) if n % 9 == 0 else None) as r:
        r.eng._session(r.cfg)
    assert r.eng.seg >= 8 and r.mt_calls == r.eng.seg, (r.eng.seg, r.mt_calls)
    assert len(r.lines) >= r.eng.seg - r.publishes // 11 - 1, (len(r.lines), r.eng.seg)
    assert "error" not in r.states and r.states[-1] == "stopped", r.states


def test_a_value_error_mid_service_never_stops_it():
    """Before the service goes live a choice that can't work stops with a message; after that,
    whatever the error is (even a ValueError, even twenty-five bad passes), only Stop ends it."""
    import riah_translate.engine as E
    with _rig(fault=_fail_first(25, ValueError)) as r:
        r.eng._sleep = lambda secs: r.waits.append(secs) or False
        r.eng._run(r.cfg)
    assert "error" not in r.states, r.states
    assert r.states.count("reconnecting") == 2 and r.states[-1] == "stopped", r.states
    assert [w for w in r.waits if w >= 1][:2] == [1, 2] and len(r.lines) >= 5, (r.waits, len(r.lines))
    # a sound input that raises a ValueError mid-sermon is retried too
    with _rig(read_fault=lambda n: (_ for _ in ()).throw(ValueError("injected")) if n == 30 else None) as r:
        r.eng._run(r.cfg)
    assert "error" not in r.states and r.states.count("reconnecting") == 1 and r.states[-1] == "stopped", r.states


def test_a_failed_start_keeps_its_message():
    """Start failing before everything was set up (no internet for the first download) must show
    the download message, not an error from the cleanup (an unset `voice` once hid it)."""
    import riah_translate.engine as E

    def no_internet(n):
        raise E.DownloadFailed("No internet.")
    with _rig(load_fault=no_internet) as r:
        try:
            r.eng._session(r.cfg)
            raise AssertionError("a failed download should raise")
        except E.DownloadFailed:
            pass
        r.eng._sleep = lambda secs: r.waits.append(secs) or len(r.waits) >= 2
        r.eng._run(r.cfg)
        rec = [d for s, d in zip(r.states, r.details) if s == "reconnecting"]
        assert len(rec) == 2 and "No internet" in rec[0], (r.states, r.details)
        assert r.waits == [1, 2], r.waits


def _words(r):
    return sum(len(ln["src"].split()) for ln in r.lines)


def test_restart_button_is_instant_and_loses_nothing():
    """The sound person's Restart: a new sound input and a fresh hearing in the same breath, with
    the screen's lines, their numbering and the translator carried straight on. Nothing is cleared,
    no words are lost or said twice, and pressing it twice in a row is fine."""
    with _rig(chunks=300) as r:
        r.eng._session(r.cfg)
    base_words, base_segs = _words(r), r.eng.seg
    presses = (40, 90, 91, 140, 141, 200)
    with _rig(chunks=300, read_fault=lambda n: r.eng.restart_flag.set() if n in presses else None) as r:
        r.eng._session(r.cfg)
    assert r.hearings == 1 + len(presses), r.hearings               # a fresh hearing per press
    assert r.reader_log == sorted(r.reader_log) and r.reader_log[-1] == len(presses), "new input not used"
    assert r.opened == 1 + len(presses) and r.stops == r.opened, (r.opened, r.stops)   # a fresh input per press, none left open
    assert "error" not in r.states and r.states[-1] == "stopped", r.states
    assert r.states.count("reconnecting") == len(presses), r.states
    for i, s in enumerate(r.states):                              # every Restart ends back on "listening"
        if s == "reconnecting":
            assert r.states[i + 1] == "listening", r.states
    assert "clear" not in r.kinds, "Restart must never wipe the church screen"
    ids = [ln["id"] for ln in r.lines]
    assert ids == sorted(set(ids)), "line ids repeat or go backwards"
    assert [ln["seg"] for ln in r.lines] == list(range(len(r.lines))), "numbering skipped or repeated"
    assert r.mt_calls == r.eng.seg == len(r.lines), (r.mt_calls, r.eng.seg, len(r.lines))
    lost = base_words - _words(r)
    assert -3 <= lost <= 3 * len(presses), (base_words, _words(r))     # a moment of audio, never a sentence
    assert r.eng.mt is None and r.eng.tr is None


def test_restart_survives_bad_moments():
    """Restart while the hearing is failing, and a sound input that won't open straight away: the
    service still never ends. Only Stop does."""
    with _rig(chunks=300, fault=_fail_first(3),
              read_fault=lambda n: r.eng.restart_flag.set() if n in (2, 60) else None) as r:
        r.eng._session(r.cfg)
    assert "error" not in r.states and r.states[-1] == "stopped", r.states
    assert len(r.lines) >= 5
    # the new sound input won't open: the old one carries on, and nothing else changes
    with _rig(chunks=300, read_fault=lambda n: r.eng.restart_flag.set() if n == 50 else None,
              open_fault=lambda n: (_ for _ in ()).throw(OSError("busy")) if n == 2 else None) as r:
        r.eng._run(r.cfg)
    assert "error" not in r.states and r.states[-1] == "stopped" and r.opened == 2, (r.states, r.opened)
    assert r.states.count("reconnecting") == 1 and r.left == 0 and r.stops == 1 and len(r.lines) >= 20, (r.states, r.left, r.stops)
    # Stop pressed while the new input is still opening: it is closed as soon as it opens, never left running
    late = []
    with _rig(chunks=60, read_fault=lambda n: r.eng.restart_flag.set() if n == 55 else None) as r:
        r.eng._spawn = late.append
        r.eng._run(r.cfg)
        assert len(late) == 1 and r.opened == 1 and r.stops == 1, (len(late), r.opened, r.stops)
        late[0]()
    assert r.opened == 2 and r.stops == 2, (r.opened, r.stops)


def test_restart_state_and_pause():
    """restart() only does something while a service is live, and a Restart pressed during the pause
    before a retry cuts that pause short; Stop still ends it."""
    import types
    from riah_translate.engine import Engine
    e = Engine(Hub())
    assert e.restart() is False and not e.restart_flag.is_set()          # nothing running
    e.thread = types.SimpleNamespace(is_alive=lambda: True)
    assert e.restart() is False                                          # running, but not live yet
    e._live = True
    assert e.restart() is True and e.restart_flag.is_set()
    e.stop_flag.set()
    e.restart_flag.clear()
    assert e.restart() is False and not e.restart_flag.is_set()          # Stop is already on its way
    e.stop_flag.clear()
    for who, expect in ((e.restart_flag, False), (e.stop_flag, True)):
        e.restart_flag.clear()
        e.stop_flag.clear()
        t = time.monotonic()
        threading.Timer(0.05, who.set).start()
        assert e._sleep(5) is expect and time.monotonic() - t < 1.0


def test_flush_finishes_the_words_without_a_speech_pass():
    """Restart uses flush(): what was heard but not yet a line becomes a line at once, with no
    Whisper pass (the model here would blow up if it were asked), and it never raises."""
    from riah_translate.asr import StreamingTranscriber, Word
    tr = StreamingTranscriber(object(), "en")
    tr.pending = [Word(1.0, 1.4, " hello"), Word(1.5, 1.9, " there")]
    tr.hyp.buffer = [Word(2.0, 2.4, " friends")]
    lines = tr.flush()
    assert [ln["text"] for ln in lines] == ["Hello there friends"], lines
    assert tr.pending == [] and tr.hyp.buffer == []
    assert tr.flush() == []
    tr.hyp = None                                        # broken state: still no exception
    assert tr.flush() == [] and tr.pending == []


def test_a_long_service_with_random_faults_never_ends():
    """Ten minutes of audio with faults sprinkled over every stage at random (seeded, so a failure
    reproduces): the service must reach the end of the audio on its own, with no error shown."""
    import random
    import riah_translate.engine as E
    rnd = random.Random(2026)
    boom = (RuntimeError, ValueError, OSError, IndexError, MemoryError, ZeroDivisionError, KeyError)

    def maybe(p):
        def fault(*a):
            if rnd.random() < p:
                raise rnd.choice(boom)("injected fault")
        return fault
    t = time.monotonic()
    with _rig(chunks=3000, fault=maybe(0.12), level_fault=maybe(0.04), publish_fault=maybe(0.03),
              mt_fault=maybe(0.06), read_fault=maybe(0.002)) as r:
        r.eng._run(r.cfg)
    assert "error" not in r.states and r.states[-1] == "stopped", (r.states[-5:], r.states.count("reconnecting"))
    assert r.left == 0 and r.eng.seg >= 120, (r.left, r.eng.seg)
    assert time.monotonic() - t < 60, time.monotonic() - t


def test_mumbling_silence_and_going_off_the_mic_are_normal():
    """A preacher mumbles, steps away from the mic to make a point, goes quiet, bumps it. None of
    that is a fault: no repair, no restart, no status change, no lost or repeated lines, and the
    next real words come out as normal. (The stand-in Whisper answers mumbling with nothing, a
    low-confidence guess or one of its known inventions, the way the real one does.)"""
    script = [(20, "speech"), (60, "silence"), (20, "speech"), (40, "mumble"), (30, "offmic"), (20, "speech"),
              (10, "blast"), (20, "speech"), (120, "silence"), (30, "mumble"), (20, "speech")]
    with _rig(script=script) as r:
        r.eng._run(r.cfg)
        buffer_sec = len(r.tr.audio) / 16000
    assert r.recovers == [] and r.forgets == 0 and r.loads == 1, (r.recovers, r.forgets, r.loads)
    assert r.states == ["loading", "listening", "stopped"], r.states       # nothing the operator would notice
    words = [int(w[1:]) for ln in r.lines for w in ln["src"].split() if w.startswith("w")]
    assert words == sorted(set(words)), "a word came out twice or out of order"
    assert len(words) >= 5 * 20 * 2 * 0.6, len(words)                       # each stretch of real speech got through
    assert not any(w in ln["src"].lower() for ln in r.lines for w in ("thank you", "uh")), "an invention reached the screen"
    assert [ln["seg"] for ln in r.lines] == list(range(len(r.lines))), "line numbers skipped"
    assert buffer_sec < 25, buffer_sec                                      # long quiet stretches don't pile up audio


def test_recover_keeps_the_clock():
    """The repair after a failed pass keeps line times right, throws away odd audio, and (hard)
    forgets what was waiting to become a line. It never raises."""
    import numpy as np
    from riah_translate.asr import SR, StreamingTranscriber, Word
    tr = StreamingTranscriber(object(), "en")
    tr.push(np.full(10 * SR, 0.2, dtype=np.float32))
    tr.audio[100] = np.nan
    tr.pending = [Word(1.0, 1.4, "hello")]
    tr.hyp.buffer = [Word(9.0, 9.4, "there")]
    end = tr.now
    tr.recover()
    assert abs(tr.now - end) < 1e-6 and len(tr.audio) == 2 * SR and np.isfinite(tr.audio).all()
    assert tr.hyp.buffer == [] and [w.text for w in tr.pending] == ["hello"]
    tr.recover(hard=True)
    assert tr.pending == [] and abs(tr.now - end) < 1e-6 and tr.hyp.last_time == tr.offset
    tr.audio = None                      # even a broken buffer is put right, not raised about
    tr.recover(hard=True)
    assert len(tr.audio) == 0


def test_voice_keeps_the_newest_line():
    """Spoken translation: when speech falls behind a fast speaker the OLDEST waiting line is
    dropped, never the newest (a person needs the latest sentence, not a backlog)."""
    import queue
    from riah_translate.voice import _offer

    q = queue.Queue()
    assert _offer(q, "one", 2) is False
    assert _offer(q, "two", 2) is False
    assert q.qsize() == 2
    assert _offer(q, "three", 2) is True        # room is made by dropping "one"
    assert [q.get_nowait(), q.get_nowait()] == ["two", "three"]

    # A line never silently disappears when there is room.
    q2 = queue.Queue()
    for w in ("a", "b", "c", "d"):
        assert _offer(q2, w, 2) is (w in ("c", "d"))
    assert q2.qsize() == 2


if __name__ == "__main__":
    tests =[(k, v) for k, v in list(globals().items()) if k.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"PASS ({len(tests)} checks, {threading.active_count()} threads left)")
