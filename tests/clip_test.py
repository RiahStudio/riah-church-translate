"""End-to-end check on a recorded clip: transcribe + translate, then score it.

    python tests/clip_test.py tests/clips/sermon-en.wav --script tests/clips/sermon-en.txt --to th zh
    python tests/clip_test.py ... --app "dist/Riah Church Translate.app/Contents/MacOS/Riah Church Translate"   (CI: test the built app)

Checks: enough lines came out, the English matches the script (word error rate), every line got a
translation in each target script (e.g. Thai letters for Thai), and how far behind the speaker it ran:
when each translation first showed (as a draft, while the sentence was still being spoken) and when
it was settled.
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = {"th": r"[฀-๿]", "zh": r"[一-鿿]", "zh-TW": r"[一-鿿]",
           "my": r"[က-႟]", "ko": r"[가-힯]", "ja": r"[぀-ヿ一-鿿]",
           "lo": r"[຀-໿]", "km": r"[ក-៿]", "ru": r"[Ѐ-ӿ]", "hi": r"[ऀ-ॿ]"}


def words(t):
    return re.sub(r"[^a-z0-9' ]+", " ", t.lower().replace("-", " ")).split()


def wer(ref, hyp):
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return d[len(h)] / max(1, len(r))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--script", help="what was really said (for the word error rate)")
    ap.add_argument("--from", dest="src", default="en")
    ap.add_argument("--to", dest="tgts", nargs="+", default=["th"])
    ap.add_argument("--model")
    ap.add_argument("--mt", default="nllb")
    ap.add_argument("--app", help="run this built app instead of the source tree")
    ap.add_argument("--out", help="keep the events file here")
    ap.add_argument("--hints", help="names and words that may come up, as typed in the Words box")
    ap.add_argument("--no-drafts", action="store_true")
    ap.add_argument("--max-wer", type=float, default=0.15)
    ap.add_argument("--min-lines", type=int, default=5)
    a = ap.parse_args()
    # A Windows console with a non-UTF-8 code page can't print Thai; the scores still matter.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    out = a.out or str(Path(tempfile.mkdtemp()) / "events.jsonl")
    cmd = ([a.app] if a.app else [sys.executable, "-m", "riah_translate"]) + [
        "--offline", a.audio, "--out", out, "--from", a.src, "--to", *a.tgts, "--mt", a.mt]
    if a.no_drafts:
        cmd += ["--no-drafts"]
    if a.model:
        cmd += ["--model", a.model]
    if a.hints:
        cmd += ["--hints", a.hints]
    print("running:", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=Path(__file__).resolve().parents[1])

    ev = [json.loads(l) for l in open(out, encoding="utf-8") if l.strip()]
    lines = {e["id"]: e for e in ev if e["type"] == "line"}
    trans = {(e["id"], e.get("lang", a.tgts[0])): e for e in ev if e["type"] == "translation"}
    first_draft = {}
    for e in ev:
        if e["type"] == "draft":
            k = (e["seg"], e["lang"])
            first_draft[k] = min(first_draft.get(k, e["at"]), e["at"])
    summary = next(e for e in ev if e["type"] == "summary")
    said = " ".join(l["src"] for l in lines.values())
    problems = []

    def med(xs):
        xs = sorted(xs)
        return f"median {xs[len(xs) // 2]:.1f}s, worst {xs[-1]:.1f}s" if xs else "none"

    print()
    print(f"{len(lines)} lines from {summary['audioSec']}s of audio; Whisper busy {summary['asrBusySec']}s "
          f"(x{summary['realtimeFactor']} of real time)")
    if len(lines) < a.min_lines:
        problems.append(f"only {len(lines)} lines")
    if a.script:
        ref = Path(a.script).read_text(encoding="utf-8")
        score = wer(ref, said)
        print(f"word error rate vs script: {score:.1%}")
        if a.src == "en" and score > a.max_wer:
            problems.append(f"word error rate {score:.1%} > {a.max_wer:.0%}")
    print(f"original on screen: {med(l['lag'] for l in lines.values())} after the line ended")
    for t in a.tgts:
        got = {i: trans[(i, t)] for i in lines if (i, t) in trans}
        starts = []
        for i, l in lines.items():
            if i not in got:
                continue
            first = min(first_draft.get((l.get("seg"), t), got[i]["at"]), got[i]["at"])
            starts.append(first - l["start"])
        kept = sum(1 for l in lines.values() if l.get("keepDraft"))
        print(f"{t}: first shows {med(starts)} after the line STARTED; settled {med(g['lag'] for g in got.values())} "
              f"after it ended; drafts kept on {kept}/{len(lines)} lines")
        pat = SCRIPTS.get(t)
        missing = [i for i in lines if i not in got or not got[i]["dst"].strip()]
        wrong_script = [i for i in got if pat and not re.search(pat, got[i]["dst"])]
        if missing:
            problems.append(f"{len(missing)} lines without a {t} translation")
        if a.mt != "none" and wrong_script:
            problems.append(f"{len(wrong_script)} translations not in the {t} script")
    print()
    for i, l in lines.items():
        print(f"  {l['src']}")
        for t in a.tgts:
            print(f"    {t}: {trans.get((i, t), {}).get('dst', '(none)')}")
    print()
    if problems:
        print("FAIL: " + "; ".join(problems))
        sys.exit(1)
    print("PASS")


if __name__ == "__main__":
    main()
