"""Reads the sermon notes a church gives the app, so the subtitles can follow along.

The notes never leave the computer and nobody reads them: this file only pulls out
  - the sentences, so a line the preacher reads straight from his notes can be recognised,
  - a short list of names (people, places, Bible books) to help the speech-to-text spell them,
  - the Bible references, so those verses are expected (verses.Verses.expect_refs).
Nothing here writes the notes to the log or the screen; error messages never quote them.

Word (.docx), PowerPoint (.pptx) and OpenDocument (.odt, .odp) files are zip files of XML, read
with Python's own zipfile and XML reader. PDF needs pypdf (BSD-3). Keynote and Pages can't be read:
the message says how to export them.
"""
from __future__ import annotations

import io
import logging
import os
import re
import warnings
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter

MAX_BYTES = 25 * 1024 * 1024         # the file as added
MAX_UNPACKED = 60 * 1024 * 1024      # what a Word or PowerPoint file may unpack to, in all
MAX_PARTS = 3000                     # slides, notes pages ...
MAX_PDF_PAGES = 400
MAX_SENTENCES = 500
MAX_REFS = 60
HINTS_MAX = 240                      # what the speech-to-text reads (asr.StreamingTranscriber)
MAX_LINE_CHARS = 2000
MAX_TOTAL_CHARS = 200_000            # about 100 pages; a whole sermon manuscript is a tenth of that
_MAX_ABBR_TAIL = 20                  # longest abbreviation the regexes can match at a sentence end

FORMATS = "Word (.docx), PowerPoint (.pptx), PDF, OpenDocument (.odt, .odp) or plain text (.txt)"


class NotesError(ValueError):
    """The notes can't be read. str(error) is a plain message for the person who added the file."""


def read_notes(filename: str, data: bytes) -> dict:
    """{"sentences": [...], "hints": "Somchai, Chiang Mai, Nicodemus, ...", "refs": ["JHN 3:16-18", ...]}.
    Raises NotesError with a message that can be shown as it is."""
    data = bytes(data or b"")
    if not data:
        raise NotesError("That file is empty.")
    if len(data) > MAX_BYTES:
        raise NotesError("That file is too big for sermon notes (over 25 MB). Try saving just the "
                         "notes as a Word file or PDF.")
    text = _read(os.path.splitext(filename or "")[1].lower(), data)
    return notes_from_text(text)


# -- getting the text out --------------------------------------------------------------------------

_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"      # old .doc / .ppt files


def _read(ext, data):
    if ext in (".key", ".pages", ".numbers"):
        raise NotesError("Keynote and Pages files can't be read here. In Keynote or Pages choose "
                         "File > Export To > PDF (or PowerPoint / Word), then add that file instead.")
    if data[:5] == b"%PDF-":
        return _pdf(data)
    if data[:8] == _OLE:
        raise NotesError("This is an older Word or PowerPoint file. Open it, choose Save As, and save "
                         "it as .docx or .pptx (or as a PDF), then add that file.")
    if data[:4] == b"PK\x03\x04":
        return _zipped(data)
    if ext in (".docx", ".pptx", ".odt", ".odp", ".pdf", ".doc", ".ppt"):
        raise NotesError(f"This file doesn't look like a real {ext[1:].upper()} file. Try opening it "
                         "and saving it again.")
    if ext in ("", ".txt", ".text", ".md", ".markdown"):
        text = _decode(data)
        return _from_markdown(text) if ext in (".md", ".markdown") or _looks_markdown(text) else text
    raise NotesError(f"Sermon notes can be {FORMATS}.")


def _decode(data):
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    if b"\x00" in data[:4000]:
        raise NotesError(f"This file doesn't look like text. Sermon notes can be {FORMATS}.")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _zip_part(z, name, budget):
    info = z.getinfo(name)
    if info.file_size > budget[0]:
        raise NotesError("This file is too big to read as sermon notes.")
    budget[0] -= info.file_size
    raw = z.read(info)
    # Office files never declare their own XML entities; a file that does is not a normal one.
    if b"<!ENTITY" in raw or b"<!DOCTYPE" in raw:
        raise NotesError("This file couldn't be read. Try opening it and saving it again.")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in raw[:512]:
        raise NotesError("This file couldn't be read. Try opening it and saving it again.")
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        raise NotesError("This file couldn't be read. Try opening it and saving it again.") from None


def _num(name):
    m = re.search(r"(\d+)\.xml$", name)
    return int(m.group(1)) if m else 0


def _zipped(data):
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        names = z.namelist()
    except (zipfile.BadZipFile, ValueError):
        raise NotesError("This file couldn't be opened. Try opening it and saving it again.") from None
    if len(names) > 20000:
        raise NotesError("This file couldn't be read as sermon notes.")
    have = set(names)
    budget = [MAX_UNPACKED]
    try:
        if "word/document.xml" in have:
            return _docx(z, budget)
        slides = [n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
        if slides:
            notes = [n for n in names if re.fullmatch(r"ppt/notesSlides/notesSlide\d+\.xml", n)]
            return _pptx(z, sorted(slides, key=_num), sorted(notes, key=_num), budget)
        if "content.xml" in have:
            return _odf(z, budget)
    except (zipfile.BadZipFile, KeyError, RuntimeError, OSError, EOFError):
        raise NotesError("This file couldn't be opened. Try opening it and saving it again.") from None
    if any(n.startswith("Index/") or n.endswith(".iwa") for n in names):
        raise NotesError("Keynote and Pages files can't be read here. In Keynote or Pages choose "
                         "File > Export To > PDF (or PowerPoint / Word), then add that file instead.")
    raise NotesError(f"Sermon notes can be {FORMATS}.")


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
TX = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"


def _docx(z, budget):
    root = _zip_part(z, "word/document.xml", budget)
    out = []
    for p in root.iter(W + "p"):
        parts = []
        for el in p.iter():
            if el.tag == W + "t" and el.text:
                parts.append(el.text)
            elif el.tag in (W + "tab",):
                parts.append(" ")
            elif el.tag in (W + "br", W + "cr"):
                parts.append("\n")
        out.append("".join(parts))
    return "\n".join(out)


def _drawing_paragraphs(root):
    out = []
    for p in root.iter(A + "p"):
        parts = []
        for el in p:
            if el.tag == A + "r":
                parts.extend(t.text or "" for t in el.iter(A + "t"))
            elif el.tag == A + "br":
                parts.append("\n")
            # a:fld is a slide number or date, not the notes
        out.append("".join(parts))
    return out


def _pptx(z, slides, notes, budget):
    if len(slides) + len(notes) > MAX_PARTS:
        raise NotesError("This presentation has too many slides to read as sermon notes.")
    out = []
    for name in slides + notes:          # speaker notes after the slides: both are the preacher's
        out.extend(_drawing_paragraphs(_zip_part(z, name, budget)))
        out.append("")
    return "\n".join(out)


def _odf_text(el):
    parts = [el.text or ""]
    for ch in el:
        tag = ch.tag
        if tag == TX + "s":
            try:
                c = min(max(int(ch.get(TX + "c", "1") or 1), 1), 64)   # a run of spaces, kept sane
            except ValueError:
                c = 1
            parts.append(" " * c)
        elif tag == TX + "tab":
            parts.append(" ")
        elif tag == TX + "line-break":
            parts.append("\n")
        elif tag in (TX + "note", TX + "note-citation"):
            pass                         # footnotes come out of order; leave them
        else:
            parts.append(_odf_text(ch))
        parts.append(ch.tail or "")
    return "".join(parts)


def _odf(z, budget):
    root = _zip_part(z, "content.xml", budget)
    out = []

    def walk(el):
        if el.tag in (TX + "p", TX + "h"):
            out.append(_odf_text(el))
            return
        for ch in el:
            walk(ch)

    walk(root)
    return "\n".join(out)


def _pdf(data):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise NotesError("PDF notes can't be read on this computer. Save the notes as a Word or "
                         "PowerPoint file instead.") from None
    # pypdf talks about damaged files in the log; keep it quiet so nothing from the file is logged.
    logging.getLogger("pypdf").setLevel(logging.CRITICAL + 1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted and not reader.decrypt(""):
                raise NotesError("This PDF is locked with a password. Save an unlocked copy and add "
                                 "that instead.")
            pages = reader.pages
            if len(pages) > MAX_PDF_PAGES:
                raise NotesError("This PDF has too many pages to read as sermon notes.")
            out = []
            for page in pages:
                out.append(page.extract_text() or "")
        except NotesError:
            raise
        except Exception:
            raise NotesError("This PDF couldn't be read. Try saving it again, or save the notes as "
                             "a Word file instead.") from None
    text = "\n".join(out)
    if not text.strip():
        raise NotesError("This PDF has no text in it that can be read (it may be pictures of pages). "
                         "Save the notes as a Word file instead.")
    return text


_MD_HINT = re.compile(r"^(?:#{1,6}\s|\s*[-*+]\s|\s*\d+\.\s|>\s)", re.M)


def _looks_markdown(text):
    return len(_MD_HINT.findall(text[:5000])) >= 3


def _from_markdown(text):
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)                 # pictures
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)               # links: keep the words
    text = re.sub(r"<[^>\n]+>", " ", text)                             # HTML tags
    text = re.sub(r"^\s{0,3}(?:#{1,6}\s*|>\s?|[-*+]\s+|\d+[.)]\s+)", "", text, flags=re.M)
    text = re.sub(r"(\*\*|__|\*|_|`|~~)(?=\S)(.+?)(?<=\S)\1", r"\2", text)
    return text


# -- what the notes give the subtitles ---------------------------------------------------------------

_BULLET = re.compile(r"^\s*(?:[-*+•·◦▪‣–—]|\d{1,3}[.)]|[a-zA-Z][.)](?=\s))\s*")
_SENT_END = re.compile(r"(?:(?<=[.!?])|(?<=[.!?][\"”’)\]]))\s+(?=[\"“‘(\[]?[A-Z0-9])")
_ABBREV = re.compile(r"\b(?:Mr|Mrs|Ms|Dr|St|Rev|Jr|Sr|vs|v|vv|ch|cf|etc|e\.g|i\.e|No)\.$", re.I)
_BOOK_ABBR_END = re.compile(r"\b[A-Z][a-z]{1,5}\.$")      # "Rom." or "1 Cor." before "8:28"


def _sentences_in(line):
    parts = _SENT_END.split(line)
    out, cur = [], ""
    for k, p in enumerate(parts):
        cur = f"{cur} {p}" if cur else p
        nxt = parts[k + 1] if k + 1 < len(parts) else ""
        tail = cur[-_MAX_ABBR_TAIL:]
        if nxt and (_ABBREV.search(tail) or (_BOOK_ABBR_END.search(tail) and nxt[:1].isdigit())):
            continue                     # "Dr. Lee", "Rom. 8:28": not the end of a sentence
        out.append(cur)
        cur = ""
    return out


def _chunks(s, most=40):
    words = s.split()
    if len(words) <= most:
        return [s]
    out = []
    for piece in re.split(r"(?<=[;:])\s+", s):
        w = piece.split()
        while len(w) > most:
            out.append(" ".join(w[:most]))
            w = w[most:]
        if w:
            out.append(" ".join(w))
    return out


def _lines(text):
    for raw in (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n").split("\n"):
        line = re.sub(r"\s+", " ", raw.replace(" ", " ").replace("​", "")).strip()
        line = _BULLET.sub("", line).strip()
        if line:
            yield line[:MAX_LINE_CHARS]


def notes_from_text(text: str) -> dict:
    """The sentences, hints and references in notes already turned into text."""
    from .verses import find_refs

    text = (text or "")[:MAX_TOTAL_CHARS]
    lines = list(_lines(text))
    sentences, seen = [], set()
    refs, ref_seen, last = [], set(), None
    plain = []                           # the lines without their references, for finding names
    for line in lines:
        bare = line
        for ref, (a, b) in find_refs(line, last):
            last = ref
            bare = bare[:a] + " " * (b - a) + bare[b:]
            code = str(ref)
            if code not in ref_seen and len(refs) < MAX_REFS:
                ref_seen.add(code)
                refs.append(code)
        plain.append(bare)
        if len(sentences) >= MAX_SENTENCES:
            continue
        for s in _sentences_in(line):
            for c in _chunks(s.strip()):
                key = re.sub(r"[^\w]+", " ", c.lower()).strip()
                if len(key.split()) < 3 or key in seen:
                    continue
                seen.add(key)
                sentences.append(c)
                if len(sentences) >= MAX_SENTENCES:
                    break
            if len(sentences) >= MAX_SENTENCES:
                break
    return {"sentences": sentences, "hints": _hints(plain, refs), "refs": refs}


# Capitalised words that aren't names, when they show up mid-sentence.
_NOT_NAMES = set("""I I'm I'll I've I'd God Lord Jesus Christ Holy Spirit Father Son Bible Scripture
Scriptures Word Gospel Church Amen Hallelujah Sunday Monday Tuesday Wednesday Thursday Friday Saturday
January February March April May June July August September October November December Christmas Easter
Lent Advent Pentecost Heaven Hell Kingdom King Savior Saviour Messiah Lamb Almighty He Him His
Old New Testament OT NT NIV ESV KJV NKJV NLT NASB CSB BSB WEB AM PM OK Mr Mrs Ms Dr St Pastor
Rev Reverend Brother Sister Elder Deacon Bishop Prophet Apostle Saint The A An And But Or So If""".split())
_TITLES = {"pastor", "rev", "reverend", "brother", "sister", "elder", "deacon", "bishop", "mr",
           "mrs", "ms", "dr", "st", "saint", "prophet", "apostle", "king", "queen", "uncle", "aunt"}
_CAPWORD = re.compile(r"[A-Z][a-z]+(?:['’][a-z]+)?|[A-Z]{2,}")
_WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")


def _hints(lines, refs):
    """Up to HINTS_MAX characters: names Whisper can't guess first (people and places from the
    notes that aren't in the Bible list), then the rarer Bible names, then the books."""
    from .bible_words import bible_words
    from .verses import _names, parse_ref

    bw = bible_words()
    bible_names = bw.names if bw else {}
    lower = Counter()
    for line in lines:
        for w in _WORD.findall(line):
            if w.islower():
                lower[w] += 1
    local, holy = Counter(), Counter()
    for line in lines:
        words = _WORD.findall(line)
        if not words:
            continue
        caps = sum(1 for w in words if w[0].isupper())
        if len(words) >= 2 and caps / len(words) > 0.6:
            continue                     # A Title In Capitals: not telling
        if bw:
            for name in bw.names_in(line):
                holy[name] += 1
        # Runs of capitalised words not at the start of a sentence: "Chiang Mai", "Somchai".
        for sent in _sentences_in(line):
            toks = list(_WORD.finditer(sent))
            run = []
            for k, m in enumerate(toks + [None]):
                w = m.group(0) if m else None
                good = (w is not None and k > 0 and _CAPWORD.fullmatch(w) and w not in _NOT_NAMES
                        and w.lower() not in lower and w not in bible_names)
                if good and w.lower() in _TITLES:
                    good = False
                if good:
                    run.append(w)
                    continue
                if run:
                    local[" ".join(run)] += 1
                    run = []
    picked = [n for n, _c in local.most_common()]
    # Rare Bible names help most ("Nicodemus"); everyone gets "Paul" and "David" right.
    rare = sorted(holy, key=lambda n: (bible_names.get(n, {}).get("verses", 0) > 150,
                                      -holy[n], bible_names.get(n, {}).get("verses", 0)))
    picked += [n for n in rare if n not in picked]
    nm = _names()
    for r in refs:
        ref = parse_ref(r)
        if ref:
            en = nm.show.get(ref.book, {}).get("en")
            if en and en not in picked:
                picked.append(en)
    out = ""
    for p in picked:
        nxt = f"{out}, {p}" if out else p
        if len(nxt) > HINTS_MAX:
            continue
        out = nxt
    return out
