"""Checks with no models for the Bible verses and sermon notes (known.py, verses.py, notes.py).
Safe to run anywhere:

    python tests/test_known.py

The first check reads the shipped Bibles (a couple of seconds); everything after that is quick.
"""
import io
import statistics
import sys
import threading
import time
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from riah_translate.known import KnownLines, tokens  # noqa: E402
from riah_translate.notes import NotesError, read_notes  # noqa: E402
from riah_translate.verses import Ref, Verses, display, find_refs, parse_ref  # noqa: E402

HERE = Path(__file__).resolve().parent
V = None


def verses():
    """A fresh follower for one service (the Bibles load once)."""
    global V
    if V is None:
        t = time.perf_counter()
        V = Verses.load(langs=("th", "zh"))
        assert V is not None, "the Bible files in riah_translate/bible are missing"
        print(f"   (Bibles loaded in {time.perf_counter() - t:.1f}s)")
    return Verses(V.bible)


def vid(code, c, v):
    return V.bible.books.vid(code, c, v)


def thai(code, c, v):
    return V.bible.text("th")[vid(code, c, v)]


# -- words and numbers ----------------------------------------------------------------------------

def test_tokens():
    assert tokens("John three sixteen")[0] == ("john", "3", "16")
    assert tokens("Psalm one hundred and nineteen")[0][-1] == "119"
    assert tokens("Romans eight twenty-eight")[0][-2:] == ("8", "28")
    assert tokens("12,000 men")[0][0] == "12000"
    # Older Bible English lines up with modern English.
    assert tokens("whosoever believeth in thee")[0] == tokens("whoever believes in you")[0]
    assert tokens("He doesn't")[0] == tokens("he does not")[0]


def test_bible_data():
    v = verses()
    b = v.bible
    assert b.versions == ["bsb", "web", "kjv"]
    assert len(b.index) == b.books.total > 31000
    assert set(b.langs) == {"th", "zh", "zh-TW"}, b.langs
    for lang in b.langs:
        assert len(b.text(lang)) == b.books.total and b.text(lang)[vid("JHN", 3, 16)], lang
    assert all("god" in t for t in b.index.toks[vid("JHN", 3, 16)])


# -- references -----------------------------------------------------------------------------------

def refs(text, last=None):
    return [str(r) for r, _span in find_refs(text, last)]


def test_reference_phrasings():
    assert refs("John 3:16") == ["JHN 3:16"]
    assert refs("John three sixteen") == ["JHN 3:16"]
    assert refs("John chapter 3 verse 16") == ["JHN 3:16"]
    assert refs("Romans 8 verse 28") == ["ROM 8:28"]
    assert refs("Romans eight twenty-eight") == ["ROM 8:28"]
    assert refs("turn with me to Psalm 23") == ["PSA 23"]
    assert refs("First Corinthians 13") == ["1CO 13"]
    assert refs("second Timothy three sixteen") == ["2TI 3:16"]
    assert refs("Psalm one nineteen") == ["PSA 119"]
    assert refs("Psalm one oh three") == ["PSA 103"]
    assert refs("John 316") == ["JHN 3:16"]
    assert refs("Please open your Bibles to the Gospel of John, chapter three.") == ["JHN 3"]
    assert refs("the book of Ruth chapter 1") == ["RUT 1"]
    # Written the way notes write them.
    assert refs("Rom. 8:28") == ["ROM 8:28"]
    assert refs("1 Cor 13:4-7") == ["1CO 13:4-7"]
    assert refs("Ps 23") == ["PSA 23"]
    # Bare verses go with the last reference.
    last = Ref("ROM", 8, 28)
    assert refs("verses 28 to 30", last) == ["ROM 8:28-30"]
    assert refs("now look at verse 29", last) == ["ROM 8:29"]
    assert refs("vv. 28-30", last) == ["ROM 8:28-30"]
    assert refs("verse 29") == []                       # no chapter yet: nothing to go on
    # Not references.
    assert refs("let me mark two things") == []
    assert refs("acts 2 of kindness") == []
    assert refs("I have 2 points this morning") == []
    assert str(parse_ref("JHN 3:16-18")) == "JHN 3:16-18"
    assert str(parse_ref("Psalm 23")) == "PSA 23"
    assert parse_ref("not a reference") is None


# -- verses ---------------------------------------------------------------------------------------

def test_reading_with_reference():
    v = verses()
    v.expect("Turn with me to John chapter 3, verse 16.")
    hit = v.match("For God so loved the world that he gave his one and only Son,", ["th", "zh"])
    assert hit and hit["kind"] == "verse" and hit["code"] == "JHN 3:16", hit
    # The line where a verse starts gets the whole verse, labelled with the book's own name.
    assert hit["text"]["th"] == thai("JHN", 3, 16)
    assert display(hit, "th") == f"{thai('JHN', 3, 16)} (ยอห์น 3:16)"
    assert display(hit, "zh").endswith("3:16）")
    assert hit["ref"] == "John 3:16"
    # The rest of the verse: "continued", nothing new to show.
    hit = v.match("that whoever believes in him shall not perish but have eternal life.", ["th"])
    assert hit and hit["kind"] == "continued" and hit["code"] == "JHN 3:16", hit
    assert display(hit, "th") is None and hit["langs"] == ["th"]
    # The next verse.
    hit = v.match("For God did not send his Son into the world to condemn the world,", ["th"])
    assert hit and hit["kind"] == "verse" and hit["code"] == "JHN 3:17", hit
    assert hit["text"]["th"] == thai("JHN", 3, 17)


def test_line_spanning_two_verses():
    v = verses()
    v.expect("John 3:16")
    hit = v.match("For God so loved the world that he gave his one and only Son, that whoever believes in him", ["th"])
    assert hit and hit["code"] == "JHN 3:16", hit
    # Finishes 3:16 and starts 3:17: show 3:17.
    hit = v.match("shall not perish but have eternal life. For God did not send his Son into the world", ["th"])
    assert hit and hit["kind"] == "verse" and hit["code"] == "JHN 3:17", hit
    assert hit["text"]["th"] == thai("JHN", 3, 17)


def test_cross_version_readings():
    # Read aloud from the NIV, ESV or NKJV; matched against the BSB, WEB and KJV.
    v = verses()
    v.expect("Romans 8:28")
    hit = v.match("And we know that for those who love God all things work together for good,", ["th"])
    assert hit and hit["code"] == "ROM 8:28", hit                                  # ESV
    v = verses()
    v.expect("Psalm 23")
    got = [v.match(line, ["th"]) for line in ("The Lord is my shepherd, I lack nothing.",
                                               "He makes me lie down in green pastures,",
                                               "he leads me beside quiet waters, he refreshes my soul.")]
    assert [g and g["code"] for g in got] == ["PSA 23:1", "PSA 23:2", "PSA 23:3"], got
    v = verses()
    hit = v.match("But those who wait on the Lord shall renew their strength; they shall mount up with wings like eagles,", ["th"])
    assert hit and hit["code"] == "ISA 40:31", hit                                 # NKJV, no reference


def test_famous_verse_without_reference():
    for line, code in [("For God so loved the world that he gave his one and only Son,", "JHN 3:16"),
                       ("For I know the plans I have for you, declares the Lord, plans to prosper you and not to harm you,", "JER 29:11"),
                       ("I can do all this through him who gives me strength.", "PHP 4:13"),
                       ("Trust in the Lord with all your heart, and do not lean on your own understanding.", "PRO 3:5")]:
        hit = verses().match(line, ["th"])
        assert hit and hit["kind"] == "verse" and hit["code"] == code, (line, hit)


def test_preacher_own_words_are_not_verses():
    v = verses()
    v.expect("John chapter 3")
    for line in ["For God so loved you, church, that he gave his only son.",      # his own words inside
                 "Listen church, God so loved the world, amen?",
                 "Jesus is talking with a man named Nicodemus, a teacher who came to him at night.",
                 "A teacher who came to him at night.",
                 "God loves you so much that he sent his son for you."]:
        assert v.match(line, ["th"]) is None, line
    v = verses()
    v.expect("Proverbs 3")
    assert v.match("Don't lean on your own understanding, lean on him.", ["th"]) is None
    for line in ["I want to talk to you this morning about the love of God and what it means for your family.",
                 "Paul tells us that all things work together for good.",
                 "Love is patient and kind, and that is what our marriages need.",
                 "The plans God has for you are good plans, plans to give you a future."]:
        assert verses().match(line, ["th"]) is None, line


def test_sermon_clip():
    """The test sermon: only the line that quotes John 3:16 is Bible text."""
    v = verses()
    hits = []
    for line in (HERE / "clips" / "sermon-en.txt").read_text(encoding="utf-8").splitlines():
        hit = v.hear(line, ["th", "zh"])
        if hit:
            hits.append((line[:20], hit["kind"], hit["code"]))
    assert hits == [("In verse sixteen, Je", "verse", "JHN 3:16")], hits


def test_notes_open_the_window():
    # Too short to trust with no reference, fine once the notes say 1 Corinthians 13.
    line = "Love is patient, love is kind."
    assert verses().match(line, ["th"]) is None
    v = verses()
    v.expect_refs(["1CO 13:4-7", "not a ref"])
    hit = v.match(line, ["th"])
    assert hit and hit["code"] == "1CO 13:4", hit


def test_no_bible_for_language():
    v = verses()
    assert v.match("For God so loved the world that he gave his one and only Son,", ["ko"]) is None
    hit = v.match("For God so loved the world that he gave his one and only Son,", ["ko", "th"])
    assert hit and hit["langs"] == ["th"]


def test_threads():
    v = verses()
    errors = []

    def run():
        try:
            for _ in range(20):
                v.hear("For God so loved the world that he gave his one and only Son,", ["th"])
                v.hear("Good morning, church.", ["th"])
        except Exception as e:      # pragma: no cover - reported below
            errors.append(e)

    ts = [threading.Thread(target=run) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors, errors


def test_speed():
    v = verses()
    lines = (HERE / "clips" / "sermon-en.txt").read_text(encoding="utf-8").splitlines() + [
        "For God so loved the world that he gave his one and only Son,",
        "that whoever believes in him shall not perish but have eternal life.",
        "And we know that in all things God works for the good of those who love him,",
        "who have been called according to his purpose.",
        "I want to talk to you this morning about the love of God and what it means for your family.",
    ]
    times, rounds = [], []
    for _ in range(5):
        one = []
        for line in lines:
            t = time.perf_counter()
            v.hear(line, ["th", "zh"])
            one.append((time.perf_counter() - t) * 1000)
        times += one
        rounds.append(statistics.median(one))
    # The best round's typical line: a busy computer slows some rounds, but a real slowdown in the code
    # slows every round, so this catches the code and not the load.
    med = min(rounds)
    print(f"   (a line takes {med:.2f} ms typically, {max(times):.1f} ms at most)")
    assert med < 5, med


# -- sermon notes ---------------------------------------------------------------------------------

NOTES = [
    "Sermon: Born Again",
    "Main text: John 3:1-8",
    "Nicodemus came to Jesus at night.",
    "Pastor Somchai will pray after the sermon.",
    "Our team went to Chiang Mai last month and served there.",
    "Read Rom. 8:28 together.",
    "vv. 28-30 show the plan of God.",
    "Key verse: John 3:16",
    "Nicodemus came to Jesus at night.",
    "We have forty days until Easter.",
]


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


def make_docx(paras):
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f"<w:p><w:r><w:t xml:space='preserve'>{escape(p)}</w:t></w:r></w:p>" for p in paras)
    return _zip({"[Content_Types].xml": "<Types/>",
                 "word/document.xml": f"<?xml version='1.0' encoding='UTF-8'?><w:document xmlns:w='{w}'>"
                                      f"<w:body>{body}</w:body></w:document>"})


def make_pptx(slides, notes):
    ns = ("xmlns:p='http://schemas.openxmlformats.org/presentationml/2006/main' "
          "xmlns:a='http://schemas.openxmlformats.org/drawingml/2006/main'")

    def part(paras, number=None):
        fld = f"<a:p><a:fld id='1' type='slidenum'><a:t>{number}</a:t></a:fld></a:p>" if number else ""
        ps = "".join(f"<a:p><a:r><a:t>{escape(p)}</a:t></a:r></a:p>" for p in paras)
        return (f"<?xml version='1.0' encoding='UTF-8'?><p:sld {ns}><p:cSld><p:spTree><p:sp><p:txBody>"
                f"{ps}{fld}</p:txBody></p:sp></p:spTree></p:cSld></p:sld>")

    files = {"[Content_Types].xml": "<Types/>"}
    for i, s in enumerate(slides, 1):
        files[f"ppt/slides/slide{i}.xml"] = part(s)
    for i, n in enumerate(notes, 1):
        files[f"ppt/notesSlides/notesSlide{i}.xml"] = part(n, number=i)
    return _zip(files)


def make_odt(paras):
    ns = ("xmlns:office='urn:oasis:names:tc:opendocument:xmlns:office:1.0' "
          "xmlns:text='urn:oasis:names:tc:opendocument:xmlns:text:1.0'")
    body = "<text:h>" + escape(paras[0]) + "</text:h>" + "".join(
        "<text:p>" + escape(p).replace(" ", "<text:s/>", 1) + "</text:p>" for p in paras[1:])
    return _zip({"mimetype": "application/vnd.oasis.opendocument.text",
                 "content.xml": f"<?xml version='1.0' encoding='UTF-8'?><office:document-content {ns}>"
                                f"<office:body><office:text>{body}</office:text></office:body>"
                                "</office:document-content>"})


def make_pdf(lines):
    esc = [ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for ln in lines]
    content = "BT /F1 12 Tf 72 720 Td 16 TL " + " ".join(f"({ln}) Tj T*" for ln in esc) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            "/Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def check_notes(got, label):
    assert got["refs"][:4] == ["JHN 3:1-8", "ROM 8:28", "ROM 8:28-30", "JHN 3:16"], (label, got["refs"])
    hints = got["hints"]
    assert len(hints) <= 240, label
    names = [h.strip() for h in hints.split(",")]
    # People and places nobody could guess come first, then Bible names, then the books.
    assert names[:2] == ["Somchai", "Chiang Mai"], (label, names)
    assert "Nicodemus" in names and "Romans" in names and "John" in names, (label, names)
    assert names.index("Nicodemus") < names.index("Romans"), (label, names)
    assert "Pastor" not in hints and "Rom" not in names and "Easter" not in names, (label, names)
    s = got["sentences"]
    assert sum(1 for x in s if x.startswith("Nicodemus came")) == 1, (label, s)     # no repeats
    assert "Read Rom. 8:28 together." in s, (label, s)
    # A sentence read out from the notes is recognised (numbers said either way).
    kl = KnownLines(s)
    found = kl.find("we have 40 days until Easter")
    assert found and s[found[0]].startswith("We have forty days"), (label, found)
    assert kl.find("we have a lot of plans until Easter comes") is None


def test_notes_docx():
    check_notes(read_notes("Sermon notes.docx", make_docx(NOTES)), "docx")


def test_notes_pptx():
    half = len(NOTES) // 2
    got = read_notes("slides.pptx", make_pptx([NOTES[:half]], [NOTES[half:]]))
    check_notes(got, "pptx")
    assert not any(x.strip() == "1" for x in got["sentences"])           # the slide number field


def test_notes_odt():
    check_notes(read_notes("notes.odt", make_odt(NOTES)), "odt")


def test_notes_text_and_markdown():
    check_notes(read_notes("notes.txt", "\n".join(NOTES).encode("utf-8")), "txt")
    md = "# Born Again\n\n" + "\n".join(f"- **{n}**" if i % 2 else f"* {n}" for i, n in enumerate(NOTES[1:]))
    check_notes(read_notes("notes.md", md.encode("utf-8")), "md")
    check_notes(read_notes("notes.txt", "\n".join(NOTES).encode("utf-16")), "utf-16")


def test_notes_pdf():
    try:
        import pypdf  # noqa: F401
    except ImportError:
        print("   (pypdf not installed: PDF check skipped)")
        return
    check_notes(read_notes("notes.pdf", make_pdf(NOTES)), "pdf")


def test_notes_errors():
    def message(name, data):
        try:
            read_notes(name, data)
        except NotesError as e:
            return str(e)
        raise AssertionError(f"{name} should not be read")

    assert "Export" in message("Sermon.key", b"PK\x03\x04 anything")
    assert "Export" in message("Sermon.pages", b"whatever")
    assert "Save As" in message("old.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 100)
    assert "Word (.docx)" in message("photo.jpg", b"\xff\xd8\xff\xe0 not notes")
    assert message("empty.txt", b"")
    assert message("broken.docx", b"not a zip at all")
    bomb = _zip({"word/document.xml": "<!DOCTYPE x [<!ENTITY a 'aaaa'>]><x>&a;</x>"})
    assert message("bomb.docx", bomb)
    # Messages never quote the notes.
    secret = "Confidential prayer request for Somchai"
    assert secret not in message("x.docx", _zip({"word/document.xml": f"<w:document>{secret}"}))


if __name__ == "__main__":
    tests = [(k, v) for k, v in list(globals().items()) if k.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"PASS ({len(tests)} checks)")
