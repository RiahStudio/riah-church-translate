"""Finds how the translator (NLLB) spells each Bible name and book, and keeps the spellings that
differ from the Bible's ("misses"), so the app can swap them for the Bible spelling.

Each name goes into a few short sermon sentences. The names the translator spells right show what
each sentence turns into around a name, so cutting that away leaves its spelling of the others.
A miss is only kept when it can't be an ordinary word (it's rare in the Bible text) or another name.

Run on a Linux test machine after build_bible_words.py (needs the app's Python packages and the Bibles):
  python tools/bible/probe_bible_words.py ~/bible-data
Writes the "miss" part of riah_translate/bible_words.json and tools/bible/_out/misses.tsv.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from riah_translate.mt import NllbTranslator          # noqa: E402
from riah_translate.langs import lang                # noqa: E402
import build_bible_words as B                        # noqa: E402

OUT = os.path.join(ROOT, "riah_translate", "bible_words.json")
NAME_SAY = ["Then {} went home.", "Today we read about {}.", "{} is in the Bible."]
BOOK_SAY = ["Please open your Bible to {} chapter 3.", "{} chapter 5 says this."]
CONTROLS = ("David", "Moses")               # still in the list so saved passes line up; not used to cut
BOOK_CONTROLS = ("Genesis", "Exodus")
LANGS = ("th", "zh", "zh-TW")
SPOKEN_NUM = {"1": "First", "2": "Second", "3": "Third"}


# Thai marks that sit on the letter before them, and vowels written before their consonant: a piece
# that starts or ends with one was cut mid-syllable.
TH_MARKS = tuple("ะาำิีึืุูั็่้๊๋์ํๅ")
TH_LEADING = tuple("เแโใไ")
PUNCT = " ,，、。.!！?？:：;；\"'“”‘’"


def own_extra(longer, miss, bits):
    """True if what `longer` adds around `miss` isn't the end or start of a sentence frame."""
    before, _, after = longer.partition(miss)
    return not (before and any(b.endswith(before) for b in bits)) and not (after and any(b.startswith(after) for b in bits))


def main(data):
    doc = json.load(open(OUT, encoding="utf-8"))
    mt = None                                   # loaded only if a pass isn't saved yet
    th_text = "\n".join(B.read_vpl(data, "thafb").values()).replace("​", "")
    tw_raw = B.read_vpl(data, "cmn-cu89t")
    zh_text = "\n".join(B.fill_gaps(B.read_vpl(data, "cmn-cu89s"), tw_raw).values())
    tw_text = "\n".join(tw_raw.values())
    bible = {"th": th_text, "zh": zh_text, "zh-TW": tw_text}
    key = {"th": "th", "zh": "zh", "zh-TW": "zh-TW"}

    # What to probe: (kind, id, the words said, the Bible spelling per language)
    items = []
    for w, e in doc["names"].items():
        items.append(("name", w, w, {t: e.get(key[t]) for t in LANGS}))
    for b in doc["books"]:
        said = [b["en"]] if not b["num"] else [f"{SPOKEN_NUM[b['num']]} {b['en']}", f"{b['num']} {b['en']}"]
        for s in said:
            items.append(("book", b["code"], s, {"th": b["th"], "zh": b["zh"], "zh-TW": b["zh-TW"]}))

    sentences = []                              # (item index or control tag, template, sentence)
    for kind, says, controls in (("name", NAME_SAY, CONTROLS), ("book", BOOK_SAY, BOOK_CONTROLS)):
        for t in says:
            for c in controls:
                sentences.append((("ctl", kind, c), t, t.format(c)))
    for i, (kind, _id, said, _w) in enumerate(items):
        for t in (NAME_SAY if kind == "name" else BOOK_SAY):
            sentences.append((i, t, t.format(said)))
    print(f"{len(items)} names and books, {len(sentences)} sentences x {len(LANGS)} languages", flush=True)

    # Each pass takes 5 to 25 minutes, so each one is saved as it finishes and a rerun on the same
    # sentences picks up where the last one stopped.
    cache_dir = os.path.join(HERE, "_out", "probe-cache")
    os.makedirs(cache_dir, exist_ok=True)
    tag = hashlib.sha1("\n".join(s[2] for s in sentences).encode("utf-8")).hexdigest()[:12]
    outs = {}                                   # (sentence index, lang, beam) -> text
    for tgt in LANGS:
        code = lang(tgt)["nllb"]
        for beam in (4, 1):                      # final lines, and mid-sentence drafts
            saved = os.path.join(cache_dir, f"{tgt}-beam{beam}-{tag}.json")
            if os.path.exists(saved):
                texts = json.load(open(saved, encoding="utf-8"))
                print(f"{tgt} beam {beam} from the last run", flush=True)
            else:
                mt = mt or NllbTranslator(threads=os.cpu_count() or 4)
                src = [mt._src_pieces(s[2], "en") for s in sentences]
                res = mt.translator.translate_batch(src, target_prefix=[[code]] * len(src), beam_size=beam,
                                                    max_decoding_length=128, repetition_penalty=1.1,
                                                    no_repeat_ngram_size=4, max_batch_size=48)
                texts = [mt._text([p for p in r.hypotheses[0] if p != code], tgt, None, False) for r in res]
                with open(saved + ".part", "w", encoding="utf-8") as f:
                    json.dump(texts, f, ensure_ascii=False)
                os.replace(saved + ".part", saved)
                print(f"{tgt} beam {beam} done", flush=True)
            for j, text in enumerate(texts):
                outs[(j, tgt, beam)] = text

    # What each sentence turns into around a name, learned from the names it spells right
    # ("แล้ว|ก็กลับบ้าน", "然后|回家了。"). Cutting a wrong output on the same frame leaves the
    # translator's spelling of the name.
    frames = defaultdict(Counter)
    for j, (who, t, _s) in enumerate(sentences):
        if isinstance(who, tuple):
            continue
        want = items[who][3]
        for tgt in LANGS:
            for beam in (4, 1):
                out = outs[(j, tgt, beam)]
                if want.get(tgt) and out.count(want[tgt]) == 1:
                    i = out.index(want[tgt])
                    left, right = out[:i], out[i + len(want[tgt]):]
                    if not right.startswith(TH_MARKS) and not left.endswith(TH_LEADING):
                        frames[(t, tgt)][(left, right)] += 1

    def cut(out, t, tgt):
        best = None
        for (left, right), n in frames[(t, tgt)].items():
            if n >= 2 and len(left) + len(right) < len(out) and out.startswith(left) and out.endswith(right):
                if best is None or len(left) + len(right) > len(best[0]) + len(best[1]):
                    best = (left, right)
        return None if best is None else out[len(best[0]):len(out) - len(best[1])].strip(PUNCT)

    # Every Bible spelling of a name or book, so a miss never turns one name into another.
    spelled = {t: set() for t in LANGS}
    for _k, _i, _s, want in items:
        for t in LANGS:
            if want.get(t):
                spelled[t].add(want[t])

    miss = {t: defaultdict(Counter) for t in LANGS}
    heard_in = defaultdict(set)                 # (lang, name, miss) -> the sentences it came out of
    rows, hits, total = [], Counter(), Counter()
    for j, (who, t, s) in enumerate(sentences):
        if isinstance(who, tuple):
            continue
        kind, _id, said, want = items[who]
        for tgt in LANGS:
            if not want.get(tgt):
                continue
            for beam in (4, 1):
                out = outs[(j, tgt, beam)]
                total[(kind, tgt)] += 1
                if want[tgt] in out:
                    hits[(kind, tgt)] += 1
                    continue
                got = cut(out, t, tgt)
                rows.append((kind, _id, said, tgt, beam, want[tgt], got or "", out))
                if not got or got == want[tgt] or len(got) > 3 * len(want[tgt]) + 4:
                    continue
                if len(got) < (3 if tgt == "th" else 2):
                    continue
                if got.startswith(TH_MARKS) or got.endswith(TH_LEADING) or re.search(r"[，、；：,;:]", got):
                    continue                     # half a syllable, or a piece of the sentence
                if re.search(r"[A-Za-z]", got) and not re.fullmatch(r"[A-Za-z][A-Za-z' -]*", got):
                    continue                     # half Latin, half Thai: not a clean spelling
                # A name's miss must not be an ordinary word or another name. A book's may be the
                # name it's called after (约翰 for the Gospel of John): the app only swaps that one
                # right before a chapter number.
                if kind == "name" and (got in spelled[tgt] or bible[tgt].count(got) > 3):
                    continue
                k = _id if kind == "book" else said
                miss[tgt][k][got] += 1
                heard_in[(tgt, k, got)].add(t)

    # Keep a miss when two different sentences gave it. One sentence alone can still glue a word on
    # ("了关于..."), and swapping that would drop the word. A longer form of a kept miss stays too
    # (ซากายอส next to ซากาย), unless the extra is a piece of a sentence frame (ของซากาย), so a
    # longer form is never half replaced.
    frame_bits = {t: set() for t in LANGS}
    for (_t, tgt), c in frames.items():
        for (left, right), n in c.items():
            if n >= 2:
                frame_bits[tgt].update(x for x in (left.strip(PUNCT), right.strip(PUNCT)) if x)
    out_miss = {}
    for t in LANGS:
        out_miss[t] = {}
        for k, c in miss[t].items():
            keep = [g for g, _n in c.most_common() if len(heard_in[(t, k, g)]) >= 2][:4]
            longer = [g for g, _n in c.most_common()
                      if g not in keep and any(m in g and own_extra(g, m, frame_bits[t]) for m in keep)][:4]
            if keep:
                out_miss[t][k] = keep + longer
    doc["miss"] = out_miss
    doc["probe"] = {f"{k}/{t}": f"{hits[(k, t)]}/{total[(k, t)]}" for (k, t) in total}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    os.makedirs(os.path.join(HERE, "_out"), exist_ok=True)
    with open(os.path.join(HERE, "_out", "misses.tsv"), "w", encoding="utf-8") as f:
        f.write("kind\tid\tsaid\tlang\tbeam\tbible\tgot\toutput\n")
        for r in rows:
            f.write("\t".join(str(x) for x in r) + "\n")
    for (k, t), n in sorted(total.items()):
        print(f"right already: {k} {t} {hits[(k, t)]}/{n}")
    print("misses kept:", {t: sum(len(v) for v in out_miss[t].values()) for t in LANGS})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("data", nargs="?", default=os.path.expanduser("~/bible-data"))
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main(a.data)
