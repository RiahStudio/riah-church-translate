"""Builds the Bible word list (riah_translate/bible_words.json) from open Bibles.

For every Bible name, book and key church term the preacher might say in English, it finds how the
Thai and Chinese Bibles write it, so the translation can use the same spelling the congregation
reads in their own Bible.

How a name is matched: the English Bibles and the Thai/Chinese Bibles are lined up verse by verse.
A name like "Nicodemus" sits in 5 verses; the Thai word that sits in those same 5 verses (and
rarely anywhere else) is its Thai spelling. Scored with the Dice overlap of the two verse sets.

* English names come from three English Bibles (World English, Berean Standard, King James), so
  the older spellings a preacher may quote (Elias, Esaias) are covered too.
* Thai: the Thai Freedom Bible (public domain). The Thai KJV only double-checks it: the spelling both
  Thai Bibles use wins; a Freedom Bible spelling the other doesn't use is kept only when it's clear-cut.
* Chinese: the Chinese Union Version, simplified and traditional (public domain).
* Only the word list ships with the app, never the Bible text.

Run on a Linux test machine (stdlib, plus pypinyin for the Chinese sound check):
  bash tools/bible/fetch.sh ~/bible-data
  python tools/bible/build_bible_words.py ~/bible-data
Then tools/bible/probe_bible_words.py adds the spellings the translator gets wrong ("misses").
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(ROOT, "riah_translate", "bible_words.json")
REPORT = os.path.join(HERE, "_out")

SOURCES = [
    dict(id="engwebp", name="World English Bible", license="Public domain", use="English names"),
    dict(id="engbsb", name="Berean Standard Bible", license="Public domain", use="English names"),
    dict(id="eng-kjv", name="King James Version", license="Public domain", use="English names (older spellings)"),
    dict(id="thafb", name="Thai Freedom Bible (draft)", license="Public domain", use="Thai spellings"),
    dict(id="thaKJV", name="Thai King James Version, (c) 2003 Philip Pope", license="CC BY-NC-ND 4.0",
         use="Cross-check only: a Thai name is kept when both Thai Bibles agree. No text taken from it."),
    dict(id="cmn-cu89s", name="Chinese Union Version (Simplified)", license="Public domain", use="Chinese spellings"),
    dict(id="cmn-cu89t", name="Chinese Union Version (Traditional)", license="Public domain", use="Chinese spellings"),
]

# The 66 books in order: USFM code, English name, how a preacher may say the number.
BOOKS = [
    ("GEN", "Genesis"), ("EXO", "Exodus"), ("LEV", "Leviticus"), ("NUM", "Numbers"), ("DEU", "Deuteronomy"),
    ("JOS", "Joshua"), ("JDG", "Judges"), ("RUT", "Ruth"), ("1SA", "1 Samuel"), ("2SA", "2 Samuel"),
    ("1KI", "1 Kings"), ("2KI", "2 Kings"), ("1CH", "1 Chronicles"), ("2CH", "2 Chronicles"), ("EZR", "Ezra"),
    ("NEH", "Nehemiah"), ("EST", "Esther"), ("JOB", "Job"), ("PSA", "Psalms"), ("PRO", "Proverbs"),
    ("ECC", "Ecclesiastes"), ("SNG", "Song of Solomon"), ("ISA", "Isaiah"), ("JER", "Jeremiah"),
    ("LAM", "Lamentations"), ("EZK", "Ezekiel"), ("DAN", "Daniel"), ("HOS", "Hosea"), ("JOL", "Joel"),
    ("AMO", "Amos"), ("OBA", "Obadiah"), ("JON", "Jonah"), ("MIC", "Micah"), ("NAM", "Nahum"),
    ("HAB", "Habakkuk"), ("ZEP", "Zephaniah"), ("HAG", "Haggai"), ("ZEC", "Zechariah"), ("MAL", "Malachi"),
    ("MAT", "Matthew"), ("MRK", "Mark"), ("LUK", "Luke"), ("JHN", "John"), ("ACT", "Acts"), ("ROM", "Romans"),
    ("1CO", "1 Corinthians"), ("2CO", "2 Corinthians"), ("GAL", "Galatians"), ("EPH", "Ephesians"),
    ("PHP", "Philippians"), ("COL", "Colossians"), ("1TH", "1 Thessalonians"), ("2TH", "2 Thessalonians"),
    ("1TI", "1 Timothy"), ("2TI", "2 Timothy"), ("TIT", "Titus"), ("PHM", "Philemon"), ("HEB", "Hebrews"),
    ("JAS", "James"), ("1PE", "1 Peter"), ("2PE", "2 Peter"), ("1JN", "1 John"), ("2JN", "2 John"),
    ("3JN", "3 John"), ("JUD", "Jude"), ("REV", "Revelation"),
]
# Where the Thai Freedom Bible's book title differs from the Thai Standard Version that most Thai
# churches read, use the Standard Version's title (the Thai KJV agrees on each of these).
TH_BOOK = {"RUT": "นางรูธ", "LAM": "เพลงคร่ำครวญ", "PHM": "ฟีเลโมน", "REV": "วิวรณ์"}
BOOK_SAY = {"Psalms": ["Psalms", "Psalm"], "Song of Solomon": ["Song of Solomon", "Song of Songs"],
            "Revelation": ["Revelation", "Revelations"], "Acts": ["Acts"]}

# Key church words, with the words Protestant churches use. Each must also be found in the Thai and
# Chinese Bibles (checked below), so a typo or an odd choice shows up in the report.
# en: an English regex, matched case-insensitively on whole words.
TERMS = [
    dict(en=r"holy spirit|holy ghost", th="พระวิญญาณบริสุทธิ์", zh="圣灵", tw="聖靈"),
    dict(en=r"gospels?", th="ข่าวประเสริฐ", zh="福音", tw="福音"),
    dict(en=r"grace", th="พระคุณ", zh="恩典", tw="恩典"),
    dict(en=r"pray(s|ed|ing)?|prayers?", th="อธิษฐาน", zh="祷告", tw="禱告"),
    dict(en=r"worship(s|ped|ping)?", th="นมัสการ", zh="敬拜", tw="敬拜"),
    dict(en=r"pastors?", th="ศิษยาภิบาล", zh="牧师", tw="牧師"),
    dict(en=r"disciples?", th="สาวก", zh="门徒", tw="門徒"),
    dict(en=r"apostles?", th="อัครทูต", zh="使徒", tw="使徒"),
    dict(en=r"baptism|baptiz(e|ed|es|ing)|baptis(e|ed|es|ing)", th="บัพติศมา", zh="洗礼", tw="洗禮"),
    dict(en=r"repent(s|ed|ing|ance)?", th="กลับใจ", zh="悔改", tw="悔改"),
    dict(en=r"righteousness", th="ความชอบธรรม", zh="义", tw="義"),
    dict(en=r"resurrection", th="การเป็นขึ้นมาจากความตาย", zh="复活", tw="復活"),
    dict(en=r"cross", th="กางเขน", zh="十字架", tw="十字架"),
    dict(en=r"crucif(y|ied|ixion)", th="ตรึงกางเขน", zh="钉十字架", tw="釘十字架"),
    dict(en=r"covenants?", th="พันธสัญญา", zh="约", tw="約"),
    dict(en=r"pharisees?", th="ฟาริสี", zh="法利赛人", tw="法利賽人"),
    dict(en=r"sadducees?", th="สะดูสี", zh="撒都该人", tw="撒都該人"),
    dict(en=r"synagogues?", th="ธรรมศาลา", zh="会堂", tw="會堂"),
    dict(en=r"sabbath", th="วันสะบาโต", zh="安息日", tw="安息日"),
    dict(en=r"passover", th="ปัสกา", zh="逾越节", tw="逾越節"),
    dict(en=r"tabernacle", th="พลับพลา", zh="会幕", tw="會幕"),
    dict(en=r"messiah", th="พระเมสสิยาห์", zh="弥赛亚", tw="彌賽亞"),
    dict(en=r"angels?", th="ทูตสวรรค์", zh="天使", tw="天使"),
    dict(en=r"prophets?", th="ผู้เผยพระวจนะ", zh="先知", tw="先知"),
    dict(en=r"parables?", th="คำอุปมา", zh="比喻", tw="比喻"),
    dict(en=r"lamb of god", th="พระเมษโปดกของพระเจ้า", zh="神的羔羊", tw="神的羔羊"),
    dict(en=r"kingdom of god", th="อาณาจักรของพระเจ้า", zh="神的国", tw="神的國"),
    dict(en=r"kingdom of heaven", th="อาณาจักรสวรรค์", zh="天国", tw="天國"),
    dict(en=r"son of man", th="บุตรมนุษย์", zh="人子", tw="人子"),
    dict(en=r"eternal life", th="ชีวิตนิรันดร์", zh="永生", tw="永生"),
    dict(en=r"salvation", th="ความรอด", zh="救恩", tw="救恩"),
    dict(en=r"hallelujah", th="ฮาเลลูยา", zh="哈利路亚", tw="哈利路亞"),
]

# New Testament books, by both the USFM codes above and the codes the verse-per-line files use.
NT = {code for code, _ in BOOKS[39:]} | {"MAR", "JOH", "PHI", "JAM", "1JO", "2JO", "3JO"}
THAI_RUN = re.compile("[ก-๎]+")
# One Thai letter cluster: a consonant with its vowels and tone marks, so a piece of text never
# starts or ends in the middle of a syllable.
TH_CLUSTER = re.compile("[เ-ไ]?[ก-ฮ][ัิ-ฺ็-๎]*"
                        "[ะาำๅ]?[็-๎]*")
CJK = re.compile("[㐀-鿿豈-﫿]+")
EN_WORD = re.compile(r"[A-Za-z]+(?:[-'][A-Za-z]+)*")

try:                                  # optional: checks that a Chinese name starts with the right sound
    from pypinyin import lazy_pinyin
except ImportError:
    lazy_pinyin = None


def read_vpl(data, bid):
    """{verse key 'JOH 3:16': text}"""
    out = {}
    with open(os.path.join(data, bid, f"{bid}_vpl.txt"), encoding="utf-8") as f:
        for line in f:
            m = re.match(r"(\w{3}) (\d+:\d+) (.*)", line.rstrip("\n"))
            if m:
                out[f"{m.group(1)} {m.group(2)}"] = m.group(3)
    return out


GAP_FILL = {"崙": "仑", "蔯": "陈"}    # rare characters the simplified file leaves as a space


def fill_gaps(simple, trad):
    """The eBible simplified Union Version prints a space for a few rare characters (希伯 见 for
    希伯仑见, 153 times for 崙 alone); the traditional file has them, letter for letter."""
    out = {}
    for k, s in simple.items():
        t = trad.get(k, "")
        if " " in s and len(t) == len(s):
            s = "".join(GAP_FILL.get(b, b) if a == " " and b != " " else a for a, b in zip(s, t))
        out[k] = s
    return out


def zh_clean(text):
    """The Union Version writes a long name in two parts with a middle dot (亚多尼·比色); a subtitle
    doesn't need the dot, and the name shouldn't break there."""
    return re.sub(r"(?<=[一-鿿])[·・‧](?=[一-鿿])", "", text.replace("　", ""))


def book_headers(data, bid):
    """{USFM code: the book's short title (\\h line)}"""
    d = os.path.join(data, bid, "usfm")
    out = {}
    for fn in sorted(os.listdir(d)):
        m = re.match(r"\d+-(\w{3})", fn)
        if not m:
            continue
        with open(os.path.join(d, fn), encoding="utf-8") as f:
            for line in f:
                if line.startswith("\\h "):
                    out[m.group(1)] = re.sub(r"\s+", " ", line[3:].replace("​", "")).strip()
                    break
    return out


# Words no name runs into: a piece of text never reaches across them (บิลดัด | ชาวชูอาห์, not บิลดัดชา).
TH_BREAK = re.compile("(?=ชาว|กษัตริย์|ทั้งหลาย|และ|ของ|แห่ง|ซึ่ง)")


def thai_grams(text):
    """Every run of 2 to 10 Thai letter clusters (the Freedom Bible's word breaks aren't reliable
    enough: it splits some names and runs others into the next word)."""
    grams = set()
    for run in (p for r in THAI_RUN.findall(text) for p in TH_BREAK.split(r) if p):
        cl = TH_CLUSTER.findall(run)
        for i in range(len(cl)):
            s = cl[i]
            for j in range(i + 1, min(len(cl), i + 10)):
                s += cl[j]
                grams.add(s)
    return grams


def cjk_grams(text):
    grams = set()
    for run in CJK.findall(text):
        for n in range(2, 7):
            for i in range(len(run) - n + 1):
                grams.add(run[i:i + n])
    return grams


def part_of(keys):
    """Old or New Testament, when a name sits (almost) only in one: Judas is 犹大 in the New
    Testament even though 犹大 is mostly Judah in the Old."""
    nt = sum(1 for k in keys if k[:3] in NT)
    if nt >= 0.9 * len(keys):
        return "nt"
    if nt <= 0.1 * len(keys):
        return "ot"
    return "all"


class Side:
    """One target Bible, and the text in it that lines up with an English name."""

    def __init__(self, verses, grams):
        self.verses = verses
        self.grams = grams
        self.text = {"ot": "\n".join(t for k, t in verses.items() if k[:3] not in NT),
                     "nt": "\n".join(t for k, t in verses.items() if k[:3] in NT)}
        self._n = {}
        self.inside = {}      # piece of text -> sure names that contain it (filled after the first pass)

    def count(self, g, part):
        n = self._n.get((g, part))
        if n is None:
            n = (self.count(g, "ot") + self.count(g, "nt")) if part == "all" else self.text[part].count(g)
            self._n[(g, part)] = n
        return n

    def candidates(self, keys, ok=None, many=8):
        """The pieces of text that sit in the same verses as the English name, best first, scored by
        how many of its verses hold the piece (recall) and how much of the piece's use is in those
        verses (precision). A piece inside a longer sure name doesn't count there (马利亚 in 撒马利亚)."""
        keys = [k for k in keys if k in self.verses]
        if not keys:
            return []
        part = part_of(keys)
        sample = keys[::max(1, len(keys) // 300)]
        n = len(sample)
        c = Counter()
        for k in sample:
            c.update(self.grams(self.verses[k]))
        floor = max(2, 0.2 * n)
        cand = sorted(((g, s / n) for g, s in c.items() if s >= floor and (ok is None or ok(g))),
                      key=lambda x: (-x[1], -len(x[0])))[:120]
        # A piece of a longer candidate found in as many verses is the same name cut short.
        # (One verse either way is noise: Elisha's 以利沙 sits in 77 of 92 verses, the piece 以利 in 78.)
        near = max(0.01, 1.01 / n)
        kept = [(g, r) for g, r in cand
                if not any(len(g2) > len(g) and g in g2 and r <= r2 + near for g2, r2 in cand)][:25]
        out = []
        for g, recall in kept:
            longer = self.inside.get(g, ())
            here = sum(self.verses[k].count(g) - sum(self.verses[k].count(L) for L in longer) for k in sample)
            if here <= 0:
                continue
            here *= len(keys) / n
            total = self.count(g, part) - sum(self.count(L, part) for L in longer)
            prec = min(1.0, here / max(total, 1))
            f = 2 * prec * recall / (prec + recall)
            out.append(dict(text=g, f=round(f, 3), recall=round(recall, 3), prec=round(prec, 3), part=part))
        out.sort(key=lambda x: (-x["f"], -len(x["text"])))
        picks = []
        for p in out:
            clash = [q for q in picks if p["text"] in q["text"] or q["text"] in p["text"]]
            if not clash:
                picks.append(p)
            else:
                q = clash[0]
                if len(p["text"]) > len(q["text"]) and p["f"] >= q["f"] - 0.05:
                    picks[picks.index(q)] = p       # the whole name, not its first half (ปริสสิลลา, not ปริส)
                elif len(p["text"]) < len(q["text"]) and p["recall"] >= q["recall"] + 0.25:
                    picks.append(p)                 # the name without the word after it (โกลิอัท, not โกลิอัทชาว)
            if len(picks) >= many:
                break
        return picks

    def learn_sure(self, sure):
        """Index every piece of each sure name, for the 'inside a longer name' rule."""
        self.inside = defaultdict(list)
        for L in sure:
            for i in range(len(L)):
                for j in range(i + 2, len(L) + 1):
                    if j - i < len(L):
                        self.inside[L[i:j]].append(L)


def english_names(verses):
    """Capitalised words that are (almost) never written in lower case: names, not ordinary words."""
    cap, low, where = Counter(), Counter(), defaultdict(set)
    for k, t in verses.items():
        t = t.replace("’", "'").replace("æ", "ae").replace("Æ", "Ae").replace("œ", "oe")   # KJV: Zacchæus
        for w in EN_WORD.findall(t):
            w = re.sub(r"'s?$", "", w)
            if not w:
                continue
            if w[0].isupper() and not w.isupper():
                cap[w] += 1
                where[w].add(k)
            elif w.islower():
                low[w] += 1
    names = {}
    for w, n in cap.items():
        if len(w) < 3 or (low[w.lower()] > 0.02 * n and w not in ALSO_WORDS):
            continue
        names[w] = sorted(where[w])
    return names


# Names that are also ordinary English words or titles, or that the church words list handles.
SKIP = set("""God Lord Yahweh Jehovah Most High Almighty Holy Spirit Ghost Father Son Word Lamb King Mark Job
Will Grace Hope Faith Joy Love Rose Amen Hallelujah Alleluia Sabbath Sabbaths Passover Messiah Christian
Christians Pharisee Pharisees Sadducee Sadducees Gentile Gentiles Sheol Hades Selah Sin Way Behold Yes Oh Woe
Lo Hosanna Abba Rabbi Rabboni Teacher Master Savior Saviour Redeemer Creator Heaven Heavens Hosts Meeting
Beth Psalm Psalms Musician Chronicles Abyss Most Highest Spirit Counselor Comforter Bridegroom Prince Alas
Baptist Baptizer""".split())   # "the Baptist church" is not พิธีบัพติศมา
ALSO_WORDS = {"Lot"}                 # names that are also everyday words, kept anyway

# Names set by hand: the usual church form (พระเยซู, not bare เยซู), and well-known names too rare
# in the Bible for verse matching. Each spelling is checked against the Bible text when building.
HAND = {
    "Jesus": dict(th="พระเยซู"), "Christ": dict(th="พระคริสต์"), "Jews": dict(th="ชาวยิว"),
    "Gethsemane": dict(th="เกทเสมนี", zh="客西马尼", tw="客西馬尼"),
    "Bartimaeus": dict(th="บารทิเมอัส", zh="巴底买", tw="巴底買"),
    "Babel": dict(th="บาเบล", zh="巴别", tw="巴別"),
    "Emmaus": dict(th="เอมมาอูส", zh="以马忤斯", tw="以馬忤斯"),
    "Malchus": dict(th="มัลคัส", zh="马勒古", tw="馬勒古"),
    "Rhoda": dict(th="โรดา", zh="罗大", tw="羅大"),
    "Eutychus": dict(th="ยูทิคัส", zh="犹推古", tw="猶推古"),
    "Sapphira": dict(th="สัปฟีรา", zh="撒非喇", tw="撒非喇"),
    "Dorcas": dict(th="ดอร์คัส", zh="多加", tw="多加"),
    "Tabitha": dict(th="ทาบิธา", zh="大比大", tw="大比大"),
    "Lydia": dict(th="ลิเดีย", zh="吕底亚", tw="呂底亞"),
    "Onesimus": dict(th="โอเนสิมัส", zh="阿尼西谋", tw="阿尼西謀"),
    "Ichabod": dict(th="อีคาโบด", zh="以迦博", tw="以迦博"),
    "Jabez": dict(th="ยาเบส", zh="雅比斯", tw="雅比斯"),
    "Ezekiel": dict(th="เอเสเคียล", zh="以西结", tw="以西結"),
    "Malachi": dict(th="มาลาคี", zh="玛拉基", tw="瑪拉基"),
    "Kidron": dict(th="ขิดโรน"),
    "Calvary": dict(th="กลโกธา", zh="各各他", tw="各各他"),
    # Names the verse matching got wrong (2026-11-23 check), each spelling found in the Bible text.
    "Hittites": dict(th="ชาวฮิตไทต์", zh="赫人", tw="赫人"),
    "Hittite": dict(th="ชาวฮิตไทต์", zh="赫人", tw="赫人"),
    "Jairus": dict(th="ไยรัส", zh="睚鲁", tw="睚魯"),
    "Pontius": dict(th="ปอนทิอัส", zh="本丢", tw="本丟"),   # Thai writes ปอนทิอัสปีลาต as one word
    "Bernice": dict(th="เบอร์นีส"),
    "Jehudi": dict(th="เยฮูดี"),
    "Epaphras": dict(zh="以巴弗", tw="以巴弗"),
    "Esar-haddon": dict(zh="以撒哈顿", tw="以撒哈頓"),
    "Godhead": dict(th="ความเป็นพระเจ้า"),
    "Huldah": dict(zh="户勒大", tw="戶勒大"),
    "Persians": dict(zh="波斯人", tw="波斯人"),
    "Silvanus": dict(zh="西拉", tw="西拉"),
    "Harsha": dict(zh="哈沙", tw="哈沙"),
    "Alpha": dict(th="อัลฟา", zh="阿拉法", tw="阿拉法"),
    "Omega": dict(th="โอเมกา", zh="俄梅戛", tw="俄梅戛"),
    "Obadiah": dict(th="โอบาดียาห์"),
    "Eliashib": dict(th="เอลียาชีบ"),
    "Cilicia": dict(th="ซีลีเซีย"),
}

MIN_VERSES = 3                       # rarer names are mostly genealogy lists, where verses can't tell names apart
ZH_MIN = 0.5
TH_LEAD = ("ชาว", "พระ")            # "people of", "holy/royal": part of the name (ชาวยิว, พระเยซู)
# Thai words a name never starts or ends with, or holds inside: a phrase, not a name.
TH_START_STOP = tuple("""ของ แห่ง และ ที่ ใน จาก กับ ให้ ได้ ไป ว่า ถึง แก่ ต่อ โดย เมื่อ จึง ซึ่ง นั้น นี้
เขา ท่าน พวก บรรดา เหล่า ทุก กษัตริย์ เมือง แผ่นดิน ภูเขา แม่น้ำ ทะเล ลำธาร ตระกูล เผ่า บุตร ธิดา คน ผู้
หัวหน้า ปุโรหิต กล่าว ตรัส พูด บอก เรียก ชื่อ นาง ทาง ฝั่ง ฟาก ข้าม เนิน หุบเขา ถิ่น ทุ่ง ประตู ชาย หญิง
แคว้น ดินแดน ประเทศ กรุง หมู่บ้าน วัน เดือน องค์ จง จะ ก็ แล้ว อยู่ เป็น คือ ไม่ ด้วย""".split())
TH_END_STOP = tuple("""ของ แห่ง และ ที่ ใน จาก กับ ให้ ได้ ไป ว่า ถึง แก่ ต่อ โดย เมื่อ จึง ก็ ซึ่ง นั้น นี้
เขา ท่าน พวก ทั้ง กล่าว ตรัส พูด บอก ทุกคน ด้วย แล้ว อยู่ เป็น คือ จะ ไม่ เรา ชาว บุตร ผู้ นัก พระ อ่าน""".split())
# บุต(ร) "son of", ใน "in", ที่ "at", พร้อม "with", เป็น "is", ทุ่ง "field", ภรรยา "wife": a name running
# into the next words (เอลียาสาฟบุตร..., นาโยทในรามาห์).
TH_MID_STOP = ("และ", "ของ", "แห่ง", "ซึ่ง", "กษัตริย์", "ทั้งหลาย", "ขึ้น", "ยิ่งใหญ่", "จาก", "กับ", "เมือง",
               "บุต", "ใน", "ที่", "พร้อม", "เป็น", "ทุ่ง", "ภรรยา", "หรือ")
# ...and the same words cut short at the end of a piece (เอลียาสาฟบุ, ดาเบรัทพร้อ).
TH_CUT_END = ("บุ", "เป็", "พร้", "พร้อ", "ทุ่", "ภรร", "ภรรย", "ใ")
TH_INSIDE_STOP = ("ชาว",)            # "Bildad the Shuhite" (บิลดัดชาวชูอาห์): fine first, never later
# The tail of a word a name never starts with (ษัตริย์ from กษัตริย์, รุง from กรุง): a piece of
# text that starts there began in the middle of that word.
TH_FRAG = tuple(sorted({"".join(cl[i:]) for cl in (TH_CLUSTER.findall(w) for w in TH_START_STOP)
                        for i in range(1, len(cl) - 1)}))
ZH_NEVER = set("的了说就们你我她这是和与在也都又要儿答叫见出王为")   # never inside a Bible name
ZH_START = set("一从对向到给被把将往人子神祭")
ZH_END = set("从对向到给被把将往回去问問续續")
RARE = 10                            # fewer verses than this: Chinese only when both Chinese Bibles agree

# The first sound of an English name and the Thai letters that may begin its Thai spelling.
TH_FIRST = {
    "a": "อยฮ", "e": "อยฮ", "i": "อยฮ", "o": "อยฮ", "u": "อยฮ",
    "b": "บปพ", "c": "คกซสศชข", "d": "ดฎ", "f": "ฟพ", "g": "กคจ", "h": "ฮหอ", "j": "ยจญช",
    "k": "คกข", "l": "ลฬ", "m": "ม", "n": "นณ", "p": "ปพฟ", "q": "คก", "r": "รฤ",
    "s": "สซศษช", "t": "ทตธฐถ", "v": "วฟ", "w": "ว", "x": "ซ", "y": "ยอ", "z": "ศสซษ",
}
# ...and the first letter of the Chinese name's pinyin (Peter 彼得 bi, Thomas 多马 duo, Zion 锡安 xi).
ZH_FIRST = {
    "a": "aoeyw", "e": "aoeyw", "i": "aoeyw", "o": "aoeyw", "u": "aoeyw",
    "b": "bp", "c": "gkjxqszc", "d": "dt", "f": "fbp", "g": "gjkqxe", "h": "hxyaoew", "j": "yjzqx",
    "k": "gkjqh", "l": "lrn", "m": "m", "n": "nl", "p": "bpf", "q": "gkjq", "r": "lr",
    "s": "sxzcjq", "t": "tdx", "v": "wfb", "w": "wh", "x": "sxz", "y": "yj", "z": "sxzcj",
}
ZH_SOUND_EXCEPT = {"Eve"}            # 夏娃 (from Hebrew Chavah)
TH_SOUND_EXCEPT = {"James", "Sanhedrin", "Godhead"}   # ยากอบ, สภา, ความเป็นพระเจ้า


def thai_sound_ok(en, th):
    """'Nicodemus' may be นิโคเดมัส, not เศโลเฟหัด: the first sounds must match."""
    for lead in TH_LEAD:
        if th.startswith(lead) and len(th) > len(lead) + 1:
            th = th[len(lead):]
            break
    th = th.lstrip("เแโใไ")
    return bool(th) and th[0] in TH_FIRST.get(en[0].lower(), "")


def thai_sound_score(en, th):
    """How many of the English consonants turn up, in order, as Thai letters that can spell them."""
    cons = [c for c in re.sub(r"(?<=[^aeiou])h", "", en.lower()) if c in TH_FIRST and c not in "aeiouy"]
    letters = [c for c in th if "ก" <= c <= "ฮ"]
    best = [0] * (len(letters) + 1)
    for c in cons:
        ok = TH_FIRST[c]
        prev = best[:]
        for j, t in enumerate(letters, 1):
            best[j] = max(best[j - 1], prev[j], prev[j - 1] + (t in ok))
    return best[-1]


def thai_sound_share(en, th):
    """thai_sound_score as a share of the English consonants (1.0 = every one is there)."""
    cons = [c for c in re.sub(r"(?<=[^aeiou])h", "", en.lower()) if c in TH_FIRST and c not in "aeiouy"]
    return thai_sound_score(en, th) / len(cons) if cons else 1.0


def zh_sound_ok(en, zh):
    if lazy_pinyin is None or en in ZH_SOUND_EXCEPT:
        return True
    return lazy_pinyin(zh[0])[0][:1] in ZH_FIRST.get(en[0].lower(), "")


def zh_len_ok(en, zh):
    """At most two characters more than the English has vowels (塞浦路斯 for Cyprus), not counting a
    closing 人 or 族; longer is a phrase picked up from the verse (Jairus is 睚鲁, not 有一个管会堂)."""
    core = zh[:-1] if zh[-1:] in ("人", "族") else zh
    return len(core) <= (len(re.findall(r"[aeiouy]", en.lower().rstrip("e"))) or 1) + 2


def th_sound_enough(en, th):
    """A third of the English consonants at least (Hittites isn't อาโมไรต์), unless the Thai Bible
    names it its own way (James is ยากอบ)."""
    return en in TH_SOUND_EXCEPT or thai_sound_share(en, th) >= 0.34


def same_name(simple, trad):
    """The traditional spelling of the same name: same length and the same sounds (先祖娶 isn't 哈哥斯)."""
    if len(simple) != len(trad):
        return False
    return lazy_pinyin is None or lazy_pinyin(simple) == lazy_pinyin(trad)


def th_ok(g):
    """A name, not a phrase."""
    if len(g) > 20 or any(w in g for w in TH_MID_STOP) or any(w in g[1:] for w in TH_INSIDE_STOP):
        return False
    return (not g.startswith(TH_START_STOP) and not g.startswith(TH_FRAG) and not g.endswith(TH_END_STOP)
            and not g.endswith(TH_CUT_END))


TH_GLUE = [(s[:i], s[i:]) for s in sorted(set(TH_START_STOP + TH_END_STOP + TH_MID_STOP + ("ชื่อ",)))
           for i in range(1, len(s))]


def th_glued(g, keys, verses):
    """A piece that is a name plus the start of the next word (ดาเบรัทพ, from ดาเบรัทพร้อม...) or the end
    of the word before (อฮามูทัล, from ชื่อฮามูทัล): in most of its verses, the rest of that word sits
    right next to it."""
    texts = None
    for head, tail in TH_GLUE:
        end = g.endswith(head) and len(g) > len(head) + 1
        start = g.startswith(tail) and len(g) > len(tail) + 1
        if not (end or start):
            continue
        if texts is None:
            texts = [verses[k] for k in keys[::max(1, len(keys) // 300)] if k in verses and g in verses[k]]
        if not texts:
            return False
        if end and sum(g + tail in t for t in texts) * 2 >= len(texts):
            return True
        if start and sum(head + g in t for t in texts) * 2 >= len(texts):
            return True
    return False


def zh_ok(g):
    # 人 ends a people's name (法利赛人) but never sits inside one (书亚人比勒达 is "Bildad the Shuhite").
    return not (set(g) & ZH_NEVER) and g[0] not in ZH_START and g[-1] not in ZH_END and "人" not in g[:-1]


def build(data):
    os.makedirs(REPORT, exist_ok=True)
    print("reading Bibles...", flush=True)
    eng = {b: read_vpl(data, b) for b in ("engwebp", "engbsb", "eng-kjv")}
    canon = set(eng["engwebp"])
    eng["eng-kjv"] = {k: v for k, v in eng["eng-kjv"].items() if k in canon}
    th = Side({k: v.replace("​", "") for k, v in read_vpl(data, "thafb").items()}, thai_grams)
    kjv = {k: v.replace("​", "") for k, v in read_vpl(data, "thaKJV").items()}
    zh_raw, tw_raw = read_vpl(data, "cmn-cu89s"), read_vpl(data, "cmn-cu89t")
    zh = Side({k: zh_clean(v) for k, v in fill_gaps(zh_raw, tw_raw).items()}, cjk_grams)
    tw = Side({k: zh_clean(v) for k, v in tw_raw.items()}, cjk_grams)

    # Each name with the English Bible that uses it most ("Israelites" is the Berean's word).
    found, seen = {}, Counter()
    for bid in ("engwebp", "engbsb", "eng-kjv"):
        for w, keys in english_names(eng[bid]).items():
            seen[w] = max(seen[w], len(keys))
            if w not in SKIP and len(keys) >= MIN_VERSES and len(keys) > len(found.get(w, ("", []))[1]):
                found[w] = (bid, keys)
    order = sorted(found.items(), key=lambda x: -len(x[1][1]))
    print(f"{len(order)} English names", flush=True)

    def kjv_support(text, keys):
        known = [k for k in keys if k in kjv]
        if not known:
            return 0.0
        s = sum(1 for k in known if text in kjv[k]) / len(known)
        for lead in TH_LEAD:          # ชาวยิว in one Bible, พวกยิว in the other: same name
            if s < 0.5 and text.startswith(lead) and len(text) > len(lead) + 2:
                s = max(s, sum(1 for k in known if text[len(lead):] in kjv[k]) / len(known))
        return round(s, 3)

    def taken(owner, lang, w, c):
        # Any part of the Bible: อิสยาห์ is Isaiah's, even in the verses that also say "son of Amoz".
        for part in ("ot", "nt", "all"):
            o = owner.get((lang, part, c["text"]))
            if o is not None and o[0] != w and o[1] >= c["f"] + 0.05:
                return True
        return False

    def match(w, keys, owner):
        # The sound check comes first, so a cut-off piece (โลมอน) can't hide the whole name (โซโลมอน).
        th_c = [c for c in th.candidates(keys, lambda g: th_ok(g) and thai_sound_ok(w, g) and th_sound_enough(w, g)
                                         and not th_glued(g, keys, th.verses))
                if not taken(owner, "th", w, c)]
        for c in th_c:
            c["kjv"] = kjv_support(c["text"], keys)
        zh_fits = lambda g: zh_ok(g) and zh_sound_ok(w, g) and zh_len_ok(w, g)   # noqa: E731
        zh_c = [c for c in zh.candidates(keys, zh_fits) if not taken(owner, "zh", w, c)]
        tw_c = tw.candidates(keys, zh_fits, many=3)
        # Thai: the best form both Thai Bibles use; else a very clear Freedom Bible form. Ties go to
        # the one that sounds more like the English (Halah ฮาลาห์, not Habor ฮาโบร์ in the same verses).
        th_c.sort(key=lambda c: (-c["f"], -c["kjv"], -thai_sound_score(w, c["text"])))
        agreed = [c for c in th_c if c["kjv"] >= 0.5 and c["f"] >= 0.4]
        t = agreed[0] if agreed else (th_c[0] if th_c and th_c[0]["f"] >= 0.8 else None)
        z = zh_c[0] if zh_c and zh_c[0]["f"] >= ZH_MIN else None
        z2 = tw_c[0] if z and tw_c and tw_c[0]["f"] >= ZH_MIN and same_name(z["text"], tw_c[0]["text"]) else None
        if z and not z2 and len(keys) < RARE:
            z = None                  # a rare name both Chinese Bibles don't agree on is a guess
        return th_c, zh_c, t, z, z2

    # First pass: the names that are sure, so their pieces don't count as other names.
    owner, sure_th, sure_zh, sure_tw = {}, [], [], []
    for i, (w, (bid, keys)) in enumerate(order):
        th_c, zh_c, t, z, z2 = match(w, keys, {})
        for lang, c in (("th", t), ("zh", z)):
            if c:
                key = (lang, c["part"], c["text"])
                if key not in owner or owner[key][1] < c["f"]:
                    owner[key] = (w, c["f"])
        if t and t["f"] >= 0.8:
            sure_th.append(t["text"])
        if z and z["f"] >= 0.8:
            sure_zh.append(z["text"])
            if z2:
                sure_tw.append(z2["text"])
        if i % 200 == 0:
            print(f"pass 1: {i}/{len(order)}", flush=True)
    th.learn_sure(sure_th)
    zh.learn_sure(sure_zh)
    tw.learn_sure(sure_tw)

    names, report = {}, []
    for i, (w, (bid, keys)) in enumerate(order):
        th_c, zh_c, t, z, z2 = match(w, keys, owner)
        entry = {}
        if t:
            entry["th"] = t["text"]
            top = th_c[0]
            if top is not t and top["f"] >= 0.5 and top["text"] not in t["text"] and t["text"] not in top["text"]:
                # The two Thai Bibles disagree: keep the one that sounds like the English (Husham is
                # ฮูชาม, not อโยบับ from the same verses), and the other only if it sounds right too.
                # Only a clear gap swaps them: นาโชน (both Bibles) stays ahead of นาห์โชน.
                a, b = t["text"], top["text"]
                if thai_sound_share(w, b) - thai_sound_share(w, a) >= 0.3:
                    a, b = b, a
                if thai_sound_share(w, a) < 0.5:
                    del entry["th"]
                else:
                    entry["th"] = a
                    if thai_sound_share(w, b) >= 0.5:
                        entry["th_also"] = [b]      # the other Bible's spelling (not a cut or a run-on)
        if z:
            entry["zh"] = z["text"]
            if z2:
                entry["zh-TW"] = z2["text"]
        report.append("\t".join(str(x) for x in (
            w, bid, len(keys), (t or {}).get("part", "-"),
            (t or {}).get("text", "-"), (t or {}).get("f", 0), (t or {}).get("kjv", 0),
            ",".join(entry.get("th_also", [])),
            (z or {}).get("text", "-"), (z or {}).get("f", 0), (z2 or {}).get("text", "-"),
            " ".join(f"{c['text']}/{c['f']}/{c['kjv']}" for c in th_c[:4]),
            " ".join(f"{c['text']}/{c['f']}" for c in zh_c[:3]))))
        if entry:
            entry["verses"] = len(keys)
            names[w] = entry
        if i % 200 == 0:
            print(f"pass 2: {i}/{len(order)}", flush=True)

    # Hand-set names, each checked against the Bible text.
    th_all = "\n".join(th.verses.values()) + "\n".join(kjv.values())
    zh_all, tw_all = "\n".join(zh.verses.values()), "\n".join(tw.verses.values())
    for w, h in HAND.items():
        entry = dict(names.get(w, {}))
        for key, text, where in (("th", h.get("th"), th_all), ("zh", h.get("zh"), zh_all),
                                 ("zh-TW", h.get("tw"), tw_all)):
            if not text:
                continue
            if text not in where:
                print(f"HAND {w} {key} {text}: not in the Bible text, left out", flush=True)
                continue
            entry[key] = text
        entry["th_also"] = [t for t in dict.fromkeys(entry.get("th_also", []))
                            if t not in entry.get("th", "") and entry.get("th", "") not in t]
        if not entry["th_also"]:
            del entry["th_also"]
        entry["verses"] = max(entry.get("verses", 0), seen.get(w, 0))
        names[w] = entry
        report.append(f"{w}\thand\t{entry['verses']}\t-\t{entry.get('th', '-')}\t1\t-\t"
                      f"{','.join(entry.get('th_also', []))}\t{entry.get('zh', '-')}\t1\t{entry.get('zh-TW', '-')}\t\t")
    with open(os.path.join(REPORT, "names.tsv"), "w", encoding="utf-8") as f:
        f.write("en\tbible\tverses\tpart\tth\tth_f\tth_kjv\tth_also\tzh\tzh_f\tzh-TW\tth_candidates\tzh_candidates\n")
        f.write("\n".join(report) + "\n")

    # Books: the titles printed in each Bible.
    th_h, kjv_h = book_headers(data, "thafb"), book_headers(data, "thaKJV")
    zh_h, tw_h = book_headers(data, "cmn-cu89s"), book_headers(data, "cmn-cu89t")
    books = []
    for code, en in BOOKS:
        num = en[0] if en[0].isdigit() else None
        base = en[2:] if num else en
        th_name = TH_BOOK.get(code) or re.sub(r"^(\d)\s*", r"\1 ", th_h.get(code, ""))
        books.append(dict(code=code, en=base, num=num, say=BOOK_SAY.get(base, [base]),
                          th=th_name, th_kjv=kjv_h.get(code, ""),
                          zh=zh_h.get(code, ""), **{"zh-TW": tw_h.get(code, "")}))

    # Terms: confirm each church word is really used in the Bibles.
    terms = []
    th_text = "\n".join(th.verses.values()).replace("\u200b", "")
    kjv_text = "\n".join(kjv.values())
    zh_text = "\n".join(zh.verses.values())
    tw_text = "\n".join(tw.verses.values())
    for t in TERMS:
        found = dict(th=th_text.count(t["th"]) + kjv_text.count(t["th"]), zh=zh_text.count(t["zh"]),
                     tw=tw_text.count(t["tw"]))
        terms.append(dict(en=t["en"], th=t["th"], zh=t["zh"], **{"zh-TW": t["tw"]}, found=found))

    doc = dict(
        about="Bible names, books and church words, spelled the way the Thai and Chinese Bibles spell "
              "them. Built by tools/bible/build_bible_words.py; misses added by probe_bible_words.py.",
        sources=SOURCES, names=names, books=books, terms=terms, miss={})
    if os.path.exists(OUT):
        # Keep the translator's known misses (probe_bible_words.py), minus any that are now a name's
        # Bible spelling.
        with open(OUT, encoding="utf-8") as f:
            old = json.load(f).get("miss", {})
        spelled = {t: {e[t] for e in names.values() if e.get(t)} for t in ("th", "zh", "zh-TW")}
        doc["miss"] = {t: {k: [m for m in ms if k not in names or m not in spelled.get(t, ())]
                           for k, ms in by.items()} for t, by in old.items()}
        doc["miss"] = {t: {k: ms for k, ms in by.items() if ms} for t, by in doc["miss"].items()}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print(f"names {len(names)} (th {sum('th' in e for e in names.values())}, "
          f"zh {sum('zh' in e for e in names.values())}), books {len(books)}, terms {len(terms)}")
    for t in terms:
        print("term", t["en"], t["found"])
    for b in books:
        print("book", b["code"], b["en"], b["num"] or "", b["th"], "|", b["th_kjv"], "|", b["zh"], b["zh-TW"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("data", nargs="?", default=os.path.expanduser("~/bible-data"))
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    build(a.data)
