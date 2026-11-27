"""Bible names, books and church words, spelled the way the Thai and Chinese Bibles spell them.

The translator (NLLB) knows the famous names but guesses at the rest, and sometimes leaves a name
in English letters. This puts the Bible's spelling back. It only ever touches a word when the
English line says that name, so "Lot" in Thai is only fixed when the preacher said "Lot".

The word list (bible_words.json) is built from public-domain Bibles by tools/bible/. It holds the
names and the translator's known wrong spellings ("misses"), never Bible text.
"""
from __future__ import annotations

import json
import os
import re
from itertools import groupby

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "bible_words.json")
LANGS = ("th", "zh", "zh-TW")

# Thai "people of" and "holy" in front of a name (ชาวยิว, พระเยซู). When the translator drops it,
# put it back, unless another word already does the job (คนยิว, พวกยิว).
TH_LEAD = ("พระ", "ชาว")
TH_LEAD_ALREADY = ("พระ", "ชาว", "คน", "พวก", "ชน", "ศาสนา", "องค์")
# Chinese place and people names that carry a word for "river" or "people" (约旦河): the bare stem
# is just as right.
ZH_TAIL = ("河", "溪", "人")
# Church words NLLB gets in the Catholic or Buddhist form. (English words, wrong, right.)
TERM_FIX = {
    "th": [
        (r"\bpray(s|ed|ing)?\b|\bprayers?\b", "สวดมนต์|ภาวนา", "อธิษฐาน"),
        (r"\b(?:the|this) verse\b", "บทนี้", "ข้อนี้"),             # "this chapter" for "the verse"
        (r"\bpastors?\b", "บาทหลวง", "ศิษยาภิบาล"),
        (r"\bworship(s|ped|ping)?\b", "บูชา(?!ยัญ)", "นมัสการ"),
        # Nicodemus sounded out many ways, some starting with an offensive word (นิโกรธีโมส, นิโกรดมัส)
        (r"\bnicodemus\b", "นิโ?[กค]ร?[ก-์]{0,5}?(?:มัส|โมส|มุส)", "นิโคเดมัส"),
        # Thai cities sounded out ("Chiang Mai" came out as ชางไม)
        (r"\bchiang mai\b", "(?:ชาง|เชียง|เจียง|ชิง)\\s?(?:ใหม|ไหม|ไม)่?", "เชียงใหม่"),
        (r"\bchiang rai\b", "(?:ชาง|เชียง|เจียง|ชิง)\\s?(?:ราย|ไร|ไหร)", "เชียงราย"),
    ],
    "zh": [
        (r"\bpray(s|ed|ing)?\b|\bprayers?\b", "祈祷", "祷告"),
        # "into Chiang Mai" came out as 带到迈 and 带到长迈 (Windows and Mac test runs)
        # A bare 迈 only at a word's end, so 迈克 (Mike) and 迈出 (step out) stay.
        (r"\bchiang mai\b", "[长昌张江蒋强]迈|(?<!清)迈(?=[，。！？、；：,.!?;:\\s]|$)", "清迈"),
        (r"\bchiang rai\b", "[长昌张江蒋强]莱|(?<!清)莱(?=[，。！？、；：,.!?;:\\s]|$)", "清莱"),
    ],
    "zh-TW": [(r"\bpray(s|ed|ing)?\b|\bprayers?\b", "祈禱", "禱告")],
}
_NUM = {"1": "1", "2": "2", "3": "3", "first": "1", "second": "2", "third": "3",
        "1st": "1", "2nd": "2", "3rd": "3", "i": "1", "ii": "2", "iii": "3"}
# What follows a book's name in the translation when it's a reference ("ยอห์น 3", "约翰福音第3章").
_REF_AFTER = r"(?=\s*(?:[0-9๐-๙]|第|บท))"
# "verse 16" sometimes comes out as "chapter 16" (บทที่ 16, 第16章).
_VERSE = re.compile(r"\bverses?\s+(\d+)", re.I)
_CHAPTER = re.compile(r"\bchapters?\s+(\d+)", re.I)
# "the Gospel of John" came out as "the Word of John", "the good news of John", even "material John".
_GOSPEL = re.compile(r"\bgospel\s+(?:of|according\s+to)\s+(?:saint\s+|st\.?\s+)?(matthew|mark|luke|john)\b", re.I)
_TH_GOSPEL = {"matthew": "มัทธิว", "mark": "มาระโก", "luke": "ลูกา", "john": "ยอห์น"}
_TH_GOSPEL_ALSO = {"matthew": "แมทธิว|แมทธิว", "mark": "มาร์ค|มาร์ก", "luke": "ลุค|ลูค", "john": "จอห์น"}  # everyday spellings
_TH_GOSPEL_WRONG = "(?:(?:พระวจนะ|ข่าวประเสริฐ|กิตติคุณ|พระธรรม|วัสดุ)(?:ของ)?)?"
_TWICE = re.compile(r"\b(\d+)\s+\1\b")   # a number said twice ("chapter 3 3")
_VERSE_FIX = {"th": (r"บท(?:ที่|ความ)?\s*{n}(?![0-9])", "ข้อ {n}"),   # "chapter 16", "article 16"
              "zh": (r"第\s*{n}\s*章", "第{n}节"), "zh-TW": (r"第\s*{n}\s*章", "第{n}節")}
# Chinese: the translator sounds a rare name out a little differently each time (尼哥底马, 尼哥底摩
# for 尼哥德慕). Grammar characters that never start or end a Bible name, so a near spelling never
# swallows the word next to it.
_CJK = re.compile(r"[㐀-鿿豈-﫿]+")
_ZH_GRAMMAR = set("的了是在和与對对把被就也都很这這们們我你她它说說到给給向从從为為有要会會能吗嗎呢吧啊")


class BibleWords:
    def __init__(self, path: str = PATH):
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        self.names = doc.get("names", {})
        self.miss = doc.get("miss", {})
        self.books = {b["code"]: b for b in doc.get("books", [])}
        words = sorted(self.names, key=len, reverse=True)
        self._name_re = re.compile(r"\b(" + "|".join(map(re.escape, words)) + r")\b") if words else None
        # Books: "John 3:16", "John chapter 3", "First Corinthians 13", "the book of Ruth".
        self._book_of = {}
        for b in self.books.values():
            for say in b.get("say") or [b["en"]]:
                self._book_of[(b.get("num"), say.lower())] = b["code"]
        says = sorted({s for (_n, s) in self._book_of}, key=len, reverse=True)
        num = "|".join(sorted(map(re.escape, _NUM), key=len, reverse=True))
        name = "|".join(map(re.escape, says))
        self._book_re = re.compile(
            rf"(?:\b(?P<num>{num})\s+)?\b(?P<name>{name})\b(?=\s*(?:chapter\b|ch\.|\d))"
            rf"|\b(?:book|gospel|letter|epistle) of (?:the )?(?:(?P<num2>{num})\s+)?(?P<name2>{name})\b",
            re.I)
        self._spelled = {t: {e[t] for e in self.names.values() if e.get(t)} for t in LANGS}
        self._chars = {}

    # -- what the English line mentions --------------------------------------------------------
    def names_in(self, src: str) -> list[str]:
        if not self._name_re or not src:
            return []
        return list(dict.fromkeys(self._name_re.findall(src.replace("’", "'"))))

    def books_in(self, src: str, spans: list | None = None) -> list[tuple[str, str]]:
        """(book code, the words said)"""
        out = []
        for m in self._book_re.finditer(src or ""):
            n = (m.group("num") or m.group("num2") or "").lower()
            said = (m.group("name") or m.group("name2")).lower()
            code = self._book_of.get((_NUM.get(n), said))
            if code:
                out.append((code, m.group(0)))
                if spans is not None:
                    spans.append(m.span())
            # (no code: "Corinthians 13" without first or second, so which letter isn't clear)
        return out

    def _split(self, src):
        """Books, then the names said outside them ("Galatians 2:20" is the book, not the people)."""
        spans = []
        books = self.books_in(src, spans)
        rest = src
        for a, b in spans:
            rest = rest[:a] + " " * (b - a) + rest[b:]
        return books, self.names_in(rest)

    def hints(self, src: str, tgt: str) -> list[tuple[str, str]]:
        """(English, Bible spelling) for the names and books in a line, for a translator that reads hints."""
        books, names = self._split(src)
        out = [(w, self.names[w][tgt]) for w in names if self.names[w].get(tgt)]
        out += [(said, self.books[c][tgt]) for c, said in books if self.books[c].get(tgt)]
        return out

    # -- the fix ---------------------------------------------------------------------------------
    def fix(self, src: str, out: str, tgt: str) -> str:
        if tgt not in LANGS or not src or not out:
            return out
        books, found = self._split(src)
        wants = [self.names[w][tgt] for w in found if self.names[w].get(tgt)]
        for w in found:
            want = self.names[w].get(tgt)
            if want:
                out = self._fix_name(w, want, out, tgt, [x for x in wants if x != want])
        for code, said in books:
            want = self.books[code].get(tgt)
            if want:
                out = self._fix_book(code, said, want, out, tgt)
        for pat, wrong, right in TERM_FIX.get(tgt, ()):
            if re.search(pat, src, re.I):
                out = re.sub(wrong, right, out)
        chapters = set(_CHAPTER.findall(src)) | set(re.findall(r"\b(\d+):\d", src))
        for n in sorted(set(_VERSE.findall(src)) - chapters):
            wrong, right = _VERSE_FIX[tgt]
            out = re.sub(wrong.format(n=n), right.format(n=n), out)
        gospel = _GOSPEL.search(src) if tgt == "th" else None
        if gospel and "พระกิตติคุณ" not in out:
            name = gospel.group(1).lower()
            book, said = _TH_GOSPEL[name], f"(?:{_TH_GOSPEL[name]}|{_TH_GOSPEL_ALSO[name]})"
            if len(re.findall(said, out)) == 1:      # with two, which one is the Gospel isn't sure
                out = re.sub(_TH_GOSPEL_WRONG + said, "พระกิตติคุณ" + book, out)
        # A number said twice, run together in the translation ("chapter 3 3" became "chapter 33").
        for n in set(_TWICE.findall(src)):
            if not re.search(rf"(?<![0-9]){n}{n}(?![0-9])", src):
                out = re.sub(rf"(?<![0-9]){n}{n}(?![0-9])", n, out)
        return out

    def _ok(self, w, want, out, tgt):
        if want in out:
            return True
        if any(a in out for a in self.names[w].get("th_also", ())) and tgt == "th":
            return True
        return tgt != "th" and want.endswith(ZH_TAIL) and len(want) > 2 and want[:-1] in out

    def _fix_name(self, w, want, out, tgt, others):
        if self._ok(w, want, out, tgt):
            return self._also_wrong(w, want, out, tgt, others)
        # Left in English letters.
        latin = re.compile(r"(?<![A-Za-z])" + re.escape(w) + r"(?![A-Za-z])")
        if latin.search(out):
            return latin.sub(want, out)
        # พระเยซู written as bare เยซู.
        if tgt == "th":
            for lead in TH_LEAD:
                bare = want[len(lead):]
                if want.startswith(lead) and len(bare) > 1 and bare in out:
                    i = out.index(bare)
                    if not out[:i].endswith(TH_LEAD_ALREADY):
                        return out[:i] + want + out[i + len(bare):]
                    return out
        # A spelling the translator is known to use for this name.
        for m in sorted(self.miss.get(tgt, {}).get(w, ()), key=len, reverse=True):
            if m in out and not any(m in o and o in out for o in others):
                return out.replace(m, want)
        if tgt != "th":
            near = self._near(w, want, out, tgt, others)
            if near:
                return out.replace(near, want)
        return out

    def _also_wrong(self, w, want, out, tgt, others):
        """The right spelling and a wrong one in the same line (叫尼哥底马的老师，尼哥德慕是...)."""
        for m in self.miss.get(tgt, {}).get(w, ()):
            if m in out and m not in want and want not in m and not any(m in o and o in out for o in others):
                out = out.replace(m, want)
        if tgt != "th" and want in out:          # not when only a short form (约旦 for 约旦河) is there
            near = self._near(w, want, out, tgt, others)
            if near and near not in want and want not in near:
                out = out.replace(near, want)
        return out

    def _name_chars(self, tgt):
        """Characters the Bible or the translator use to sound out names (not 回 or 说)."""
        if tgt not in self._chars:
            got = set("".join(self._spelled.get(tgt, ())))
            for ms in self.miss.get(tgt, {}).values():
                got.update("".join(ms))
            self._chars[tgt] = got - _ZH_GRAMMAR
        return self._chars[tgt]

    def _near(self, w, want, out, tgt, others):
        """A whole sounded-out name in a Chinese line that is a near spelling of this one: the same
        first character, at least half of it shared with the Bible's spelling or a known miss, and
        about as long. Only a whole run of name characters is swapped, so the word next to the
        name (回家, 说) is never eaten; when it isn't clear where the name ends, it stays as it is."""
        if len(want) < 3:
            return None                          # two characters: one off is a different name
        forms = [want] + list(self.miss.get(tgt, {}).get(w, ()))
        chars = self._name_chars(tgt)
        best, best_key = None, None
        for run in _CJK.findall(out):
            for is_name, group in groupby(run, key=chars.__contains__):
                piece = "".join(group)
                if (not is_name or abs(len(piece) - len(want)) > 1 or piece in self._spelled[tgt]
                        or piece.rstrip("".join(ZH_TAIL)) in self._spelled[tgt]
                        or any(piece in o or o in piece for o in others)):
                    continue
                for f in forms:
                    same = sum(min(piece.count(c), f.count(c)) for c in set(piece))
                    key = (same, -abs(len(piece) - len(want)))
                    if piece[0] == f[0] and same >= 2 and same * 2 >= len(f) and (best_key is None or key > best_key):
                        best, best_key = piece, key
        return best

    def _fix_book(self, code, said, want, out, tgt):
        if want in out:
            return out
        if said in out:
            return out.replace(said, want)
        for m in sorted(self.miss.get(tgt, {}).get(code, ()), key=len, reverse=True):
            if m not in out:
                continue
            if m in self._spelled.get(tgt, ()):
                # Another name's spelling (约翰 for the Gospel of John): only right before a chapter.
                new = re.sub(re.escape(m) + _REF_AFTER, want, out, count=1)
                if new != out:
                    return new
                continue
            return out.replace(m, want, 1)
        return out


_WORDS: BibleWords | None = None
_TRIED = False


def bible_words() -> BibleWords | None:
    """The shared word list, loaded once. None if the file is missing (the app still translates)."""
    global _WORDS, _TRIED
    if not _TRIED:
        _TRIED = True
        try:
            _WORDS = BibleWords()
        except (OSError, ValueError):
            _WORDS = None
    return _WORDS


def bible_fix(src: str, out: str, tgt: str) -> str:
    bw = bible_words()
    return bw.fix(src, out, tgt) if bw else out
