"""How much do the Bible words help, and what does each beam size cost?

Translates real Bible verses that name people and places (World English Bible) into Thai and
Chinese, one line at a time like the live app, and compares with the Thai Freedom Bible and the
Chinese Union Version:
  * names right: the Bible's spelling of each name in the verse is in the translation
  * chrF: how close the whole line is to the Bible's own (0-100, higher is closer)
  * seconds per line, at beam 4 (the app's finished lines), 2 and 1 (the mid-sentence drafts)

Run on a Linux test machine (needs the app's Python packages and the Bibles):
  python tools/bible/bench_bible_words.py ~/bible-data [--lines 150] [--threads 4]
"""
from __future__ import annotations

import argparse
import os
import random
import re
import sys
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from riah_translate.bible_words import BibleWords   # noqa: E402
from riah_translate.mt import NllbTranslator         # noqa: E402
import build_bible_words as B                       # noqa: E402


def chrf(hyp, ref, n=6, beta=2.0):
    hyp, ref = re.sub(r"\s+", "", hyp), re.sub(r"\s+", "", ref)
    ps, rs = [], []
    for k in range(1, n + 1):
        h = Counter(hyp[i:i + k] for i in range(len(hyp) - k + 1))
        r = Counter(ref[i:i + k] for i in range(len(ref) - k + 1))
        if not h or not r:
            continue
        m = sum((h & r).values())
        ps.append(m / sum(h.values()))
        rs.append(m / sum(r.values()))
    if not ps:
        return 0.0
    p, r = sum(ps) / len(ps), sum(rs) / len(rs)
    return 0.0 if p + r == 0 else 100 * (1 + beta ** 2) * p * r / (beta ** 2 * p + r)


def main(a):
    bw = BibleWords()
    eng = B.read_vpl(a.data, "engwebp")
    tw_raw = B.read_vpl(a.data, "cmn-cu89t")
    ref = {"th": {k: v.replace("​", "") for k, v in B.read_vpl(a.data, "thafb").items()},
           "zh": B.fill_gaps(B.read_vpl(a.data, "cmn-cu89s"), tw_raw)}
    pool = [k for k, v in eng.items()
            if 6 <= len(v.split()) <= 28 and k in ref["th"] and k in ref["zh"]
            and any(bw.names[w].get("th") and bw.names[w].get("zh") for w in bw.names_in(v))]
    random.Random(7).shuffle(pool)
    keys = pool[:a.lines]
    print(f"{len(pool)} verses name someone; testing {len(keys)}", flush=True)

    mt = NllbTranslator(threads=a.threads)
    for beam in a.beams:
        took = []
        res = {t: {} for t in ("th", "zh")}
        for k in keys:
            src = eng[k]
            for t in ("th", "zh"):
                t0 = time.perf_counter()
                (_s, pieces), = mt._run([(mt._src_pieces(src, "en"), "tha_Thai" if t == "th" else "zho_Hans", None)], beam)
                took.append(time.perf_counter() - t0)
                raw = mt._text(pieces, t, {"bible": False}, False)
                res[t][k] = (raw, bw.fix(src, raw, t))
        took.sort()
        print(f"\nbeam {beam}: median {took[len(took) // 2]:.2f}s per line, slowest tenth {took[int(len(took) * 0.9)]:.2f}s")
        for t in ("th", "zh"):
            right = {"raw": 0, "fixed": 0}
            total, cf = 0, {"raw": [], "fixed": []}
            for k in keys:
                src = eng[k]
                raw, fixed = res[t][k]
                for w in bw.names_in(src):
                    want = bw.names[w].get(t)
                    if not want:
                        continue
                    total += 1
                    right["raw"] += bw._ok(w, want, raw, t)
                    right["fixed"] += bw._ok(w, want, fixed, t)
                cf["raw"].append(chrf(raw, ref[t][k]))
                cf["fixed"].append(chrf(fixed, ref[t][k]))
            print(f"  {t}: names right {right['raw']}/{total} -> {right['fixed']}/{total} with Bible words; "
                  f"chrF {sum(cf['raw']) / len(keys):.1f} -> {sum(cf['fixed']) / len(keys):.1f}")
            if beam == 4 and a.show:
                for k in keys[:a.show]:
                    raw, fixed = res[t][k]
                    if raw != fixed:
                        print(f"    {k}: {raw}\n    {' ' * len(k)}  -> {fixed}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("data", nargs="?", default=os.path.expanduser("~/bible-data"))
    ap.add_argument("--lines", type=int, default=150)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--show", type=int, default=40)
    ap.add_argument("--beams", type=lambda v: [int(x) for x in v.split(",")], default=[1, 2, 4])
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main(a)
