"""Times one Whisper pass on a short buffer for a few setups, to tune live lag on a given computer.

    python tools/profile_asr.py tests/clips/sermon-en.wav
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from faster_whisper import WhisperModel  # noqa: E402

from riah_translate.audio import load_file  # noqa: E402
from riah_translate.models import whisper_repo  # noqa: E402

audio = load_file(sys.argv[1])
buf = audio[int(6.8 * 16000):int(13.5 * 16000)]   # one sentence plus a little of the next
print("cpus", os.cpu_count(), "buffer", round(len(buf) / 16000, 1), "s")
for name in ("small.en", "base.en"):
    for threads in (2, 4):
        m = WhisperModel(whisper_repo(name), device="cpu", compute_type="int8", cpu_threads=threads)
        for wt, vad in ((True, True), (True, False), (False, False)):
            times = []
            for _ in range(3):
                t0 = time.perf_counter()
                segs, _ = m.transcribe(buf, language="en", beam_size=1, word_timestamps=wt, vad_filter=vad,
                                       condition_on_previous_text=False, temperature=0.0)
                text = " ".join(s.text.strip() for s in segs)
                times.append(time.perf_counter() - t0)
            print(f"{name:9} threads={threads} words={wt!s:5} vad={vad!s:5} "
                  f"best {min(times):.2f}s  | {text[:70]}", flush=True)
        del m
