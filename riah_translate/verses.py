"""Shows the real Bible verse when the preacher reads one.

When the preacher reads John 3:16, the Thai screen should show John 3:16 from a Thai Bible, not a
machine translation of what he said. This module listens for that:

* References: "John 3:16", "John three sixteen", "John chapter 3 verse 16", "Romans 8 verse 28",
  "verses 28 to 30", a bare "verse 29" (same chapter as the last one), "turn with me to Psalm 23",
  "First Corinthians 13". A reference opens a window (that verse to the end of the chapter, or the
  whole chapter) where a line only has to match loosely.
* Readings: each finished line is lined up word by word against three English Bibles (Berean
  Standard, World English, King James) so it still matches when he reads the NIV or ESV. Without a
  reference the match must be long and close. Anything of his own in the line ("listen, church",
  "Paul writes") means it's his words, not the reading: no match, and the line is translated as usual.

What a line gets (lines are cut at sentence ends, pauses or 22 words):
* the line where a verse starts: the whole verse from the Thai/Chinese Bible, with its reference;
* later lines of the same verse: "continued" (nothing new to show);
* a line that finishes one verse and starts the next: the next verse.

    v = Verses.load()                 # once per service; the first load takes a second or two
    v.expect_refs(notes["refs"])      # optional: references from the sermon notes
    hit = v.hear(line, ["th", "zh"])  # each finished English line
    # None: translate as usual. Otherwise see Verses.match.
"""
from __future__ import annotations

import json
import lzma
import os
import re
import threading
from bisect import bisect_right
from difflib import SequenceMatcher
from typing import NamedTuple

from .known import Index, align, number_groups, number_kind, tokens, STOP_STEMS

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, "bible")
BIBLE_LANGS = ("th", "zh", "zh-TW")       # languages with a Bible shipped (see tools/bible/build_verses.py)


# -- book, chapter and verse numbers ---------------------------------------------------------------

class Books:
    """Verse numbers: every verse of the Bible has a number (0 = Genesis 1:1), the same in every file."""

    def __init__(self, books):
        self.order = [code for code, _ in books]
        self.counts = {code: list(chapters) for code, chapters in books}
        self.first = {}
        self._starts, self._keys = [], []
        vid = 0
        for code, chapters in books:
            for c, n in enumerate(chapters, 1):
                self.first[(code, c)] = vid
                self._starts.append(vid)
                self._keys.append((code, c, n))
                vid += n
        self.total = vid

    def ok(self, code, c, v=None):
        ch = self.counts.get(code)
        return bool(ch) and 1 <= c <= len(ch) and (v is None or 1 <= v <= ch[c - 1])

    def vid(self, code, c, v=1):
        return self.first[(code, c)] + v - 1

    def where(self, vid):
        """(book, chapter, verse) of a verse number."""
        i = bisect_right(self._starts, vid) - 1
        code, c, _n = self._keys[i]
        return code, c, vid - self._starts[i] + 1

    def chapter_span(self, vid):
        i = bisect_right(self._starts, vid) - 1
        return self._starts[i], self._starts[i] + self._keys[i][2] - 1


_BOOKS = None
_BOOKS_LOCK = threading.Lock()


def books(folder: str = DIR) -> Books | None:
    global _BOOKS
    with _BOOKS_LOCK:
        if _BOOKS is None:
            try:
                with open(os.path.join(folder, "books.json"), encoding="utf-8") as f:
                    _BOOKS = Books(json.load(f)["books"])
            except (OSError, ValueError, KeyError):
                return None
        return _BOOKS


# -- book names: what's said and what's shown ------------------------------------------------------

# Short forms seen in written notes ("Rom 8:28", "1 Cor 13"). Only taken with a number after them.
_ABBR = {
    "GEN": "gen gn", "EXO": "ex exo exod", "LEV": "lev lv", "NUM": "num nm", "DEU": "deut deu dt",
    "JOS": "josh jos", "JDG": "judg jdg", "RUT": "rth", "SA": "sam sa sm", "KI": "kgs ki kin",
    "CH": "chr chron", "EZR": "ezr", "NEH": "neh", "EST": "esth est", "JOB": "jb",
    "PSA": "ps psa pss", "PRO": "prov prv pro", "ECC": "eccl ecc eccles qoh", "SNG": "song sos",
    "ISA": "isa", "JER": "jer", "LAM": "lam", "EZK": "ezek eze ezk", "DAN": "dan dn", "HOS": "hos",
    "JOL": "jl", "AMO": "amo", "OBA": "obad ob", "JON": "jon jnh", "MIC": "mic", "NAM": "nah",
    "HAB": "hab", "ZEP": "zeph zep", "HAG": "hag", "ZEC": "zech zec", "MAL": "mal",
    "MAT": "matt mt mat", "MRK": "mk mrk mar", "LUK": "lk luk", "JHN": "jn jhn joh", "ACT": "ac act",
    "ROM": "rom rm", "CO": "cor co", "GAL": "gal", "EPH": "eph", "PHP": "phil php",
    "COL": "col", "TH": "thess thes th", "TI": "tim ti", "TIT": "tit", "PHM": "philem phlm phm",
    "HEB": "heb", "JAS": "jas jm", "PE": "pet pe pt", "JN": "jn jhn jo", "JUD": "jud", "REV": "rev rv",
}
_EN_SHOW = {"PSA": "Psalm"}
_ORDINAL = {"first": 1, "second": 2, "third": 3, "1st": 1, "2nd": 2, "3rd": 3}


class _Names:
    def __init__(self):
        from .bible_words import bible_words
        bw = bible_words()
        table = list(bw.books.values()) if bw else []
        self.show = {}               # code -> {lang: name}
        self.said = {}               # first word -> [(words, number, code, short form)]
        for b in table:
            num = int(b["num"]) if b.get("num") else None
            en = _EN_SHOW.get(b["code"], b["en"])
            show = {"en": f"{num} {en}" if num else en}
            for lang in BIBLE_LANGS:
                if b.get(lang):
                    show[lang] = b[lang]
            self.show[b["code"]] = show
            for say in b.get("say") or [b["en"]]:
                self._add(say.lower().split(), num, b["code"], False)
            key = b["code"][1:] if num else b["code"]
            for ab in _ABBR.get(key, "").split():
                self._add([ab], num, b["code"], True)
        for lst in self.said.values():
            lst.sort(key=lambda e: -len(e[0]))

    def _add(self, words, num, code, short):
        self.said.setdefault(words[0], []).append((tuple(words), num, code, short))

    def label(self, lang, code, c, v=None, end=None):
        name = self.show.get(code, {}).get(lang) or self.show.get(code, {}).get("en") or code
        ref = f"{c}:{v}" if v else f"{c}"
        if v and end and end != v:
            ref += f"-{end}"
        return f"{name} {ref}"


_NAMES = None


def _names() -> _Names:
    global _NAMES
    with _BOOKS_LOCK:
        if _NAMES is None:
            _NAMES = _Names()
        return _NAMES


# -- reading references out of a line -------------------------------------------------------------

class Ref(NamedTuple):
    """A Bible reference. verse None: the whole chapter. end: last verse of a range."""
    book: str
    chapter: int
    verse: int | None = None
    end: int | None = None

    def __str__(self):
        s = f"{self.book} {self.chapter}"
        if self.verse:
            s += f":{self.verse}"
            if self.end and self.end != self.verse:
                s += f"-{self.end}"
        return s


class _T:
    __slots__ = ("kind", "val", "a", "b", "raw", "cap")

    def __init__(self, kind, val, a, b, raw="", cap=False):
        self.kind, self.val, self.a, self.b, self.raw, self.cap = kind, val, a, b, raw, cap


_RTOK = re.compile(r"\d+(?:st|nd|rd|th)\b|[A-Za-z]+(?:['’][A-Za-z]+)?|\d+|[:.,;\-–—]")


def _ref_tokens(text):
    text = re.sub(r"(?<=[A-Za-z])-(?=[A-Za-z])", " ", text)      # "twenty-eight"
    out, run = [], []

    def flush():
        if not run:
            return
        groups = number_groups([k for k, _a, _b in run])
        # "one oh three" is Psalm 103
        if len(groups) == 3 and groups[1][0] == 0 and 1 <= groups[0][0] <= 9 and groups[2][0] <= 9:
            groups = [(groups[0][0] * 100 + groups[2][0], groups[0][1], groups[2][2])]
        for n, i, j in groups:
            out.append(_T("n", n, run[i][1], run[j][2], "words"))
        run.clear()

    for m in _RTOK.finditer(text):
        raw = m.group(0)
        low = raw.lower()
        if raw[0].isalpha():
            nk = number_kind(low)
            if nk is None and low == "oh" and run and run[-1][0][0] == "unit":
                nk = ("unit", 0)
            if nk:
                run.append((nk, m.start(), m.end()))
                continue
            if low == "and" and run and run[-1][0][0] == "scale":
                continue
        flush()
        if raw[0].isdigit() and raw.isdigit():
            out.append(_T("n", int(raw), m.start(), m.end(), raw))
        elif raw[0].isalnum():
            out.append(_T("w", low.replace("’", "'"), m.start(), m.end(), raw, raw[0].isupper()))
        else:
            out.append(_T("p", raw, m.start(), m.end(), raw))
    flush()
    return out


def _w(T, k):
    return T[k].val if k < len(T) and T[k].kind == "w" else None


def _n(T, k):
    return T[k].val if k < len(T) and T[k].kind == "n" else None


def _p(T, k):
    return T[k].val if k < len(T) and T[k].kind == "p" else None


_VERSE_WORDS = ("verse", "verses", "vs", "v", "vv")
_CHAPTER_WORDS = ("chapter", "chapters", "ch", "chap")
_RANGE_WORDS = ("-", "–", "—", "to", "through", "thru", "and", "till", "until")


def _range(T, k, v):
    """'... to 30': (end verse, next token)."""
    sep = _p(T, k) or _w(T, k)
    e = _n(T, k + 1)
    if sep in _RANGE_WORDS and e is not None and e > v:
        if sep == "and" and _w(T, k + 2) in _VERSE_WORDS + _CHAPTER_WORDS:
            return None, k
        return e, k + 2
    return None, k


def _chapter_verse(T, k):
    """Numbers after a book's name: (chapter, verse, end verse, next token, clearly a reference)."""
    clear = False
    if _p(T, k) == "," and _w(T, k + 1) in _CHAPTER_WORDS:     # "the Gospel of John, chapter three"
        k += 1
    if _w(T, k) in _CHAPTER_WORDS:
        clear = True
        k += 1
        if _p(T, k) == ".":
            k += 1
    c = _n(T, k)
    if c is None:
        return None
    k += 1
    v = end = None
    sep = _p(T, k)
    nxt = _n(T, k + 1)
    if sep == ":" and nxt is not None:
        v, k, clear = nxt, k + 2, True
    elif sep == "." and nxt is not None and T[k].a == T[k - 1].b and T[k + 1].a == T[k].b:
        v, k, clear = nxt, k + 2, True                         # "3.16"
    elif sep == "," and nxt is not None and T[k + 1].raw != "words" and T[k + 1].a - T[k].b <= 1:
        v, k = nxt, k + 2                                       # "3, 16"
    else:
        kk = k + 1 if sep == "," else k
        if _w(T, kk) in _VERSE_WORDS and _n(T, kk + 1) is not None:
            v, k, clear = _n(T, kk + 1), kk + 2, True
        elif _n(T, k) is not None and T[k].raw == "words" and T[k - 1].raw == "words":
            v, k = _n(T, k), k + 1                              # "three sixteen"
    if v is not None:
        end, k = _range(T, k, v)
    return c, v, end, k, clear


def _settle(bk, code, c, v, end):
    """Make the numbers a real place in the book, or None."""
    if not bk.ok(code, c):
        s = str(c)
        if v is None and c >= 100:          # "John 316"
            for cut in (1, 2):
                if s[cut:] and s[cut] != "0" and bk.ok(code, int(s[:cut]), int(s[cut:])):
                    return int(s[:cut]), int(s[cut:]), None
        return None
    if v is not None and not bk.ok(code, c, v):
        if v < 100 and bk.ok(code, c * 100 + v):              # "Psalm one nineteen"
            return c * 100 + v, None, None
        return c, None, None
    if end is not None:
        end = min(end, bk.counts[code][c - 1])
    return c, v, end


def _book_at(T, i, nm):
    """A book's name starting at token i: (code, token after it, the name token, short form)."""
    o = None
    t = T[i]
    if t.kind == "n" and t.val in (1, 2, 3):
        o = t.val
    elif t.kind == "w" and t.val in _ORDINAL:
        o = _ORDINAL[t.val]
    elif t.kind == "w" and t.raw in ("I", "II", "III"):
        o = len(t.raw)
    j = i + 1 if o else i
    first = _w(T, j)
    for words, num, code, short in nm.said.get(first, ()):
        if num != o:
            continue
        if all(_w(T, j + q) == words[q] for q in range(len(words))):
            k = j + len(words)
            if short and _p(T, k) == "." and T[k].a == T[k - 1].b:
                k += 1
            return code, k, T[j], short
    return None


def find_refs(text: str, last: Ref | None = None):
    """Bible references in a line: [(Ref, (start, end) in the text)]. `last` is the reference before,
    for a bare "verse 29" or "chapter 5"."""
    bk = books()
    if not text or bk is None:
        return []
    nm = _names()
    T = _ref_tokens(text)
    out = []
    i = 0
    while i < len(T):
        hit = _book_at(T, i, nm) if T[i].kind in ("w", "n") else None
        if hit:
            code, k, name, short = hit
            cv = _chapter_verse(T, k)
            if cv:
                c, v, end, k2, clear = cv
                # A book's name needs a capital ("Mark 2", not "mark two things") unless the numbers
                # are clearly a reference. A short form ("Rom 8:28") needs a capital and a verse.
                if (not short and (name.cap or clear)) or \
                        (short and name.cap and (clear or v is not None or code == "PSA")):
                    got = _settle(bk, code, c, v, end)
                    if got:
                        last = Ref(code, *got)
                        out.append((last, (T[i].a, T[k2 - 1].b)))
                        i = k2
                        continue
        w = _w(T, i)
        j = i + 2 if w in _VERSE_WORDS[2:] and _p(T, i + 1) == "." else i + 1    # "vv. 28-30" in notes
        if last is not None and w in _VERSE_WORDS and _n(T, j) is not None \
                and (w in _VERSE_WORDS[:2] or j == i + 2 or w == "vs"):
            v = _n(T, j)
            end, k = _range(T, j + 1, v)
            got = _settle(bk, last.book, last.chapter, v, end)
            if got and got[1]:
                last = Ref(last.book, *got)
                out.append((last, (T[i].a, T[k - 1].b)))
                i = k
                continue
        if last is not None and w in _CHAPTER_WORDS[:2]:
            cv = _chapter_verse(T, i)
            if cv:
                c, v, end, k, _clear = cv
                got = _settle(bk, last.book, c, v, end)
                if got:
                    last = Ref(last.book, *got)
                    out.append((last, (T[i].a, T[k - 1].b)))
                    i = k
                    continue
        i += 1
    return out


_CODE = re.compile(r"^\s*([1-3]?[A-Z]{2,3})\s+(\d+)(?::(\d+)(?:\s*-\s*(\d+))?)?\s*$")


def parse_ref(text) -> Ref | None:
    """One reference written out: "JHN 3:16-18" (as notes.read_notes gives them) or "John 3:16"."""
    if isinstance(text, Ref):
        return text
    bk = books()
    m = _CODE.match(str(text or ""))
    if m and bk and m.group(1) in bk.counts:
        got = _settle(bk, m.group(1), int(m.group(2)), int(m.group(3)) if m.group(3) else None,
                      int(m.group(4)) if m.group(4) else None)
        return Ref(m.group(1), *got) if got else None
    found = find_refs(str(text or ""))
    return found[0][0] if found else None


# Words around a reference that only introduce the reading: "turn with me to", "it says".
_LEAD_IN = re.compile(
    r"(?:\b(?:(?:please\s+)?(?:turn|open|go|look|flip|read|turning|opening)(?:\s+(?:with\s+me|your\s+bibles?|"
    r"in\s+your\s+bibles?|over|back))*|let'?s\s+(?:read|look|turn|go)|let\s+us\s+(?:read|look|turn)|"
    r"we\s+(?:read|see))\s*(?:to|at|in|from)?\s*)?(?:\b(?:in|from|at|to)\s+)?"
    r"(?:the\s+(?:book|gospel|letter)\s+(?:of|to)\s+(?:the\s+)?)?$", re.I)
_LEAD_OUT = re.compile(
    r"^\s*[,:;.]?\s*(?:(?:(?:and\s+)?(?:it|this|he|she|paul|jesus|god|the\s+bible|scripture)\s+)?"
    r"(?:says|said|reads|writes|wrote|tells\s+us|declares|puts\s+it)\b)?\s*[,:;.]?\s*", re.I)


def strip_refs(text: str, found=None):
    """The line without its references and the words that only introduce them."""
    found = find_refs(text) if found is None else found
    for _ref, (a, b) in reversed(found):
        head = _LEAD_IN.sub("", text[:a])
        tail = _LEAD_OUT.sub("", text[b:], count=1)
        text = head + " " + tail
    return text.strip()


# -- the English Bibles and the word index ---------------------------------------------------------

def _read_xz(path):
    # Reading the whole file and unpacking it in one go is ~10x faster than lzma.open's stream.
    with open(path, "rb") as f:
        return json.loads(lzma.decompress(f.read()).decode("utf-8"))


class Bible:
    """The English Bibles (for recognising a verse) and the verse texts to show. Load once per
    process with bible(); the target Bibles load the first time a language asks for them."""

    def __init__(self, folder: str = DIR):
        doc = _read_xz(os.path.join(folder, "en.json.xz"))
        self.folder = folder
        self.books = Books(doc["books"])
        self.versions = doc["versions"]
        self.index = Index(doc["text"])
        self.langs = tuple(l for l in BIBLE_LANGS if os.path.exists(os.path.join(folder, f"{l}.json.xz")))
        self._text = {}
        self._words = {}
        self._lock = threading.Lock()

    def words(self, vid):
        """Every word stem of a verse, in any of the English versions."""
        got = self._words.get(vid)
        if got is None:
            got = self._words[vid] = frozenset(w for t in self.index.toks[vid] for w in t)
        return got

    def text(self, lang):
        """The verse texts of one target language (a list by verse number), or None."""
        if lang not in self.langs:
            return None
        with self._lock:
            if lang not in self._text:
                try:
                    doc = _read_xz(os.path.join(self.folder, f"{lang}.json.xz"))
                    self._text[lang] = doc["text"] if len(doc["text"]) == self.books.total else None
                except (OSError, ValueError, KeyError, lzma.LZMAError):
                    self._text[lang] = None
            return self._text[lang]


_BIBLE = None
_BIBLE_LOCK = threading.Lock()


def bible(folder: str = DIR) -> Bible | None:
    global _BIBLE
    with _BIBLE_LOCK:
        if _BIBLE is None:
            try:
                _BIBLE = Bible(folder)
            except (OSError, ValueError, KeyError, lzma.LZMAError):
                return None
        return _BIBLE


# -- following the reading line by line ------------------------------------------------------------

class _Read:
    """One way a line can be read as Bible text (internal)."""
    __slots__ = ("start", "first", "last", "new", "score", "frac", "fit")


class Verses:
    """Follows the preacher through the Bible readings of one service. Safe to call from more than
    one thread (one lock); cheap to make once the Bible is loaded."""

    KEEP_LINES = 60          # a spoken reference stays open this many lines
    MAX_WINDOWS = 4
    STAY_LINES = 2           # a reading may pause this many lines and still continue
    LOOSE = 0.7              # how much of the line must be Bible text inside a window
    STRICT = 0.85            # ... with no reference
    STRICT_WORDS = 8         # ... and at least this many words
    ORDER = 0.6              # how much of the line must be the verse's words in the verse's order
    STRICT_ORDER = 0.75      # ... with no reference

    def __init__(self, bib: Bible):
        self.bible = bib
        self.names = _names()
        self._lock = threading.Lock()
        self.reset()

    @classmethod
    def load(cls, folder: str = DIR, langs=()) -> "Verses | None":
        """None if the Bible files are missing. The first call in a process reads and indexes the
        English Bibles (a second or two); later calls are instant. `langs`: target languages to
        read now rather than at the first verse (each takes a fraction of a second)."""
        b = bible(folder)
        if not b:
            return None
        for lang in langs or ():
            b.text(lang)
        return cls(b)

    def reset(self):
        with self._lock:
            self._last = None        # the last reference said (for "verse 29")
            self._spoken = []        # windows from references said: [first verse, last verse, lines left]
            self._noted = []         # windows from the sermon notes (stay for the service)
            self._cur = None         # the reading being followed
            self._shown = {}         # lang -> verse numbers whose text was shown last

    # -- references ------------------------------------------------------------------------------
    def expect(self, line: str):
        """Note any references in a line ("turn with me to John 3"). Returns them."""
        with self._lock:
            return self._expect(find_refs(line, self._last))

    def _expect(self, found):
        bk = self.bible.books
        refs = []
        for ref, _span in found:
            if not bk.ok(ref.book, ref.chapter):
                continue
            a = bk.vid(ref.book, ref.chapter, ref.verse or 1)
            z = bk.chapter_span(a)[1]
            self._open(self._spoken, a, z, self.KEEP_LINES)
            self._last = ref
            self._cur = None
            refs.append(ref)
        return refs

    def _open(self, windows, a, z, lines):
        for w in windows:
            if w[1] == z and w[0] <= a:          # already open (same chapter, from earlier on)
                w[2] = lines
                windows.remove(w)
                windows.append(w)
                return
        windows[:] = [w for w in windows if not (w[1] == z and w[0] > a)]
        windows.append([a, z, lines])
        del windows[:-self.MAX_WINDOWS]

    def expect_refs(self, refs):
        """References known ahead of time (from the sermon notes): ["JHN 3:16-18", "Psalm 23", ...].
        Replaces the ones given before."""
        bk = self.bible.books
        noted = []
        for r in refs or []:
            ref = parse_ref(r)
            if ref is None or not bk.ok(ref.book, ref.chapter):
                continue
            a = bk.vid(ref.book, ref.chapter, ref.verse or 1)
            lo, hi = bk.chapter_span(a)
            z = hi if not ref.verse else min(hi, bk.vid(ref.book, ref.chapter, ref.end or ref.verse) + 2)
            noted.append([a, z, None])
        with self._lock:
            self._noted = noted[:60]

    # -- lines -----------------------------------------------------------------------------------
    def hear(self, line: str, tgts):
        """expect() and match() in one go, for each finished line."""
        with self._lock:
            found = find_refs(line, self._last)
            self._expect(found)
            return self._match(line, tgts, found)

    def match(self, line: str, tgts):
        """Is this line the preacher reading the Bible?

        None: no (or no target language has a Bible) - translate the line as usual.
        {"kind": "verse", ...}: a verse starts in this line. Show text[lang] with label[lang]
            instead of a translation, for each lang in "langs"; a lang in "langs" with no text has
            already shown this verse (nothing new). Translate as usual for tgts not in "langs".
        {"kind": "continued", ...}: more of a verse already shown; nothing new for "langs".
        Both also carry "ref" ("John 3:16", English) and "code" ("JHN 3:16")."""
        with self._lock:
            return self._match(line, tgts, find_refs(line, self._last))

    def _match(self, line, tgts, found):
        langs = [t for t in (tgts or []) if t in self.bible.langs]
        for w in self._spoken:
            w[2] -= 1
        self._spoken = [w for w in self._spoken if w[2] > 0]
        if not langs:
            return None
        stems, _ = tokens(strip_refs(line, found))
        hit = None
        if stems:
            sm = SequenceMatcher(None, autojunk=False)
            sm.set_seq2(stems)
            hit = (self._continue(stems, sm) or self._in_windows(stems, sm)
                   or self._anywhere(stems, sm))
        if hit is None:
            if self._cur is not None:
                self._cur["ago"] += 1
                if self._cur["ago"] > self.STAY_LINES:
                    self._cur = None
            return None
        kind, r = hit
        upto = max(r.new) if r.new else self._cur["upto"]
        self._cur = dict(base=r.last, frac=r.frac, upto=upto, ago=0)
        lo, hi = self.bible.books.chapter_span(r.last)
        self._open(self._spoken, r.last, hi, self.KEEP_LINES)
        return self._result(kind, r.new or [self._cur["upto"]], langs)

    def _reads(self, stems, sm, start, mode, after=None):
        """Read the line against the verses from `start` on (a few, same chapter). mode: "next"
        (carries on the reading), "window" (a reference was said), "anywhere"."""
        bib = self.bible
        idx = bib.index
        end = min(bib.books.chapter_span(start)[1], start + 3)
        present = set()
        streams = []
        for ver in range(len(bib.versions)):
            seq, bounds = [], []
            for vid in range(start, end + 1):
                bounds.append(len(seq))
                seq.extend(idx.toks[vid][ver])
            bounds.append(len(seq))
            streams.append((seq, bounds))
            present.update(seq)
        n = len(stems)
        best = None
        for ver, (seq, bounds) in enumerate(streams):
            vlen = bounds[1] - bounds[0]
            if not vlen:
                continue
            f = align(stems, seq, present, sm)
            if f.bad or f.first < 0:
                continue
            first_v = bisect_right(bounds, f.first) - 1
            last_v = min(bisect_right(bounds, f.last) - 1, len(bounds) - 2)
            at_start = any(s <= f.first <= s + 3 for s in idx.starts[start][ver]) if first_v == 0 else False
            if mode == "next":
                pos = round(after * vlen) - 1
                if not (pos - 3 <= f.first <= pos + 8):
                    continue
                if f.cover < self.LOOSE or f.order < self.ORDER or (n < 4 and (f.cover < 0.9 or f.absent)):
                    continue
                if first_v > 1:
                    continue
            elif mode == "window":
                whole_short = (vlen <= 4 and f.first == 0 and f.last >= vlen - 1 and f.cover >= 0.9
                               and n <= vlen + 1)
                if first_v != 0 or f.edge or f.order < self.ORDER:
                    continue
                if not ((at_start and f.cover >= self.LOOSE and n >= 4) or whole_short
                        or (f.cover >= self.STRICT and n >= 6)):
                    continue
            else:
                if first_v != 0 or not at_start or f.cover < self.STRICT or n < self.STRICT_WORDS \
                        or f.edge or f.order < self.STRICT_ORDER:
                    continue
                inside = min(f.last, vlen - 1) - f.first + 1
                content_hits = sum(1 for b in f.blocks for q in range(b.size) if seq[b.a + q] not in STOP_STEMS)
                # Enough of the verse's own words, read in order: four or more, or three when the
                # line is nearly the whole verse ("I can do all this through him who gives me strength").
                if not ((content_hits >= 4 and (inside >= 0.6 * vlen or f.hit >= 10))
                        or (content_hits >= 3 and f.first <= 1 and inside >= 0.8 * vlen)):
                    continue
            # Which verses start in this line.
            new = []
            for u in range(first_v, last_v + 1):
                lo, hi = bounds[u], bounds[u + 1]
                got = [b.a + q for b in f.blocks for q in range(b.size) if lo <= b.a + q < hi]
                if not got:
                    continue
                starts_here = (u == first_v and (mode != "next" or at_start or f.first == lo)) or \
                              (got[0] - lo <= 3 and len(got) >= 2 and any(seq[p] not in STOP_STEMS for p in got))
                if u == first_v and mode != "next":
                    starts_here = True
                if starts_here:
                    new.append(start + u)
            r = _Read()
            r.start, r.fit = start, f
            r.first, r.last = start + first_v, start + last_v
            r.new = new
            vl = bounds[last_v + 1] - bounds[last_v]
            r.frac = (f.last + 1 - bounds[last_v]) / vl if vl else 1.0
            r.score = f.cover + 0.5 * f.order + 0.1 * min(1.0, (min(f.last, vlen - 1) - f.first + 1) / vlen)
            if best is None or r.score > best.score:
                best = r
        return best

    def _continue(self, stems, sm):
        cur = self._cur
        if cur is None:
            return None
        r = self._reads(stems, sm, cur["base"], "next", after=cur["frac"])
        if r is None:
            return None
        r.new = [u for u in r.new if u > cur["upto"]]
        if r.new and r.new[0] != cur["upto"] + 1:
            return None
        return ("verse" if r.new else "continued"), r

    def _in_windows(self, stems, sm):
        windows = self._spoken + self._noted
        if not windows:
            return None
        idx = self.bible.index
        want = {s for s in stems if s not in STOP_STEMS}
        if not want:
            want = set(stems)
        need = min(2, len(want))
        seen, cand = set(), []
        near = (self._cur["upto"] + 1) if self._cur else None
        for a, z, _left in reversed(windows):
            for vid in range(a, z + 1):
                if vid in seen:
                    continue
                seen.add(vid)
                got = len(want & self.bible.words(vid))
                if got >= need:
                    cand.append((-got, abs(vid - near) if near is not None else vid - a, vid))
        cand.sort()
        best = None
        for _g, dist, vid in cand[:4]:
            r = self._reads(stems, sm, vid, "window")
            if r is not None and (best is None or r.score > best[0].score + 0.02
                                  or (abs(r.score - best[0].score) <= 0.02 and dist < best[1])):
                best = (r, dist)
        return ("verse", best[0]) if best else None

    def _anywhere(self, stems, sm):
        if len(stems) < self.STRICT_WORDS:
            return None
        reads = []
        for vid, _c in self.bible.index.candidates(stems, limit=6, need=3):
            r = self._reads(stems, sm, vid, "anywhere")
            if r is not None:
                reads.append(r)
        if not reads:
            return None
        reads.sort(key=lambda r: -r.score)
        best = reads[0]
        for other in reads[1:]:
            if not (best.first <= other.first <= best.last) and other.score >= best.score - 0.03:
                return None                     # two different verses fit as well: don't guess
        return "verse", best

    # -- what to show ----------------------------------------------------------------------------
    def _result(self, kind, vids, langs):
        bk = self.bible.books
        code, c, v = bk.where(vids[0])
        v_end = bk.where(vids[-1])[2]
        out = {"kind": kind, "ref": self.names.label("en", code, c, v, v_end),
               "code": str(Ref(code, c, v, v_end)), "label": {}, "text": {}, "langs": []}
        lo_ch = bk.chapter_span(vids[0])[0]
        for lang in langs:
            texts = self.bible.text(lang)
            if texts is None:
                continue
            out["langs"].append(lang)
            if kind == "continued":
                out["label"][lang] = self.names.label(lang, code, c, v, v_end)
                continue
            # A verse with no text of its own in this Bible is joined to the verse before it.
            src = []
            for vid in vids:
                u = vid
                while u > lo_ch and not texts[u]:
                    u -= 1
                if texts[u] and u not in src:
                    src.append(u)
            before = self._shown.get(lang, ())
            new = [u for u in src if u in vids or u not in before]
            if not new:
                continue
            self._shown[lang] = set(src)
            first = bk.where(min(new))[2]
            last = max(new[-1], vids[-1])
            # The verses after it that this Bible joined into it (not ones most Bibles leave out).
            while last + 1 <= bk.chapter_span(last)[1] and not texts[last + 1] \
                    and sum(1 for t in self.bible.index.toks[last + 1] if t) >= 2:
                last += 1
            out["label"][lang] = self.names.label(lang, code, c, first, bk.where(last)[2])
            joiner = "" if lang.startswith("zh") else " "
            out["text"][lang] = joiner.join(texts[u] for u in new)
        if not out["langs"]:
            return None
        return out


def display(hit, lang: str) -> str | None:
    """The verse as it goes on the screen: "text (ยอห์น 3:16)", or None if nothing new to show."""
    if not hit or lang not in hit.get("text", {}):
        return None
    label = hit["label"].get(lang, hit["ref"])
    if lang.startswith("zh"):
        return f"{hit['text'][lang]}（{label}）"
    return f"{hit['text'][lang]} ({label})"
