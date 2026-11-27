"""Compares speech models on real accented sermons, with no translation.

    python tests/accent_compare.py --models base.en small.en --clips dennis-sy:1200 tanchi-full:0

Each clip is tests/clips/real/<name>.wav. Its reference text comes from the YouTube captions
saved next to it (<name>.en-orig.vtt, which cover the whole video), starting <offset> seconds
into the video. Those captions are another machine's guess, so treat the error rates as a
comparison between models, not as the truth. Run it on a fast test machine, not the church computer.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REAL = ROOT / "tests" / "clips" / "real"
TS = re.compile(r"(\d+):(\d+):(\d+)\.(\d+)")


def secs(m):
    h, mi, s, ms = (int(x) for x in m.groups())
    return h * 3600 + mi * 60 + s + ms / 1000


def vtt_words(path):
    """(time, word) pairs from YouTube's rolling auto-captions, each word once."""
    out = []
    cue_start = 0.0
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if "-->" in line:
            cue_start = secs(TS.search(line))
            continue
        if "<c>" not in line and not TS.search(line):
            continue                      # a repeat of the line above, or a header
        t = cue_start
        for part in re.split(r"(<\d+:\d+:\d+\.\d+>)", line):
            m = TS.search(part) if part.startswith("<") else None
            if m:
                t = secs(m)
                continue
            for w in re.sub(r"</?c[^>]*>", "", part).split():
                out.append((t, w))
    return out


def words(t):
    return re.sub(r"[^a-z0-9' ]+", " ", t.lower().replace("-", " ")).split()


def fitted_wer(ref, hyp):
    """Word error rate where the reference may start earlier and end later than the clip
    (the cut points never land exactly on a word)."""
    n, m = len(ref), len(hyp)
    prev = [0] * (n + 1)                  # skipping reference words before the clip is free
    for j in range(1, m + 1):
        cur = [j] + [0] * n
        for i in range(1, n + 1):
            cur[i] = min(prev[i] + 1, cur[i - 1] + 1, prev[i - 1] + (ref[i - 1] != hyp[j - 1]))
        prev = cur
    end = min(range(n + 1), key=lambda i: prev[i])   # ... and after it
    return prev[end] / max(1, m)


def run(clip, model, out):
    cmd = [sys.executable, "-m", "riah_translate", "--offline", str(clip), "--out", str(out),
           "--from", "en", "--to", "th", "--mt", "none", "--model", model]
    done = out.exists() and '"type": "summary"' in out.read_text(encoding="utf-8")
    if not done:                          # a finished run from before is reused
        subprocess.run(cmd, check=True, cwd=ROOT, stdout=subprocess.DEVNULL)
    ev = [json.loads(l) for l in open(out, encoding="utf-8") if l.strip()]
    return [e for e in ev if e["type"] == "line"], next(e for e in ev if e["type"] == "summary")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--clips", nargs="+", required=True, help="name:offset-in-video-seconds")
    a = ap.parse_args()
    res_dir = ROOT / "tests" / "results" / "accent"
    res_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for spec in a.clips:
        name, offset = spec.split(":")
        clip = REAL / f"{name}.wav"
        vtt = REAL / f"{name}.en-orig.vtt"
        ref_all = vtt_words(vtt) if vtt.exists() else None
        for model in a.models:
            tag = model.split("/")[-1]
            lines, summ = run(clip, model, res_dir / f"{name}--{tag}.jsonl")
            said = " ".join(l["src"] for l in lines)
            (res_dir / f"{name}--{tag}.txt").write_text("\n".join(l["src"] for l in lines), encoding="utf-8")
            score = None
            if ref_all:
                lo, hi = float(offset) - 15, float(offset) + summ["audioSec"] + 15
                ref = words(" ".join(w for t, w in ref_all if lo <= t <= hi))
                score = fitted_wer(ref, words(said))
            lags = sorted(l["lag"] for l in lines) or [0]
            short = sum(1 for l in lines if len(l["src"].split()) <= 2)
            row = dict(clip=name, model=tag, wer=None if score is None else round(score, 3),
                       rtf=summ["realtimeFactor"], lagMedian=lags[len(lags) // 2], lagWorst=lags[-1],
                       lines=len(lines), shortLines=short)
            rows.append(row)
            print(json.dumps(row), flush=True)
    (res_dir / "summary.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
