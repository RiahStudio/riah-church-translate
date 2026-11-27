"""Builds the Bible verse files the app ships (riah_translate/bible/*.json.xz) from open Bibles.

When the preacher reads a verse, the app can show the real verse from a Thai or Chinese Bible
instead of a machine translation (riah_translate/verses.py). For that it needs:

* English Bibles to recognise the verse by: Berean Standard (bsb), World English (web) and King
  James (kjv). Preachers read many versions (NIV, ESV, NKJV...); these three between them cover
  most of the wording. Only used for matching, never shown.
* The verse text to show: Thai Freedom Bible (th), Chinese Union Version simplified (zh) and
  traditional (zh-TW).

Every text here is public domain (checked in each zip's about page, 2026-11-23). The Thai King
James Version is not used: it is CC BY-NC-ND.

Verses are stored in Bible order (66 books, English verse numbering) as one flat list per file, so
verse number i means the same verse in every file. A verse a Bible doesn't have (left out, or joined
with the verse before it in the Chinese Union Version) is "".

Run on a Linux test machine (standard library only):
  bash tools/bible/fetch.sh ~/bible-data
  python3 tools/bible/build_verses.py ~/bible-data
"""
from __future__ import annotations

import argparse
import json
import lzma
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(ROOT, "riah_translate", "bible")
sys.path.insert(0, HERE)

from build_bible_words import BOOKS, fill_gaps, zh_clean  # noqa: E402

# The verse-per-line files use a few older book codes.
VPL_CODE = {"SOL": "SNG", "EZE": "EZK", "JOE": "JOL", "NAH": "NAM", "MAR": "MRK", "JOH": "JHN",
            "PHI": "PHP", "JAM": "JAS", "1JO": "1JN", "2JO": "2JN", "3JO": "3JN"}
CODES = [c for c, _ in BOOKS]

ENGLISH = [
    dict(key="bsb", id="engbsb", name="Berean Standard Bible", license="Public domain"),
    dict(key="web", id="engwebp", name="World English Bible", license="Public domain"),
    dict(key="kjv", id="eng-kjv", name="King James Version (1769)", license="Public domain"),
]
TARGETS = [
    dict(lang="th", id="thafb", name="Thai Freedom Bible (draft)", license="Public domain"),
    dict(lang="zh", id="cmn-cu89s", name="Chinese Union Version, simplified (新标点和合本)", license="Public domain"),
    dict(lang="zh-TW", id="cmn-cu89t", name="Chinese Union Version, traditional (新標點和合本)", license="Public domain"),
]


def read_vpl(data, bid):
    """{(book, chapter, verse): text} for the 66 books (the King James file also has the Apocrypha)."""
    out = {}
    with open(os.path.join(data, bid, f"{bid}_vpl.txt"), encoding="utf-8") as f:
        for line in f:
            m = re.match(r"(\w{3}) (\d+):(\d+) ?(.*)", line.rstrip("\n"))
            if not m:
                continue
            code = VPL_CODE.get(m.group(1), m.group(1))
            if code in CODES:
                out[(code, int(m.group(2)), int(m.group(3)))] = m.group(4).strip()
    return out


def _spaces(text):
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def balance(text, pairs):
    """A verse cut out of a longer speech often has a quote mark that opens and never closes (or
    the other way round). On its own on the screen that looks like a mistake, so drop the strays."""
    for a, b in pairs:
        if text.count(a) == text.count(b):
            continue
        keep, stack = [True] * len(text), []
        for i, ch in enumerate(text):
            if ch == a:
                stack.append(i)
            elif ch == b:
                if stack:
                    stack.pop()
                else:
                    keep[i] = False
        for i in stack:
            keep[i] = False
        text = "".join(ch for ch, k in zip(text, keep) if k)
    return _spaces(text)


def clean_en(text):
    # King James: [ ] marks words the translators added; ¶ marks a paragraph.
    return _spaces(text.replace("¶", " ").replace("[", "").replace("]", ""))


def clean_th(text):
    # The Freedom Bible marks word breaks with a zero-width space; Thai only shows phrase spaces.
    text = _spaces(text.replace("\u200b", ""))
    return balance(text, [("“", "”"), ("‘", "’"), ("«", "»")])


def clean_zh(text):
    # [ ] marks words the translators added; * is a stray mark in two verses.
    text = zh_clean(text.replace("[", "").replace("]", "").replace("*", ""))
    text = re.sub(r"(?<=[^\x00-\x7f])\s+(?=[^\x00-\x7f])", "", text)   # spaces between poetry lines
    return balance(text, [("「", "」"), ("『", "』"), ("（", "）"), ("“", "”"), ("‘", "’")])


def build(data, out_dir):
    en = {e["key"]: read_vpl(data, e["id"]) for e in ENGLISH}
    tg = {t["lang"]: read_vpl(data, t["id"]) for t in TARGETS}
    tg["zh"] = fill_gaps(tg["zh"], {(k[0], k[1], k[2]): v for k, v in tg["zh-TW"].items()})

    # Every verse any of the Bibles has, in Bible order.
    counts = {}
    for src in list(en.values()) + list(tg.values()):
        for (b, c, v) in src:
            counts.setdefault(b, {})
            counts[b][c] = max(counts[b].get(c, 0), v)
    books, order = [], []
    for code in CODES:
        chapters = [counts[code][c] for c in range(1, max(counts[code]) + 1)]
        books.append([code, chapters])
        for c, n in enumerate(chapters, 1):
            order += [(code, c, v) for v in range(1, n + 1)]

    os.makedirs(out_dir, exist_ok=True)
    sizes = {}

    def write(name, doc):
        raw = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        packed = lzma.compress(raw, preset=9 | lzma.PRESET_EXTREME)
        with open(os.path.join(out_dir, name), "wb") as f:
            f.write(packed)
        sizes[name] = (len(raw), len(packed))

    about = ("Bible text for recognising a verse the preacher reads and showing it from a Thai or "
             "Chinese Bible. Built by tools/bible/build_verses.py from ebible.org verse-per-line files.")
    # English: one [bsb, web, kjv] list per verse, side by side so the packer finds the shared words.
    write("en.json.xz", dict(
        v=1, about=about, books=books, versions=[e["key"] for e in ENGLISH],
        sources=[{k: e[k] for k in ("key", "id", "name", "license")} for e in ENGLISH],
        text=[[clean_en(en[e["key"]].get(k, "")) for e in ENGLISH] for k in order]))
    for t in TARGETS:
        clean = clean_th if t["lang"] == "th" else clean_zh
        src = tg[t["lang"]]
        write(f"{t['lang']}.json.xz", dict(
            v=1, about=about, lang=t["lang"], verses=len(order),
            source={k: t[k] for k in ("id", "name", "license")},
            text=[clean(src[k]) if src.get(k) else "" for k in order]))

    # The chapter and verse counts on their own, so reading "John 3:16" doesn't need the whole Bible.
    with open(os.path.join(out_dir, "books.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(dict(about="Verses per chapter for each book, English numbering.", books=books), f,
                  separators=(",", ":"))
        f.write("\n")

    print(f"{len(order)} verses in {len(books)} books")
    for name, (raw, packed) in sizes.items():
        print(f"  {name:14} {raw / 1e6:6.2f} MB raw  {packed / 1e6:5.2f} MB packed")
    print(f"  total packed: {sum(p for _, p in sizes.values()) / 1e6:.2f} MB")
    for lang, src in tg.items():
        missing = [k for k in order if not src.get(k)]
        print(f"  {lang}: {len(missing)} verses without text (left out, or joined to the verse before)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("data", nargs="?", default=os.path.expanduser("~/bible-data"))
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    build(a.data, a.out)
