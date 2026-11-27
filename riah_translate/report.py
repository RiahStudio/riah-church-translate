"""Crash reports, off unless someone switches them on for this computer (Captain, 2026-11-26/27).

The church's Mac belongs to the church: nobody from the studio can log in to it, ever. So when
"Send crash reports" is ticked (More settings, off by default, saved per computer) the app sends
a few small facts out, and only when something breaks. Nothing runs on a timer and a normal day
sends nothing at all (Captain, 2026-11-27: "Why are we running something every ten minutes?"):

    bad_pass    one speech pass failed and the service kept going
    restart     the service broke and restarted itself; held until it is listening again
                (back=true), or WAIT_BACK_SEC (back=false, then a 'back' report when it is)
    lost        the last run stopped suddenly during a service (nothing could catch it);
                held the same way once the app is open again
    crash       an error nothing caught
    back        listening again after a report that said it wasn't

What a report holds, and nothing else: app version, platform, a random id made for this install,
the kind, the error's type name, the stack as file:line (never a folder), whether it carried on or
came back (and after how many seconds), whether a service is listening, and the time. Never sound,
words, notes, error messages, folder paths, settings or anything typed.

A problem report is the person's own (Captain, 2026-11-27: reports are something the user sends).
With the tick off, the app still keeps a short list of what broke on this computer
(problems.json in its data folder: the same few facts, the last 30 days, 20 at most) and sends none
of it. "Send a problem report" (More settings) shows that list in plain words, and one press sends
it. When the send doesn't go through, the same text is saved as a file (Downloads) for the person
to email or message to the studio.

No key, and nothing to set up (Captain, 2026-11-27): every build can send. A person's report goes to
the studio's own form on riahstudio.com (/api/talk) as its plain text, size-capped and rate-limited.
The studio files it and nobody is emailed or messaged; the studio fixes what comes in and releases a
new dated version (Captain, 2026-11-27/28). The tick-box that sent reports on its own is gone
(Captain: reports are something the user sends); the code for it stays dormant, with no address.

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

URL = os.environ.get("RIAH_TRANSLATE_REPORT_URL") or ""   # the dormant tick-box sender: no address, nothing goes
# A person's problem report goes to the studio's own form on riahstudio.com. No key, and nobody gets a
# message: it is filed, and the studio fixes it and releases a new dated version (Captain, 2026-11-27/28).
TALK_URL = os.environ.get("RIAH_TRANSLATE_TALK_URL") or "https://riahstudio.com/api/talk"
TALK_SHOW = "Riah Church Translate app"   # how the site knows to file it rather than email it
TALK_MAX = 1800              # the form keeps 2000 characters

WAIT_BACK_SEC = 120          # a restart waits this long to say "and it's back up" in the same report
TIMEOUT = 5                  # seconds; a report that can't go out in time is dropped
QUEUE_MAX = 20
SAME_EVERY_SEC = 600         # the same failure goes out at most once per 10 minutes, with a count
MAX_PER_HOUR = 30            # failures sent per hour, at most
MAX_HELD = 5                 # different failures waiting for "back up" at once
MAX_AT_QUIT = 2              # waiting reports sent while the app quits (each can take TIMEOUT)
MAX_FRAMES = 15
FILE = "crash-report.json"   # in the app's data folder: this install's random id, "open now", "down"
HELD_KINDS = ("restart", "lost")   # the service is down: these wait to say whether it came back

LOG_FILE = "problems.json"   # in the app's data folder: what broke lately, for a person's report
MAX_LOG = 20                 # problems kept
LOG_DAYS = 30
SAVE_EVERY_SEC = 10          # a count going up is saved at most this often (a new problem at once)
IN_REPORT = 8                # problems in a person's report, newest first
REPORT_FRAMES = 6            # file:line places per problem in a person's report (where it broke)
REPORT_MAX_BYTES = 3800      # the studio takes 4 KB
USER_TIMEOUT = 10            # a person is waiting on this one
USER_AGAIN_SEC = 20          # a second press soon after a send that went: not sent twice
LOG_KEYS = ("kind", "at", "last", "n", "exc", "frames", "recovered", "back", "secs", "closed")
_ISO = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")

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


def post(url: str, payload: dict, timeout: float = TIMEOUT, want_ok: bool = False) -> bool:
    """One report out. True if the studio took it; never raises. With want_ok, only the studio's own
    {"ok": true} counts, so a sign-in page or any other 2xx on the way is never taken for "sent"."""
    if not url:
        return False
    try:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "User-Agent": f"RiahTranslate/{__version__}",
        })
        ctx = _ssl_context() if url.lower().startswith("https:") else None
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            data = r.read(512)
            if not 200 <= r.status < 300:
                return False
            if not want_ok:
                return True
            got = json.loads(data.decode("utf-8") or "null")
            return isinstance(got, dict) and got.get("ok") is True
    except Exception:
        return False


_FRAME = re.compile(r"[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+){0,2}:\d{1,6}")
_LOG_KINDS = ("bad_pass", "restart", "lost", "crash")


def _iso_seconds(iso: str) -> float:
    try:
        return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return 0.0


def clean_problem(e) -> dict | None:
    """One problem from problems.json, rebuilt from the fields we keep, each checked."""
    if not isinstance(e, dict) or e.get("kind") not in _LOG_KINDS:
        return None
    if not isinstance(e.get("at"), str) or not _ISO.fullmatch(e["at"]):
        return None
    out = dict(kind=e["kind"], at=e["at"])
    if isinstance(e.get("last"), str) and _ISO.fullmatch(e["last"]):
        out["last"] = e["last"]
    n = e.get("n")
    out["n"] = min(n, 100000) if isinstance(n, int) and not isinstance(n, bool) and n >= 1 else 1
    if isinstance(e.get("exc"), str) and re.fullmatch(r"[A-Za-z0-9_.]{1,100}", e["exc"]):
        out["exc"] = e["exc"]
    if isinstance(e.get("frames"), list):
        out["frames"] = [f for f in e["frames"] if isinstance(f, str) and len(f) <= 90 and _FRAME.fullmatch(f)][-MAX_FRAMES:]
    for k in ("recovered", "back", "closed"):
        if isinstance(e.get(k), bool):
            out[k] = e[k]
    s = e.get("secs")
    if isinstance(s, int) and not isinstance(s, bool) and 0 <= s <= 86400:
        out["secs"] = s
    return out


def _when(iso: str, day=True) -> str:
    """A report's time as this computer's clock shows it: 'Thu 27 Nov 2026, 10:42'."""
    try:
        t = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone()
    except Exception:
        return "?"
    return t.strftime("%a %d %b %Y, %H:%M") if day else t.strftime("%H:%M")


def _took(secs) -> str:
    if not isinstance(secs, int):
        return ""
    if secs < 90:
        return f" after {secs} s"
    if secs < 5400:
        return f" after {round(secs / 60)} min"
    return f" after {round(secs / 3600)} h"


def _what(e: dict) -> str:
    kind = e["kind"]
    if kind == "bad_pass":
        return "One speech pass failed. The service kept going."
    if kind == "crash":
        if e.get("closed"):
            return "The app hit an error nothing caught, and closed."
        return "Part of the app hit an error nothing caught."
    if kind == "lost":
        how = "The app stopped suddenly during a service (around this time)."
        if e.get("back") is True:
            return how + " It was opened again and listening."
        return how + " It wasn't listening again before it closed." if e.get("closed") else how
    how = "The service broke and restarted itself."
    if e.get("back") is True:
        return how + f" Back up and listening{_took(e.get('secs'))}."
    if e.get("closed") or e.get("back") is False:
        return how + " It wasn't listening again before the app closed."
    return how + " Not listening again yet."


def problem_text(p: dict) -> str:
    """A person's problem report in plain words: exactly what the studio gets, nothing more."""
    out = ["Riah Church Translate problem report", "",
           f"Made: {_when(p.get('at', ''))} (this computer's clock)",
           f"App: {p.get('app', '?')} on {p.get('platform', '?')}",
           f"This computer's report id: {p.get('install', '?')}",
           f"Listening when this was made: {'yes' if p.get('live') else 'no'}", ""]
    items = p.get("problems") or []
    if not items:
        out.append(f"Nothing has broken that the app noticed in the last {LOG_DAYS} days.")
    else:
        out.append("What broke, newest first:")
        for e in items:
            when = _when(e["at"])
            n = int(e.get("n", 1))
            if n > 1:
                last = ""
                if e.get("last"):
                    full = _when(e["last"])
                    same_day = full.rsplit(", ", 1)[0] == when.rsplit(", ", 1)[0]
                    last = f", last at {_when(e['last'], day=False) if same_day else full}"
                when += f" ({n} times{last})"
            out += ["", when, _what(e)]
            if e.get("exc") or e.get("frames"):
                where = " > ".join(e.get("frames") or [])
                out.append(f"Error: {e.get('exc', '?')}" + (f". Where: {where}" if where else ""))
    out += ["", "That's everything in this report. No sound, no words from the service, no notes,",
            "no folders and nothing typed."]
    return "\n".join(out)


def report_folder(app_dir: Path | None = None) -> Path:
    """Where a report that couldn't be sent is saved: Downloads, else the Desktop, else the app's folder."""
    home = Path.home()
    for c in (home / "Downloads", home / "Desktop"):
        if c.is_dir():
            return c
    return (app_dir or home) / "Problem reports"


def save_text(folder: Path, text: str, when: datetime) -> Path | None:
    """The report as a small text file with a name nobody else's file has. None if it can't be written."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        base = f"Riah Translate problem report {when.strftime('%Y-%m-%d %H%M')}"
        for i in range(1, 50):
            path = folder / (base + (f" ({i})" if i > 1 else "") + ".txt")
            try:
                with open(path, "x", encoding="utf-8") as f:     # never over another file
                    f.write(text + "\n")
                return path
            except FileExistsError:
                continue
    except Exception:
        pass
    return None


class Reporter:
    def __init__(self, url: str = URL, send=None, clock=time.monotonic, send_user=None, talk_url: str | None = None):
        self.url = url
        self.send = send or (lambda payload: post(self.url, payload))
        # A person's report, as the plain text they were shown: True only if the studio filed it
        # (the form's own {"ok": true}).
        self.talk_url = talk_url or TALK_URL
        self.send_user = send_user or (lambda payload: post(
            self.talk_url, {"message": problem_text(payload), "show": TALK_SHOW, "website": ""},
            timeout=USER_TIMEOUT, want_ok=True))
        self.clock = clock
        self.on = False
        self.folder: Path | None = None
        self.install = ""
        self.live = False
        self._lock = threading.RLock()
        self._opened = False           # this run's first switch: id, last run, the list of problems
        self._prev = None              # how the last run ended, until reports are switched on
        self._marker_at = None         # when the last run last wrote its marker (a sudden stop: about then)
        self._log = []                 # what broke lately, oldest first (problems.json)
        self._log_wait = []            # [problem, when it went down]: waiting to hear it's listening again
        self._log_saved = None         # when the list was last saved (None: not since a change)
        self._log_dirty = False        # a count went up since the last save
        self._user_sent = None         # when a person's report last went through
        self._send_lock = threading.Lock()
        self._last_file = None         # the last problem report saved as a file
        self._q: queue.Queue = queue.Queue(QUEUE_MAX)
        self._thread = None
        self._last = {}                # failure -> (when last sent, how many held back since)
        self._sent_times = []          # when failures went out, for the hourly cap
        self._held = []                # [due, report]: down, waiting to say whether it came back
        self._down_since = None        # when the service went down (None: fine, or not known)
        self._owed_back = False        # a report said "not back up yet": a 'back' follows
        self._hooks = False

    # ------------------------------------------------------------------ switching
    def switch(self, on: bool, folder: Path | None = None):
        """On or off for this computer (at app start, or ticked mid-run). Turning it on sends nothing,
        unless the last run stopped suddenly during a service ('lost'). On or off, the first call
        loads this computer's list of problems, for a person's report."""
        try:
            with self._lock:
                if folder is not None:
                    self.folder = Path(folder)
                if not self._opened and self.folder is not None:
                    self._opened = True
                    self._prev = self._read_marker()
                    self._load_log()
                    if self._prev == "lost":
                        self._note(self.payload("lost"), at=self._marker_at)
                    self._install_hooks()
                was, self.on = self.on, bool(on)
                if not self.on:
                    if was:
                        self._held, self._owed_back, self._down_since = [], False, None
                    self._write_marker(self._open_now())
                    return
                if was:
                    return
                prev, self._prev = self._prev, None
                if prev == "down":                  # quit while down last time: say when it's back
                    self._owed_back = True
                self._start_thread()
                if prev == "lost":
                    self._hold(self.payload("lost"))
                    self._put(("mark",))            # wakes the sender: the wait for "back" starts now
                self._write_marker(self._open_now())
        except Exception:
            pass

    def closing(self):
        """The app is quitting normally. Reports still waiting go now, marked closed; if the service
        was down, the next run says when it is back."""
        try:
            with self._lock:
                for e, _ in self._log_wait:
                    e.update(back=False, closed=True)
                self._log_wait = []
                self._save_log()
                held, self._held = (self._held, []) if self.on else ([], self._held)
                if held:
                    self._owed_back = True
                down = self._owed_back and self.on
            for _, p in held[:MAX_AT_QUIT]:
                p.update(back=False, closed=True)
                self.send(p)
            self._write_marker(None, down=down)
        except Exception:
            pass

    # ------------------------------------------------------------------ what the app tells us
    def status(self, state: str):
        """The service's state. 'listening' after a restart or a lost run: it's back."""
        try:
            live = state == "listening"
            if state == "stopped" and self._log_wait:
                # Someone pressed Stop: the list no longer waits to hear it's back, so a Start
                # hours later isn't counted as a long time down.
                with self._lock:
                    self._log_wait = []
                    self._save_log()
            if live == self.live:
                return
            self.live = live
            if live and self._log_wait:
                with self._lock:
                    now = self.clock()
                    for e, since in self._log_wait:
                        e["back"] = True
                        if since is not None:
                            e["secs"] = int(min(86400, max(0, now - since)))
                    self._log_wait = []
                    self._save_log()
            if not self.on:
                self._write_marker(self._open_now())     # still kept, for a sudden stop
                return
            back = []
            if live:
                with self._lock:
                    secs = self._secs()
                    for _, p in self._held:
                        p.update(back=True, live=True)
                        if secs is not None:
                            p["secs"] = secs
                        back.append(p)
                    if self._owed_back and not back:
                        p = self.payload("back")
                        if secs is not None:
                            p["secs"] = secs
                        back.append(p)
                    self._held, self._owed_back, self._down_since = [], False, None
            self._put(("mark",))
            for p in back:
                self._put(p)
        except Exception:
            pass

    def event(self, kind: str, exc: BaseException | None = None, recovered: bool | None = None, parts=None):
        """A failure: bad_pass (recovered=True: the service kept going), restart, crash. Always added to
        this computer's own list; sent only when reports are on. `parts` is (error type, frames) for a
        failure that happened in the engine's own process, where there is no exception object here."""
        try:
            p = self.payload(kind, exc, recovered, parts)
            self._note(p)
        except Exception:
            return
        if not self.on:
            return
        try:
            sig = (kind, p.get("exc"), tuple(p.get("frames") or ()))
            now = self.clock()
            with self._lock:
                if kind in HELD_KINDS:
                    for _, waiting in self._held:          # the same one again while still down
                        if (waiting["kind"], waiting.get("exc"), tuple(waiting.get("frames") or ())) == sig:
                            waiting["n"] = waiting.get("n", 1) + 1
                            return
                    if not self._room(now):
                        self._down_since = self._down_since or now
                        return
                    self._hold(p)
                else:
                    last, held = self._last.get(sig, (None, 0))
                    if last is not None and now - last < SAME_EVERY_SEC:
                        self._last[sig] = (last, held + 1)
                        return
                    if not self._room(now):
                        return
                    self._last[sig] = (now, 0)
                    p["n"] = held + 1
            self._put(("mark",) if kind in HELD_KINDS else p)
        except Exception:
            pass

    # ------------------------------------------------------------------ building a report
    def payload(self, kind: str, exc: BaseException | None = None, recovered: bool | None = None,
                parts=None) -> dict:
        p = dict(v=1, kind=kind, app=__version__, platform=platform_name(), install=self.install,
                 live=bool(self.live), at=now_iso())
        if exc is not None:
            p["exc"] = exc_name(exc)
            p["frames"] = frames(exc.__traceback__)
        elif parts:
            name, where_ = parts
            if isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_.]{1,100}", name):
                p["exc"] = name
            p["frames"] = [f for f in (where_ or []) if isinstance(f, str) and len(f) <= 90
                           and _FRAME.fullmatch(f)][-MAX_FRAMES:]
        if recovered is not None:
            p["recovered"] = bool(recovered)
        return p

    # ------------------------------------------------------------------ inside
    def _room(self, now) -> bool:
        """The hourly cap (call with the lock held)."""
        self._sent_times = [t for t in self._sent_times if now - t < 3600]
        if len(self._sent_times) >= MAX_PER_HOUR:
            return False
        self._sent_times.append(now)
        return True

    def _hold(self, p):
        """The service is down: this report waits for 'listening' (call with the lock held)."""
        now = self.clock()
        if self._down_since is None:
            self._down_since = now
        self.live = False                      # whatever the screen said, it isn't listening now
        p["live"] = False
        p.setdefault("n", 1)
        if len(self._held) < MAX_HELD:
            self._held.append([now + WAIT_BACK_SEC, p])

    def _secs(self):
        if self._down_since is None:
            return None
        return int(min(86400, max(0, self.clock() - self._down_since)))

    def _open_now(self):
        return dict(live=self.live, down=bool(self._held or self._owed_back or self._down_since is not None
                                              or self._log_wait))

    # ------------------------------------------------------------------ this computer's list of problems
    def _note(self, p, at=None, closed=False):
        """Adds a problem to this computer's own list (never sent by itself). The same problem again
        only counts up and moves to the top, so one that keeps coming back can't push the others out."""
        with self._lock:
            now = self.clock()
            when = at or p.get("at") or now_iso()
            held = p["kind"] in HELD_KINDS
            new = False
            sig = (p["kind"], p.get("exc"), tuple(p.get("frames") or ()))
            waiting = {id(e) for e, _ in self._log_wait}
            for i in range(len(self._log) - 1, -1, -1):
                e = self._log[i]
                if (e["kind"], e.get("exc"), tuple(e.get("frames") or ())) != sig:
                    continue
                e["n"] = min(100000, int(e.get("n", 1)) + 1)
                e["last"] = when
                fresh = id(e) not in waiting               # not still down from this same one
                if fresh:
                    self._log.append(self._log.pop(i))     # the newest again
                    if held:                               # down again: wait to hear it's back
                        for k in ("back", "secs", "closed"):
                            e.pop(k, None)
                        self._log_wait.append((e, None if p["kind"] == "lost" else now))
                    elif closed:
                        e["closed"] = True
                    else:
                        e.pop("closed", None)
                break
            else:
                e = {k: p[k] for k in ("kind", "exc", "frames", "recovered") if k in p}
                e.update(at=when, n=1)
                if closed:
                    e["closed"] = True
                self._log.append(e)
                del self._log[:-MAX_LOG]
                fresh = new = True
                if held:
                    self._log_wait.append((e, None if p["kind"] == "lost" else now))
            # A new problem, a new time down, or the app closing is written at once; a repeat can wait.
            if (new or (fresh and held) or closed or self._log_saved is None
                    or now - self._log_saved >= SAVE_EVERY_SEC):
                self._save_log()
            else:
                self._log_dirty = True
            if fresh and held and not self.on:
                self._write_marker(self._open_now())

    def _load_log(self):
        """problems.json from earlier runs: the last 30 days, each field checked again."""
        self._log, self._log_wait = [], []
        p = self.folder / LOG_FILE if self.folder is not None else None
        if p is None:
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return
        cutoff = datetime.now(timezone.utc).timestamp() - LOG_DAYS * 86400
        for e in (data.get("problems") if isinstance(data, dict) else None) or []:
            e = clean_problem(e)
            if e is not None and _iso_seconds(e.get("last") or e["at"]) >= cutoff:
                self._log.append(e)
        del self._log[:-MAX_LOG]

    def _save_log(self):
        """Writes problems.json (call with the lock held). Never raises."""
        self._log_dirty = False
        self._log_saved = self.clock()
        p = self.folder / LOG_FILE if self.folder is not None else None
        if p is None:
            return
        try:
            data = dict(v=1, problems=[{k: e[k] for k in LOG_KEYS if k in e} for e in self._log])
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, p)
        except Exception:
            pass

    # ------------------------------------------------------------------ a person's problem report
    def problem_report(self) -> dict:
        """What a person's report holds: this computer's last few problems, newest first."""
        with self._lock:
            if getattr(self, "_log_dirty", False):
                self._save_log()
            if not self.install:
                self.install = secrets.token_hex(6)
            items = [{k: e[k] for k in LOG_KEYS if k in e} for e in reversed(self._log)][:IN_REPORT]
        for e in items:
            if "frames" in e:
                e["frames"] = list(e["frames"])[-REPORT_FRAMES:]
        p = dict(v=1, kind="user", app=__version__, platform=platform_name(), install=self.install,
                 live=bool(self.live), at=now_iso(), problems=items)
        while items and (len(json.dumps(p, separators=(",", ":"))) > REPORT_MAX_BYTES
                         or len(problem_text(p)) > TALK_MAX):
            items.pop()
        return p

    def can_send(self) -> bool:
        return True                                          # every build can (no key since 2026-11-27)

    def send_problem(self, save_to: Path | None = None) -> dict:
        """One press: send this computer's problem report to the studio. When it doesn't go through,
        the same text is saved as a file to email or message."""
        with self._send_lock:                                # a second press waits for the first
            p = self.problem_report()
            text = problem_text(p)
            if self._user_sent is not None and self.clock() - self._user_sent < USER_AGAIN_SEC:
                return dict(sent=True, text=text)            # pressed twice: it already went
            try:
                ok = bool(self.send_user(p))
            except Exception:
                ok = False
            if ok:
                self._user_sent = self.clock()
                return dict(sent=True, text=text)
        folder = Path(save_to) if save_to is not None else report_folder(self.folder)
        path = save_text(folder, text, datetime.now())
        if path is None:
            return dict(sent=False, saved=False, text=text, why="failed")
        self._last_file = path
        return dict(sent=False, saved=True, file=path.name, folder=path.parent.name, text=text,
                    why="failed")

    def last_file(self) -> Path | None:
        return self._last_file

    def _put(self, item):
        try:
            self._q.put_nowait(item)
        except queue.Full:
            pass

    def _start_thread(self):
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name="crash-reports", daemon=True)
            self._thread.start()

    def _due(self):
        """Held reports whose wait is over go out saying 'not back up yet'. Also: how long until the next."""
        out = []
        with self._lock:
            now = self.clock()
            for item in list(self._held):
                if item[0] <= now:
                    self._held.remove(item)
                    item[1]["back"] = False
                    out.append(item[1])
            if out:
                self._owed_back = True
            nxt = min((due for due, _ in self._held), default=None)
        return out, (None if nxt is None else max(0.05, nxt - now))

    def _loop(self):
        while True:
            try:
                due, wait = self._due()
                for p in due:
                    if self.on:
                        self.send(p)
                try:
                    item = self._q.get(timeout=wait)
                except queue.Empty:
                    continue
                if item is _STOP:
                    return
                if not self.on:
                    continue
                if isinstance(item, tuple) and item and item[0] == "mark":
                    self._write_marker(self._open_now())
                elif isinstance(item, dict):
                    self.send(item)
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
        'lost' = it stopped without quitting while a service was listening (or down),
        'down' = it quit while the service was down, 'closed' = it quit normally."""
        p = self._path()
        data = {}
        if p is not None:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                self._marker_at = datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                data = {}
        if not isinstance(data, dict):
            data = {}
        ident = data.get("install")
        self.install = ident if isinstance(ident, str) and re.fullmatch(r"[a-f0-9]{12}", ident) else secrets.token_hex(6)
        was_open = data.get("open")
        if isinstance(was_open, dict):
            return "lost" if (was_open.get("live") or was_open.get("down")) else "closed"
        if data.get("down"):
            return "down"
        return None

    def _write_marker(self, open_now, down=False):
        p = self._path()
        if p is None or not self.install:
            return
        try:
            data = dict(install=self.install)
            if open_now is not None:
                data["open"] = open_now
            elif down:
                data["down"] = True
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
                if e is not None and not isinstance(e, (KeyboardInterrupt, SystemExit)):
                    p = self.payload("crash", e, recovered=False)
                    self._note(p, closed=True)      # this computer's own list, sent or not
                    if self.on:
                        p.update(n=1, closed=True)
                        self.send(p)
                        self._owed_back = True      # the next run says when it's listening again
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


def event(kind, exc=None, recovered=None, parts=None):
    _reporter.event(kind, exc, recovered, parts)


def closing():
    _reporter.closing()


def problem_report():
    """What "Send a problem report" would send, as the text the person sees."""
    p = _reporter.problem_report()
    return dict(text=problem_text(p), canSend=_reporter.can_send())


def send_problem(save_to=None):
    return _reporter.send_problem(save_to)


def last_file():
    return _reporter.last_file()
