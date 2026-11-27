"""Crash reports, off unless someone switches them on for this computer (Captain, 2026-11-26/27).

The church's Mac belongs to the church: nobody from the studio can log in to it, ever. So when
"Send crash reports" is ticked (More settings, off by default, saved per computer) the app sends
out a few small facts instead, and the studio's watcher reads them:

    start       the app opened (and whether the last run stopped suddenly during a service)
    heartbeat   about every 10 minutes while the app is open, so "it got back up" can be seen
    bad_pass    one speech pass failed and the service kept going
    restart     the service broke and is restarting itself
    crash       an error nothing caught

What a report holds, and nothing else: app version, platform, a random id made for this install,
the kind, the error's type name, the stack as file:line (never a folder), whether it carried on,
whether a service is listening, and the time. Never sound, words, notes, error messages, folder
paths, settings or anything typed.

Sending can never touch the service: a small queue, one background thread, a 5-second timeout,
and anything that goes wrong is dropped.
"""
from __future__ import annotations

import atexit
import json
import os
import platform as _platform_mod
import queue
import re
import secrets
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import __version__

URL = os.environ.get("RIAH_TRANSLATE_REPORT_URL") or ""
# Write-only, like an error tracker's key: all it can do is add small, size-capped, rate-limited
# rows. Reading them takes a different key that stays with the studio. Kept out of the source anyway.
KEY_FILE = Path(__file__).with_name("report-key.txt")   # written by the build; never in the source


def _ingest_key() -> str:
    """The write-only key: RIAH_TRANSLATE_REPORT_KEY, else report-key.txt (the Mac and Windows builds
    write it from the GitHub secret of the same name). Blank in a plain checkout: reports stay off."""
    key = os.environ.get("RIAH_TRANSLATE_REPORT_KEY", "").strip()
    if not key:
        try:
            key = KEY_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            key = ""
    return key if re.fullmatch(r"[A-Za-z0-9_.~-]{16,200}", key) else ""


INGEST_KEY = _ingest_key()

HEARTBEAT_SEC = 600          # one "still here" every 10 minutes while the app is open
TIMEOUT = 5                  # seconds; a report that can't go out in time is dropped
QUEUE_MAX = 20
SAME_EVERY_SEC = 600         # the same failure goes out at most once per 10 minutes, with a count
MAX_PER_HOUR = 30            # failures sent per hour, at most (heartbeats aside)
MAX_FRAMES = 15
FILE = "crash-report.json"   # in the app's data folder: this install's random id and "open now"

_ID_CHARS = re.compile(r"[^A-Za-z0-9_.]")
_WHERE_CHARS = re.compile(r"[^A-Za-z0-9_.\-/]")
_STOP = object()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def platform_name() -> str:
    """e.g. 'darwin 14.5 arm64' or 'win32 10.0.19045 AMD64'."""
    try:
        if sys.platform == "darwin":
            ver = _platform_mod.mac_ver()[0]
        elif os.name == "nt":
            ver = _platform_mod.version()
        else:
            ver = _platform_mod.release()
        text = f"{sys.platform} {ver} {_platform_mod.machine()}"
    except Exception:
        text = sys.platform
    return re.sub(r"[^A-Za-z0-9_.\- ]", "", text)[:60]


def exc_name(e: BaseException) -> str:
    """The error's type, e.g. 'RuntimeError' or 'av.error.InvalidDataError'. Never its message."""
    t = type(e)
    mod = getattr(t, "__module__", "") or ""
    name = getattr(t, "__qualname__", t.__name__)
    full = name if mod in ("builtins", "__main__") else f"{mod}.{name}"
    return _ID_CHARS.sub("", full)[:100] or "Exception"


def where(filename: str) -> str:
    """A stack frame's file without any folder that could say whose computer it is:
    'riah_translate/asr.py', 'faster_whisper/vad.py', 'threading.py'."""
    name = str(filename or "")
    if name.startswith("<"):
        return _WHERE_CHARS.sub("", name.strip("<>").replace(" ", "-"))[:80] or "?"
    parts = [p for p in re.split(r"[\\/]+", name) if p and p != "."]
    if not parts:
        return "?"
    keep = None
    if "riah_translate" in parts:
        keep = parts[len(parts) - 1 - parts[::-1].index("riah_translate"):]
    else:
        for marker in ("site-packages", "dist-packages"):
            if marker in parts:
                rest = parts[len(parts) - parts[::-1].index(marker):]
                keep = [rest[0], rest[-1]] if len(rest) > 1 else rest
                break
    if keep is None:
        absolute = name.startswith(("/", "\\")) or bool(re.match(r"^[A-Za-z]:", name))
        # A frozen app names library files from their package ("faster_whisper/vad.py"); a full
        # path on a disk only keeps the file's own name.
        keep = [parts[0], parts[-1]] if (not absolute and len(parts) > 1) else [parts[-1]]
    return _WHERE_CHARS.sub("", "/".join(keep[-3:]))[:80] or "?"   # the studio keeps 3 parts at most


def frames(tb) -> list[str]:
    import traceback
    try:
        stack = traceback.extract_tb(tb)
    except Exception:
        return []
    return [f"{where(f.filename)}:{int(f.lineno or 0)}" for f in stack][-MAX_FRAMES:]


def _ssl_context():
    import ssl
    try:
        import certifi      # comes with the model downloader; a frozen Mac app has no system CA path
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def post(url: str, key: str, payload: dict, timeout: float = TIMEOUT) -> bool:
    """One report out. True if the studio took it; never raises."""
    try:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "User-Agent": f"RiahTranslate/{__version__}",
        })
        ctx = _ssl_context() if url.lower().startswith("https:") else None
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            r.read(512)
            return 200 <= r.status < 300
    except Exception:
        return False


class Reporter:
    def __init__(self, url: str = URL, key: str = INGEST_KEY, send=None, clock=time.monotonic):
        self.url, self.key = url, key
        self.send = send or (lambda payload: post(self.url, self.key, payload))
        self.clock = clock
        self.on = False
        self.folder: Path | None = None
        self.install = ""
        self.live = False
        self._lock = threading.Lock()
        self._q: queue.Queue = queue.Queue(QUEUE_MAX)
        self._thread = None
        self._next_hb = 0.0
        self._error_since_hb = False
        self._last = {}                # failure -> (when last sent, how many held back since)
        self._sent_times = []          # when failures went out, for the hourly cap
        self._hooks = False
        self._started = False

    # ------------------------------------------------------------------ switching
    def switch(self, on: bool, folder: Path | None = None):
        """On or off for this computer. Turning it on (at app start, or ticked mid-run) sends 'start'."""
        try:
            with self._lock:
                if folder is not None:
                    self.folder = Path(folder)
                was, self.on = self.on, bool(on) and bool(self.key)   # no key in this build: stays off
                if not self.on:
                    if was:
                        self._write_marker(None)
                    return
                if was:
                    return
                prev = self._read_marker()
                self._write_marker(dict(live=self.live))
                self._install_hooks()
                self._start_thread()
                p = self.payload("start")
                if prev:
                    p["prev"] = prev
                self._put(p)
        except Exception:
            pass

    def closing(self):
        """The app is quitting normally: next time it opens, this run doesn't count as 'stopped suddenly'."""
        try:
            if self.on:
                self._write_marker(None)
        except Exception:
            pass

    # ------------------------------------------------------------------ what the app tells us
    def status(self, state: str):
        """The service's state. 'listening' after a failure sends a heartbeat at once: it's back."""
        try:
            live = state == "listening"
            if live == self.live:
                return
            self.live = live
            if not self.on:
                return
            self._put(("mark",))
            if live and self._error_since_hb:
                self._put(self.payload("heartbeat"))
        except Exception:
            pass

    def event(self, kind: str, exc: BaseException | None = None, recovered: bool | None = None):
        """A failure: bad_pass (recovered=True: the service kept going), restart, crash."""
        if not self.on:
            return
        try:
            p = self.payload(kind, exc, recovered)
            sig = (kind, p.get("exc"), tuple(p.get("frames") or ()))
            now = self.clock()
            with self._lock:
                last, held = self._last.get(sig, (None, 0))
                if last is not None and now - last < SAME_EVERY_SEC:
                    self._last[sig] = (last, held + 1)
                    return
                self._sent_times = [t for t in self._sent_times if now - t < 3600]
                if len(self._sent_times) >= MAX_PER_HOUR:
                    return
                self._sent_times.append(now)
                self._last[sig] = (now, 0)
                self._error_since_hb = True
            p["n"] = held + 1
            self._put(p)
        except Exception:
            pass

    # ------------------------------------------------------------------ building a report
    def payload(self, kind: str, exc: BaseException | None = None, recovered: bool | None = None) -> dict:
        p = dict(v=1, kind=kind, app=__version__, platform=platform_name(), install=self.install,
                 live=bool(self.live), at=now_iso())
        if exc is not None:
            p["exc"] = exc_name(exc)
            p["frames"] = frames(exc.__traceback__)
        if recovered is not None:
            p["recovered"] = bool(recovered)
        return p

    # ------------------------------------------------------------------ inside
    def _put(self, item):
        try:
            self._q.put_nowait(item)
        except queue.Full:
            pass

    def _start_thread(self):
        if self._thread is None or not self._thread.is_alive():
            self._next_hb = self.clock() + HEARTBEAT_SEC
            self._thread = threading.Thread(target=self._loop, name="crash-reports", daemon=True)
            self._thread.start()

    def _loop(self):
        while True:
            try:
                wait = max(0.05, self._next_hb - self.clock())
                try:
                    item = self._q.get(timeout=wait)
                except queue.Empty:
                    item = None
                if item is _STOP:
                    return
                if not self.on:
                    self._next_hb = self.clock() + HEARTBEAT_SEC
                    continue
                if isinstance(item, tuple) and item and item[0] == "mark":
                    self._write_marker(dict(live=self.live))
                elif isinstance(item, dict):
                    if item.get("kind") == "heartbeat":
                        self._error_since_hb = False
                        self._next_hb = self.clock() + HEARTBEAT_SEC
                    self.send(item)
                if self.clock() >= self._next_hb:
                    self._error_since_hb = False
                    self._next_hb = self.clock() + HEARTBEAT_SEC
                    self.send(self.payload("heartbeat"))
            except Exception:
                time.sleep(1)

    def stop_thread(self, timeout=2):
        """Tests only: let the sender go."""
        t = self._thread
        if t is not None and t.is_alive():
            try:
                self._q.put(_STOP, timeout=1)
            except queue.Full:
                pass
            t.join(timeout)
        self._thread = None

    def _path(self) -> Path | None:
        return self.folder / FILE if self.folder is not None else None

    def _read_marker(self):
        """Loads this install's id (made on first use) and says how the last run ended:
        'lost' = it stopped without quitting while a service was listening."""
        p = self._path()
        data = {}
        if p is not None:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        ident = data.get("install") if isinstance(data, dict) else None
        self.install = ident if isinstance(ident, str) and re.fullmatch(r"[a-f0-9]{12}", ident) else secrets.token_hex(6)
        was_open = data.get("open") if isinstance(data, dict) else None
        if isinstance(was_open, dict):
            return "lost" if was_open.get("live") else "closed"
        return None

    def _write_marker(self, open_now):
        p = self._path()
        if p is None or not self.install:
            return
        try:
            data = dict(install=self.install)
            if open_now is not None:
                data["open"] = open_now
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, p)
        except Exception:
            pass

    def _install_hooks(self):
        """Errors nothing caught. In the main thread the app is ending, so that one is sent right away."""
        if self._hooks:
            return
        self._hooks = True
        old_sys, old_thread = sys.excepthook, threading.excepthook

        def on_main(t, e, tb):
            try:
                if self.on and e is not None and not isinstance(e, (KeyboardInterrupt, SystemExit)):
                    p = self.payload("crash", e, recovered=False)
                    p["n"] = 1
                    self.send(p)
            except Exception:
                pass
            old_sys(t, e, tb)

        def on_thread(args):
            try:
                if args.exc_value is not None and not isinstance(args.exc_value, SystemExit):
                    self.event("crash", args.exc_value, recovered=False)
            except Exception:
                pass
            old_thread(args)

        sys.excepthook = on_main
        threading.excepthook = on_thread
        atexit.register(self.closing)


# One per app. The engine and the app call these; with reports off they do nothing.
_reporter = Reporter()


def switch(on, folder=None):
    _reporter.switch(on, folder)


def status(state):
    _reporter.status(state)


def event(kind, exc=None, recovered=None):
    _reporter.event(kind, exc, recovered)


def closing():
    _reporter.closing()
