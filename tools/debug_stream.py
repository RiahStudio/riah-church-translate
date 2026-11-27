"""Logs every streaming pass (buffer, words heard, words confirmed) to see where words go missing.

    python tools/debug_stream.py tests/clips/sermon-en.wav base.en > tests/results/debug.txt
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from riah_translate import asr  # noqa: E402
from riah_translate.audio import load_file  # noqa: E402
from riah_translate.models import load_whisper  # noqa: E402

audio = load_file(sys.argv[1])
model = load_whisper(sys.argv[2] if len(sys.argv) > 2 else "base.en", "cpu", os.cpu_count() or 4)
tr = asr.StreamingTranscriber(model, "en")

orig_transcribe, orig_insert, orig_agree = tr._transcribe, tr.hyp.insert, tr.hyp.agree


def show(ws):
    return " ".join(f"{w.text.strip()}@{w.start:.2f}" for w in ws)


def transcribe():
    ws = orig_transcribe()
    print(f"    heard   : {show(ws)}")
    return ws


def insert(ws):
    out = orig_insert(ws)
    if len(out) != len(ws):
        print(f"    dropped : {show([w for w in ws if w not in out])}  (last_time {tr.hyp.last_time:.2f})")
    return out


def agree(ws):
    before = list(tr.hyp.buffer)
    out = orig_agree(ws)
    print(f"    prev buf: {show(before)}\n    confirm : {show(out)}")
    return out


tr._transcribe, tr.hyp.insert, tr.hyp.agree = transcribe, insert, agree
clock, pos = 0.0, 0
while True:
    clock = max(clock, pos / 16000 + 1.0)
    upto = min(len(audio), int(clock * 16000))
    tr.push(audio[pos:upto])
    pos = upto
    final = pos >= len(audio)
    print(f"--- pass at {clock:.2f}s  buffer {tr.offset:.2f}-{tr.now:.2f}  silent tail {tr._silent_tail_sec():.2f}s")
    t0 = time.perf_counter()
    lines, partial = tr.step(final=final)
    clock += time.perf_counter() - t0
    for ln in lines:
        print(f"  LINE [{ln['start']:.2f}-{ln['end']:.2f}] {ln['text']}")
    print(f"    pending : {show(tr.pending)}")
    if final:
        break
