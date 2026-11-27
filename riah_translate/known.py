"""Recognises a spoken line as text known ahead of time: Bible verses, or sentences from sermon notes.

Speech never matches the page exactly: the preacher reads a different Bible version than ours
("whoever believes" for "everyone who believes"), the speech-to-text writes "40" for "forty", and he
drops in his own words ("listen, church"). So both sides are boiled down to plain word stems first
("loveth", "loves" and "loved" all become "lov"; "thee" becomes "you"; numbers become digits) and then
lined up word by word.

Finding the right text among ~93,000 (31,000 verses in three English versions) is two steps:
  1. a word index: every less-common word points to the passages that use it, so a line only looks
     at the handful of passages that share its rarer words;
  2. word alignment against each of those, which says how much of the line the passage covers, in
     order, and where in the passage the line starts and ends.

Everything here is plain Python (difflib for the alignment) and works on one line in a millisecond
or two. verses.py decides what counts as a verse; KnownLines below is a simple matcher for notes.
"""
from __future__ import annotations

import re
from array import array
from collections import Counter
from difflib import SequenceMatcher

# -- turning text into comparable word stems --------------------------------------------------------

# Words (with an apostrophe part: "don't", "god's") and the marks where a sentence or quote starts.
_TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z]+)?|[.;:?!\"“”«»]")
_DIGIT_COMMA = re.compile(r"(?<=\d),(?=\d{3}\b)")       # "12,000"

UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
         "eight": 8, "nine": 9}
TEENS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
         "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
        "eighty": 80, "ninety": 90, "fourty": 40}
SCALES = {"hundred": 100, "thousand": 1000}


def number_kind(w):
    if w in UNITS:
        return "unit", UNITS[w]
    if w in TEENS:
        return "teen", TEENS[w]
    if w in TENS:
        return "tens", TENS[w]
    if w in SCALES:
        return "scale", SCALES[w]
    return None


def number_groups(run):
    """Number words said in a row, as the numbers they mean: [(number, first word, last word)].
    "twenty eight" is 28, but "three sixteen" is 3 and 16 (that's how a chapter and verse are
    said), and "one hundred nineteen" is 119."""
    out, total, cur, have, last, start = [], 0, 0, False, None, 0
    for i, (kind, val) in enumerate(run):
        if kind == "scale":
            if not have:
                cur, start = 1, i
            if val == 100:
                cur = (cur or 1) * 100
            else:
                total, cur = total + (cur or 1) * val, 0
            have, last = True, "scale"
            continue
        if have and (last in ("unit", "teen") or (last == "tens" and kind != "unit")):
            out.append((total + cur, start, i - 1))
            total, cur, have = 0, 0, False
        if not have:
            start = i
        cur += val
        have, last = True, kind
    if have:
        out.append((total + cur, start, len(run) - 1))
    return out


def numbers_from_words(run):
    return [n for n, _a, _b in number_groups(run)]


# Older Bible English, and spellings, said the modern way.
ARCHAIC = {
    "thee": "you", "thou": "you", "ye": "you", "thy": "your", "thine": "your", "yourselves": "yourself",
    "hath": "has", "hast": "have", "hadst": "had", "doth": "does", "dost": "do", "art": "are",
    "wilt": "will", "shalt": "shall", "canst": "can", "didst": "did", "wast": "were", "saith": "says",
    "spake": "spoke", "unto": "to", "shew": "show", "shewed": "showed", "amongst": "among",
    "whosoever": "whoever", "whatsoever": "whatever", "verily": "truly", "nay": "no", "ere": "before",
    "o": "oh", "yahweh": "lord", "jehovah": "lord", "saviour": "savior", "honour": "honor",
    "neighbour": "neighbor", "labour": "labor", "favour": "favor", "colour": "color",
    "savour": "savor", "judgement": "judgment", "okay": "ok", "ok": "ok",
}
_CONTRACT = {"won't": ("will", "not"), "can't": ("can", "not"), "shan't": ("shall", "not"),
             "ain't": ("is", "not"), "cannot": ("can", "not"), "let's": ("let", "us"),
             "'tis": ("it", "is")}
_IS = {"it", "that", "he", "she", "there", "what", "here", "who", "where", "this"}

STOP = set("""a an the and or but nor of to in on at by for from with as into upon onto out up off
over about than then so if because that this these those it its is are was were be been being am
do does did done have has had having will would shall should can could may might must not no
there their theirs they them he him his himself she her hers herself we us our ours you your
yours yourself i me my mine myself who whom whose which what when where why how all also even
now oh just very let while though yet such any some""".split())

# Words a preacher says to the room that are not in the verse. One of these on its own is enough to
# say "this line is the preacher talking, not the Bible".
# (Only when the word isn't in the verse itself: "listen" is in plenty of verses.)
INTERJECT = set("""listen church friend friends folks guys amen ok um uh uhm hmm yeah yes alright
praise hallelujah notice look see hear imagine wow really beloved everybody somebody y'all repeat
neighbor turn please today tonight morning""".split())


def _plain_stem(w):
    """A rough word stem: enough that "believeth", "believes", "believed" and "believe" agree."""
    for _ in range(2):
        if len(w) <= 3 or w.isdigit():
            break
        n = w
        if w.endswith("ies") and len(w) > 4:
            n = w[:-3] + "y"
        elif w.endswith("ied") and len(w) > 4:
            n = w[:-3] + "y"
        elif w.endswith("ing") and len(w) >= 6:
            n = w[:-3]
        elif w.endswith(("eth", "est")) and len(w) >= 6:
            n = w[:-3]
        elif w.endswith("ed") and len(w) >= 5:
            n = w[:-2]
        elif w.endswith("es") and len(w) >= 5 and not w.endswith(("ses", "ues")):
            n = w[:-2]
        elif w.endswith("s") and not w.endswith(("ss", "us", "is")):
            n = w[:-1]
        if n != w and n[-1:] == n[-2:-1] and n[-1:] not in ("l", "s", "z", "e", "o") and len(n) > 3:
            n = n[:-1]                       # sinned -> sinn -> sin
        if n == w:
            break
        w = n
    if len(w) > 3 and w.endswith("e") and not w.endswith("ee"):
        w = w[:-1]
    return w


_WORD_CACHE: dict = {}


def _word(w):
    """One spoken or written word as its stems (a contraction can be two)."""
    r = _WORD_CACHE.get(w)
    if r is not None:
        return r
    if w in _CONTRACT:
        parts = _CONTRACT[w]
    elif w.endswith("n't"):
        parts = (w[:-3], "not")
    elif "'" in w:
        base, tail = w.split("'", 1)
        if tail == "s":
            parts = (base, "is") if base in _IS else (base,)
        elif tail in ("ll", "re", "ve", "m", "d"):
            parts = (base, {"ll": "will", "re": "are", "ve": "have", "m": "am", "d": "would"}[tail])
        else:
            parts = (base,)
    else:
        parts = (w,)
    r = tuple(_plain_stem(ARCHAIC.get(p, p)) for p in parts if p)
    if len(_WORD_CACHE) < 200_000:
        _WORD_CACHE[w] = r
    return r


STOP_STEMS = frozenset(s for w in STOP for s in _word(w))
INTERJECT_STEMS = frozenset(s for w in INTERJECT for s in _word(w))


def clean_text(text: str) -> str:
    text = (text or "").lower().replace("’", "'").replace("‘", "'").replace("`", "'")
    return _DIGIT_COMMA.sub("", text)


_NUMWORD = {w: (kind, v) for kind, table in (("unit", UNITS), ("teen", TEENS), ("tens", TENS),
                                              ("scale", SCALES)) for w, v in table.items()}
_MARKS = frozenset('.;:?!"“”«»')


def tokens(text: str):
    """(stems, starts): the word stems of a text, and the positions where a sentence, clause or
    quote begins (always including 0)."""
    stems, starts, run = [], [0], []
    numword, cache = _NUMWORD.get, _WORD_CACHE.get
    for t in _TOKEN.findall(clean_text(text)):
        nk = numword(t)
        if nk is not None:
            run.append(nk)
            continue
        if run:
            if t == "and" and run[-1][0] == "scale":       # "one hundred and nineteen"
                continue
            stems.extend(str(n) for n in numbers_from_words(run))
            run = []
        w = cache(t)
        if w is None:
            if t in _MARKS:
                if stems and starts[-1] != len(stems):
                    starts.append(len(stems))
                continue
            w = _word(t)
        stems.extend(w)
    if run:
        stems.extend(str(n) for n in numbers_from_words(run))
    if starts[-1] >= len(stems) and len(starts) > 1:
        starts.pop()
    return tuple(stems), tuple(starts)


def weight(stem: str) -> float:
    return 0.4 if stem in STOP_STEMS else 1.0


def is_content(stem: str) -> bool:
    return stem not in STOP_STEMS


# -- lining a line up against a known text ----------------------------------------------------------

class Fit:
    """How a spoken line lines up against one known text.

    cover   share of the line (common words count less) found in the text: in order counts fully,
            a word that is in the text but out of order counts 0.8
    first   position in the text where the line starts (first word matched in order), last: where it ends
    order   share of the line (common words count less) found in the text in order
    hit     how many text words were matched in order
    edge    a word at the start or end of the line is in the text but out of place ("lean on your
            own understanding, lean on him": the second "lean" is his own)
    bad     why this can't be the text the preacher is reading (or "" if nothing is wrong): words of
            his own at the start or end ("lead", "tail"), two or more of his own words together
            ("gap"), or words like "listen" or "amen" that aren't in the text ("aside")
    """
    __slots__ = ("cover", "order", "first", "last", "hit", "edge", "bad", "n", "content", "absent",
                 "blocks")

    def __repr__(self):
        return (f"Fit(cover={self.cover:.2f}, order={self.order:.2f}, first={self.first}, "
                f"last={self.last}, hit={self.hit}, edge={self.edge}, bad={self.bad!r}, n={self.n})")


MAX_SKIP = 8      # a reader doesn't jump over more than this many words of the text mid-line


def align(line, text, present=None, sm=None) -> Fit:
    """Line up `line` (stems) against `text` (stems). `present`: every stem that may count as "in the
    text" even out of order (by default the text's own). Pass a SequenceMatcher already holding the
    line as its second sequence (`sm.set_seq2(line)`) to save work when trying many texts."""
    if sm is None:
        sm = SequenceMatcher(None, autojunk=False)
        sm.set_seq2(line)
    sm.set_seq1(text)
    blocks = [b for b in sm.get_matching_blocks() if b.size]
    # Runs far apart in the text are words picked from here and there (someone retelling the
    # passage), not one reading: keep the weightiest stretch.
    if len(blocks) > 1:
        groups, cur = [], [blocks[0]]
        for b in blocks[1:]:
            p = cur[-1]
            if b.a - (p.a + p.size) > MAX_SKIP:
                groups.append(cur)
                cur = []
            cur.append(b)
        groups.append(cur)
        if len(groups) > 1:
            blocks = max(groups, key=lambda g: sum(weight(text[b.a + k]) for b in g for k in range(b.size)))
    # A lone common word matched far from the rest ("the" three verses on) is chance, not reading.
    while len(blocks) > 1 and blocks[0].size == 1 and text[blocks[0].a] in STOP_STEMS \
            and blocks[1].a - blocks[0].a > 3:
        blocks.pop(0)
    while len(blocks) > 1 and blocks[-1].size == 1 and text[blocks[-1].a] in STOP_STEMS \
            and blocks[-1].a - (blocks[-2].a + blocks[-2].size) > 3:
        blocks.pop()
    n = len(line)
    status = [0] * n                    # 2 in order, 1 in the text out of order, 0 not in the text
    for b in blocks:
        for k in range(b.size):
            status[b.b + k] = 2
    pres = present if present is not None else set(text)
    for i, s in enumerate(line):
        if not status[i] and s in pres:
            status[i] = 1
    f = Fit()
    f.n = n
    f.blocks = blocks                   # (text position, line position, length) runs matched in order
    total = got = inorder = 0.0
    content = absent = 0
    for s, st in zip(line, status):
        w = weight(s)
        total += w
        got += w if st == 2 else 0.8 * w if st == 1 else 0.0
        inorder += w if st == 2 else 0.0
        if s not in STOP_STEMS:
            content += 1
            absent += st == 0
    f.cover = got / total if total else 0.0
    f.order = inorder / total if total else 0.0
    f.content, f.absent = content, absent
    f.hit = sum(b.size for b in blocks)
    if blocks:
        f.first, f.last = blocks[0].a, blocks[-1].a + blocks[-1].size - 1
        lo, hi = blocks[0].b, blocks[-1].b + blocks[-1].size - 1
    else:
        f.first = f.last = -1
        lo, hi = n, -1
    f.bad = ""
    f.edge = any(st == 1 and line[i] not in STOP_STEMS for i, st in enumerate(status)
                 if i < lo or i > hi)
    if not blocks:
        f.bad = "none"
        return f
    # His own words around the reading: "Paul writes ...", "... isn't that good".
    if any(st == 0 and line[i] not in STOP_STEMS for i, st in enumerate(status[:lo])):
        f.bad = "lead"
    elif any(st == 0 and line[i] not in STOP_STEMS for i, st in enumerate(status[hi + 1:], hi + 1)):
        f.bad = "tail"
    elif any(st == 0 and s in INTERJECT_STEMS for s, st in zip(line, status)):
        f.bad = "aside"
    else:
        run = 0
        for s, st in zip(line, status):
            if st:
                run = 0
            elif s not in STOP_STEMS:
                run += 1
                if run > 1:
                    f.bad = "gap"
                    break
    return f


# -- the word index ---------------------------------------------------------------------------------

class Index:
    """Known texts, each in one or more versions (a verse in three Bible versions; a notes sentence in
    one). `docs[i]` is a list of strings; an empty string is a missing version."""

    def __init__(self, docs, common: int = 2500):
        self.toks = []           # toks[i][version] = stems
        self.starts = []         # starts[i][version] = where sentences/quotes begin
        post: dict = {}
        zero = (0,)
        for i, versions in enumerate(docs):
            ts, ss, allw = [], [], set()
            for text in versions:
                t, s = tokens(text) if text else ((), zero)
                ts.append(t)
                ss.append(zero if s == zero else s)
                allw.update(t)
            self.toks.append(tuple(ts))
            self.starts.append(tuple(ss))
            for w in allw:
                if w not in STOP_STEMS:
                    p = post.get(w)
                    if p is None:
                        post[w] = [i]
                    else:
                        p.append(i)
        self.df = {w: len(p) for w, p in post.items()}
        # Words in more than `common` texts ("lord", "said") don't help find the text.
        self.post = {w: array("I", p) for w, p in post.items() if len(p) <= common}

    def __len__(self):
        return len(self.toks)

    def candidates(self, stems, limit: int = 8, need: int = 2):
        """Texts that share the most less-common words with the line: [(doc, shared words)]."""
        keys = sorted((w for w in set(stems) if w in self.post), key=self.df.get)[:10]
        if len(keys) < need:
            return []
        cnt = Counter()
        for w in keys:
            cnt.update(self.post[w])
        floor = max(need, (len(keys) + 1) // 2)
        return [(d, c) for d, c in cnt.most_common(limit) if c >= floor]


# -- a simple matcher for sermon notes --------------------------------------------------------------

class KnownLines:
    """Sentences known ahead of time (from sermon notes). find(line) gives the sentence the line reads
    out, or None. Strict: the line must be at least 6 words, nearly all of them from one sentence,
    in order, with nothing of his own around it."""

    def __init__(self, sentences):
        self.sentences = list(sentences)
        self.index = Index([[s] for s in self.sentences], common=max(50, len(self.sentences) // 4))

    def find(self, line: str, min_words: int = 6, min_cover: float = 0.85):
        stems, _ = tokens(line)
        if len(stems) < min_words:
            return None
        sm = SequenceMatcher(None, autojunk=False)
        sm.set_seq2(stems)
        best = None
        for d, _c in self.index.candidates(stems, limit=6):
            f = align(stems, self.index.toks[d][0], sm=sm)
            if not f.bad and f.cover >= min_cover and (best is None or f.cover > best[1]):
                best = (d, f.cover)
        return best
