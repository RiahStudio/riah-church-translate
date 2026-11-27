"""A plain-text record of every service: what was said and each translation, with the time.

One file per Start, in the app's data folder under "Transcripts". Written as lines arrive, so a
crash or power cut keeps everything up to then. Useful afterwards: a Thai or Chinese speaker reads
it, finds the lines that came out wrong, and those words go into the church words list.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from .langs import LANGS, lang


class Transcript:
    def __init__(self, folder: Path):
        self.folder = folder
        self.lock = threading.Lock()
        self.f = None

    def begin(self, src: str, tgts: list[str]):
        with self.lock:
            self._close()
            self.folder.mkdir(parents=True, exist_ok=True)
            path = self.folder / time.strftime("%Y-%m-%d %H-%M-%S.txt")
            self.f = open(path, "a", encoding="utf-8", buffering=1)
            names = ", ".join(lang(t)["name"] for t in tgts) or "no translation"
            self.f.write(f"Riah Church Translate, {time.strftime('%A %d %B %Y, %H:%M')}\n"
                         f"Spoken: {lang(src)['name']}. Translated into: {names}.\n\n")
        return path

    def event(self, ev: dict):
        """Hub tap: called with every published event."""
        kind = ev.get("type")
        if kind not in ("line", "translation") or self.f is None:
            return
        stamp = time.strftime("%H:%M:%S", time.localtime(ev.get("t") or time.time()))
        with self.lock:
            if self.f is None:
                return
            if kind == "line":
                self.f.write(f"\n[{stamp}] {ev.get('src', '')}\n")
            else:
                code = ev.get("lang", "")
                name = LANGS.get(code, {}).get("name", code)
                self.f.write(f"           {name}: {ev.get('dst', '')}\n")

    def _close(self):
        if self.f is not None:
            try:
                self.f.close()
            except OSError:
                pass
            self.f = None

    def close(self):
        with self.lock:
            self._close()
