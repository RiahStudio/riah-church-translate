"""Spoken translation — the words on the screen, read aloud into a headphone system.

Captain, 2026-11-26: "give a voice into some speaker systems so people can put on the
headphones or headphone boxes and it'll auto translate in voice."

How the church uses it: a USB interface on the Mac exposes stereo pairs (Output 1+2, 3+4,
...). Each translation language is sent to its own pair, and the church's headphone
transmitter picks each pair up as a channel, so a person puts on their headphones, chooses
their language's channel, and hears the sermon in their own language a few seconds behind.

Nothing leaves the room and nothing needs a subscription: macOS reads each finished line with
its own installed voices, and the sound goes straight to the chosen output. Spoken lines are
FINAL lines only — never drafts — because a draft is half a sentence.

If speech falls behind a fast speaker the OLDEST waiting line is dropped, never the newest:
people hear the latest sentence rather than a queue of stale ones. Every drop is counted and
reported so the operator knows the voice lagged, without anything crashing.

macOS only for now (the app the church runs is the Mac build). On Windows and Linux this
module reports itself as unavailable, and the app hides the panel rather than pretending.
"""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import time
import wave
from io import BytesIO

# App language -> the locale prefixes macOS voices use for it. First match wins; a language
# with no installed voice is reported as unavailable (the UI then says so in plain words).
LOCALES = {
    "en": ("en_US", "en_GB", "en_AU", "en_"),
    "th": ("th_TH", "th_"),
    "zh": ("zh_CN", "zh_"),
    "zh-TW": ("zh_TW", "zh_HK"),
    "my": ("my_MM",),
    "shn": (),
    "lo": ("lo_LA",),
    "km": ("km_KH",),
    "vi": ("vi_VN", "vi_"),
    "ko": ("ko_KR", "ko_"),
    "ja": ("ja_JP", "ja_"),
    "id": ("id_ID", "id_"),
    "tl": ("fil_PH", "tl_PH"),
    "hi": ("hi_IN", "hi_"),
    "es": ("es_ES", "es_MX", "es_US", "es_"),
    "fr": ("fr_FR", "fr_CA", "fr_"),
    "de": ("de_DE", "de_"),
    "pt": ("pt_BR", "pt_PT", "pt_"),
    "ru": ("ru_RU", "ru_"),
}

RATE = 190            # words per minute; slightly brisk reads keep up with a pulpit pace
MAX_WAITING = 2       # lines allowed to wait per language before the oldest is dropped


def available() -> bool:
    return sys.platform == "darwin"


def list_voices() -> dict:
    """{app_language: [voice names installed on this Mac]}, best first. Empty list = no voice."""
    out = {k: [] for k in LOCALES}
    if not available():
        return out
    try:
        raw = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return out
    found = []          # (locale, name)
    for line in raw.splitlines():
        parts = line.split()
        for i, p in enumerate(parts):
            if len(p) == 5 and p[2] == "_" and p[:2].isalpha():   # th_TH
                if i:
                    found.append((p, parts[i - 1]))
                break
    for lang, prefixes in LOCALES.items():
        names = []
        for pref in prefixes:
            names += [n for loc, n in found if loc == pref and n not in names]
        out[lang] = names
    return out


def pick_voice(lang: str, voices: dict | None = None) -> str | None:
    v = (voices or list_voices()).get(lang) or []
    return v[0] if v else None


def list_outputs() -> list[dict]:
    """[{id, name, channels, default}] — the outputs a church can point a language at."""
    if not available():
        return []
    try:
        import sounddevice as sd
        rows = []
        default_out = sd.default.device[1] if sd.default.device else None
        for i, d in enumerate(sd.query_devices()):
            ch = int(d.get("max_output_channels") or 0)
            if ch:
                rows.append(dict(id=i, name=d.get("name") or f"output {i}", channels=ch,
                                 default=(i == default_out), hostapi=d.get("hostapi")))
        return rows
    except Exception:
        return []


def _offer(q, text: str, cap: int) -> bool:
    """Put a line in a language's queue, dropping the OLDEST waiting line when the queue is
    full (never the newest — a person needs the latest sentence, not a backlog). Returns True
    when a waiting line had to go."""
    dropped = False
    while q.qsize() >= cap:
        try:
            q.get_nowait()
            dropped = True
        except queue.Empty:
            break
    q.put(text)
    return dropped


class VoiceOut:
    """Reads finished lines aloud, one queue per language, one output stream per device.

    say(lang, text) is called from the translation worker and never blocks it: the line goes
    into a small queue and a speaker thread reads it when it gets there.
    """

    def __init__(self, plan: dict, on_note=None):
        """plan: {lang: {"voice": str|None, "device": int|None, "on": bool}}."""
        self.on_note = on_note or (lambda text: None)
        self.plan = {k: v for k, v in plan.items() if v.get("on") and v.get("device") is not None}
        self.qs = {lang: queue.Queue() for lang in self.plan}
        self.voices = list_voices()
        self.dropped = 0
        self.spoken = 0
        self.streams = {}
        self.closed = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        if self.plan:
            self.thread.start()

    # ------------------------------------------------------------------ from the translator
    def say(self, lang: str, text: str):
        q = self.qs.get(lang)
        if q is None or not text:
            return
        if _offer(q, text, MAX_WAITING):
            self.dropped += 1

    def note(self) -> str:
        if not self.plan:
            return ""
        s = f"Spoken in {len(self.plan)} language{'s' if len(self.plan) != 1 else ''}"
        if self.dropped:
            s += f", {self.dropped} line{'s' if self.dropped != 1 else ''} skipped to keep up"
        return s + "."

    def close(self, timeout=10):
        self.closed = True
        for q in self.qs.values():
            try:
                q.put_nowait(None)           # wake the speaker thread
            except queue.Full:
                pass
        self.thread.join(timeout)
        for st in self.streams.values():
            try:
                st.stop()
                st.close()
            except Exception:
                pass
        self.streams = {}

    # ------------------------------------------------------------------ speaker thread
    def _run(self):
        import numpy as np
        while not self.closed:
            did = False
            for lang, q in self.qs.items():
                try:
                    text = q.get(timeout=0.2 if not did else 0)
                except queue.Empty:
                    continue
                if text is None:
                    return
                did = True
                try:
                    self._speak(lang, text, np)
                    self.spoken += 1
                except Exception as e:        # a voice problem must never stop the subtitles
                    self.on_note(f"The voice for {lang} had a problem: {str(e)[:80]}")
            if not did:
                time.sleep(0.05)

    def _speak(self, lang: str, text: str, np):
        cfg = self.plan[lang]
        voice = cfg.get("voice") or pick_voice(lang, self.voices)
        if not voice:
            self.on_note(f"No {lang} voice is installed on this Mac, so that line stayed silent.")
            return
        proc = subprocess.run(
            ["say", "-v", voice, "-r", str(cfg.get("rate") or RATE), "-o", "-",
             "--file-format=WAVE", "--data-format=LEI16@22050", "--", text],
            capture_output=True, timeout=120)
        if proc.returncode != 0 or not proc.stdout:
            raise RuntimeError((proc.stderr or b"").decode("utf-8", "replace").strip()[:120] or "say failed")
        with wave.open(BytesIO(proc.stdout), "rb") as w:
            rate, ch = w.getframerate(), w.getnchannels()
            frames = w.readframes(w.getnframes())
        data = np.frombuffer(frames, dtype=np.int16)
        if ch > 1:
            data = data.reshape(-1, ch)
        dev = cfg["device"]
        st = self.streams.get(dev)
        if st is None:
            import sounddevice as sd
            info = sd.query_devices(dev)
            chans = min(2, max(1, int(info.get("max_output_channels") or 1)))
            st = sd.OutputStream(device=dev, channels=chans, samplerate=rate, dtype="int16")
            st.start()
            self.streams[dev] = st
        out = data if data.ndim == 2 else data.reshape(-1, 1)
        # Pad/trim the channel count to what the device opened with.
        if out.shape[1] != st.channels:
            want = np.zeros((out.shape[0], st.channels), dtype=np.int16)
            want[:, :min(st.channels, out.shape[1])] = out[:, :min(st.channels, out.shape[1])]
            out = want
        st.write(np.ascontiguousarray(out))


def test_voice(lang: str, voice: str | None, device: int | None, text: str | None = None) -> dict:
    """Read one sentence out loud now (the panel's Test button). Returns {ok} or {error}."""
    if not available():
        return dict(ok=False, error="The spoken voice needs the Mac app for now.")
    if device is None:
        devs = list_outputs()
        device = next((d["id"] for d in devs if d["default"]), devs[0]["id"] if devs else None)
    if device is None:
        return dict(ok=False, error="No sound output was found to speak into.")
    sample = text or {
        "th": "ทดสอบเสียงแปล",
        "zh": "这是翻译声音的测试。",
        "zh-TW": "這是翻譯聲音的測試。",
        "ko": "번역 음성 테스트입니다.",
        "ja": "翻訳音声のテストです。",
        "vi": "Đây là bài kiểm tra giọng dịch.",
        "es": "Esta es una prueba de la voz de traducción.",
        "fr": "Ceci est un test de la voix de traduction.",
        "de": "Dies ist ein Test der Übersetzungsstimme.",
        "pt": "Este é um teste da voz de tradução.",
        "ru": "Это проверка голоса перевода.",
        "hi": "यह अनुवाद आवाज़ का परीक्षण है।",
        "id": "Ini adalah uji suara terjemahan.",
        "ko": "번역 음성 테스트입니다.",
    }.get(lang, "This is the translation voice. When the preacher speaks, you hear it here.")
    v = VoiceOut({lang: dict(on=True, device=device, voice=voice)})
    try:
        v.say(lang, sample)
        deadline = time.time() + 30
        while v.spoken == 0 and time.time() < deadline and v.thread.is_alive():
            time.sleep(0.05)
        return dict(ok=v.spoken > 0, error=None if v.spoken else "Nothing came out of that output.")
    finally:
        v.close(5)
