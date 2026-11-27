"""Crash reports (report.py): off by default, never carry words or folders, never slow the service.
No models, no microphone, nothing leaves this computer (a local test server stands in for the
studio). Safe to run anywhere:

    python tests/test_report.py
"""
import json
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from riah_translate import report  # noqa: E402
from riah_translate.report import Reporter, exc_name, frames, post, where  # noqa: E402

ALLOWED = {"v", "kind", "app", "platform", "install", "live", "at", "exc", "frames", "recovered", "n", "prev"}


def wait_for(cond, secs=3):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def quiet(**kw):
    """A reporter that collects what it would send, without touching the real hooks."""
    got = []
    kw.setdefault("key", "test-key-0123456789")
    r = Reporter(send=got.append, **kw)
    r._hooks = True
    return r, got


def boom(text):
    def inner():
        raise RuntimeError(text)
    inner()


def caught(text="the sermon said John 3:16 at C:\\Users\\pastor\\Documents\\secret.txt"):
    try:
        boom(text)
    except RuntimeError as e:
        return e


class Studio:
    """A local stand-in for the studio's endpoint: keeps each report and its key."""

    def __init__(self, delay=0.0, status=204):
        self.got, self.delay, self.status = [], delay, status
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n)
                time.sleep(outer.delay)
                outer.got.append((self.headers.get("Authorization"), json.loads(body)))
                try:
                    self.send_response(outer.status)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                except OSError:
                    pass                                 # the app already gave up waiting

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/api/ct-watch/report"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def test_off_by_default():
    from riah_translate.app import SETTINGS_DEFAULTS
    assert SETTINGS_DEFAULTS["crashReports"] is False
    assert report._reporter.on is False                 # nothing switches it on by itself
    r, got = quiet()
    r.event("bad_pass", caught(), recovered=True)
    r.status("listening")
    assert got == [] and r._thread is None               # off: no thread, nothing sent


def test_never_words_or_folders():
    """The error's message and every folder stay on the computer; only type names and file:line go."""
    r, _ = quiet()
    r.install = "a1b2c3d4e5f6"
    p = r.payload("restart", caught(), recovered=False)
    text = json.dumps(p)
    assert set(p) <= ALLOWED, p
    for secret in ("sermon", "John", "pastor", "Users", "secret", "Documents", "Claude Cowork", "\\"):
        assert secret not in text, (secret, text)
    assert p["exc"] == "RuntimeError"
    assert p["frames"] and all(":" in f and not f.startswith("/") for f in p["frames"]), p["frames"]
    assert p["frames"][-1].startswith("test_report.py:"), p["frames"]
    assert len(text) < 2000


def test_where_keeps_no_folders():
    cases = {
        "/Applications/Riah Church Translate.app/Contents/Frameworks/riah_translate/asr.py": "riah_translate/asr.py",
        "C:\\Users\\pastor\\Code\\Riah Translate\\riah_translate\\engine.py": "riah_translate/engine.py",
        "/Users/pastor/venv/lib/python3.12/site-packages/faster_whisper/vad.py": "faster_whisper/vad.py",
        "C:\\Python\\Lib\\site-packages\\six.py": "six.py",
        "faster_whisper/transcribe.py": "faster_whisper/transcribe.py",          # how a frozen app names it
        "/Users/pastor/Desktop/notes.py": "notes.py",                            # a full path: the name only
        "/usr/lib/python3.12/threading.py": "threading.py",
        "<frozen importlib._bootstrap>": "frozen-importlib._bootstrap",
        "": "?",
    }
    for name, want in cases.items():
        got = where(name)
        assert got == want, (name, got, want)
        assert "pastor" not in got and "Users" not in got

    class Odd(Exception):
        pass
    assert exc_name(Odd()).endswith("Odd") and "<" not in exc_name(Odd())
    assert frames(None) == []


def test_same_failure_is_counted_not_repeated():
    now = [1000.0]
    r, got = quiet(clock=lambda: now[0])
    r.on = True
    r._put = got.append                                  # look at what goes on the queue
    e = caught()
    for _ in range(4):
        r.event("bad_pass", e, recovered=True)
    assert len(got) == 1 and got[0]["n"] == 1
    now[0] += report.SAME_EVERY_SEC + 1
    r.event("bad_pass", e, recovered=True)
    assert len(got) == 2 and got[1]["n"] == 4            # this one plus the three held back

    got.clear()
    for i in range(report.MAX_PER_HOUR + 10):            # all different: the hourly cap holds
        try:
            raise KeyError(i)
        except KeyError as k:
            k.__traceback__ = None
            r._last.clear()
            r.event("crash", k)
    assert len(got) <= report.MAX_PER_HOUR


def test_sending_never_touches_the_service():
    """A slow, broken or missing studio costs the service nothing."""
    r, _ = quiet()
    r.send = lambda p: time.sleep(5)                     # a send that hangs
    with tempfile.TemporaryDirectory() as d:
        r.switch(True, Path(d))
        t = time.perf_counter()
        for _ in range(50):
            r.event("bad_pass", caught(), recovered=True)
            r.status("listening")
            r.status("loading")
        assert time.perf_counter() - t < 0.5, time.perf_counter() - t
        r.on = False
        r._q.queue.clear()
        r.stop_thread(0.1)

    calls = []

    def flaky(p):
        calls.append(p["kind"])
        raise OSError("no internet")
    r2, _ = quiet()
    r2.send = flaky
    with tempfile.TemporaryDirectory() as d:
        r2.switch(True, Path(d))
        r2.event("bad_pass", caught(), recovered=True)
        assert wait_for(lambda: calls == ["start", "bad_pass"]), calls   # the sender lived through it
        r2.stop_thread()

    t = time.perf_counter()
    assert post("http://127.0.0.1:9/nothing-here", "k", {"kind": "start"}, timeout=1) is False
    slow = Studio(delay=3)
    try:
        assert post(slow.url, "k", {"kind": "start"}, timeout=0.5) is False   # gives up, no exception
    finally:
        slow.close()
    assert time.perf_counter() - t < 3


def test_reports_reach_the_studio():
    """Start, a failure, then 'back up' as soon as it is listening again, plus the 10-minute heartbeat."""
    studio = Studio()
    saved = report.HEARTBEAT_SEC
    try:
        with tempfile.TemporaryDirectory() as d:
            r = Reporter(url=studio.url, key="test-key")
            r._hooks = True
            r.switch(True, Path(d))
            assert wait_for(lambda: len(studio.got) == 1)
            auth, start = studio.got[0]
            assert auth == "Bearer test-key" and start["kind"] == "start" and "prev" not in start
            assert len(start["install"]) == 12 and start["app"] and start["platform"]

            r.status("listening")
            r.event("bad_pass", caught(), recovered=True)
            r.status("reconnecting")
            r.status("listening")                        # back: a heartbeat right away
            assert wait_for(lambda: [g[1]["kind"] for g in studio.got] == ["start", "bad_pass", "heartbeat"]), studio.got
            assert studio.got[1][1]["recovered"] is True and studio.got[2][1]["live"] is True

            report.HEARTBEAT_SEC = 0.2
            r._next_hb = 0
            r._put(("mark",))
            assert wait_for(lambda: len(studio.got) >= 5), studio.got       # and on its own, every so often
            assert all(g[1]["kind"] == "heartbeat" for g in studio.got[3:])
            assert all(g[0] == "Bearer test-key" for g in studio.got)

            r.switch(False)
            time.sleep(0.4)                              # let a heartbeat already on its way land
            n = len(studio.got)
            r.event("crash", caught())
            time.sleep(0.5)
            assert len(studio.got) == n                  # off again: silent
            r.stop_thread()
    finally:
        report.HEARTBEAT_SEC = saved
        studio.close()


def test_a_run_that_stopped_suddenly():
    """If the app vanished mid-service (no clean quit), the next start says so; a clean quit doesn't."""
    with tempfile.TemporaryDirectory() as d:
        r, got = quiet()
        r._put = got.append
        r._start_thread = lambda: None
        r.switch(True, Path(d))
        ident = r.install
        r.live = True
        r._write_marker(dict(live=True))                 # what the sender writes while listening

        r2, got2 = quiet()
        r2._put = got2.append
        r2._start_thread = lambda: None
        r2.switch(True, Path(d))
        assert got2[0]["prev"] == "lost" and r2.install == ident, got2
        r2.closing()

        r3, got3 = quiet()
        r3._put = got3.append
        r3._start_thread = lambda: None
        r3.switch(True, Path(d))
        assert "prev" not in got3[0] and r3.install == ident
        stored = json.loads((Path(d) / report.FILE).read_text())
        assert set(stored) <= {"install", "open"}


def test_uncaught_thread_error_is_reported():
    saved = (sys.excepthook, threading.excepthook)
    try:
        r, got = quiet()
        r._hooks = False
        r._start_thread = lambda: None
        r._put = got.append
        threading.excepthook = lambda args: None         # keep the test output quiet
        with tempfile.TemporaryDirectory() as d:
            r.switch(True, Path(d))
            t = threading.Thread(target=boom, args=("words",))
            t.start()
            t.join()
            assert [g["kind"] for g in got] == ["start", "crash"], got
            assert got[1]["exc"] == "RuntimeError" and got[1]["recovered"] is False
    finally:
        sys.excepthook, threading.excepthook = saved
        import atexit
        atexit.unregister(r.closing)


def test_engine_reports_bad_passes_and_restarts():
    import numpy as np
    import riah_translate.audio as A
    import riah_translate.engine as E
    from riah_translate.hub import Hub

    class FakeSource:
        def __init__(self, chunks): self.left = chunks
        def start(self): return self
        def read(self, secs):
            if self.left <= 0: return np.zeros(0, dtype=np.float32), True
            self.left -= 1
            return np.full(int(0.2 * E.SR), 0.01, dtype=np.float32), False
        def stop(self): pass

    class FakeTr:
        def __init__(self, fails): self.fails, self.calls, self.last_transcribe_sec = fails, 0, 0.0
        def push(self, chunk): pass
        def step(self, final=False):
            self.calls += 1
            if self.calls <= self.fails: raise RuntimeError("the preacher's words here")
            return [], ""
        def pending_text(self): return ""
        def trim(self): pass
        def recover(self, hard=False): pass

    class FakeMt:
        def draft_covers(self, seg, text): return False
        def add_line(self, *a): pass
        def want_draft(self, *a): pass
        def close(self, timeout=30): pass
        def thread_alive(self): return False

    r, got = quiet()
    r.on = True
    r._put = got.append
    saved = (report._reporter, A.MicSource, E.StreamingTranscriber, E.load_whisper, E.choose_whisper)
    try:
        report._reporter = r
        E.load_whisper = lambda *a, **k: object()
        E.choose_whisper = lambda *a, **k: "small.en"
        A.MicSource = lambda *a, **k: FakeSource(20)
        tr = FakeTr(1)
        E.StreamingTranscriber = lambda *a, **k: tr
        eng = E.Engine(Hub())
        eng.stop_flag.wait = lambda secs=None: False
        eng._translator = lambda cfg, threads: FakeMt()
        cfg = dict(src="en", tgts=["th"], input=None, channel=None, model="small.en")
        eng._session(cfg)
        kinds = [g["kind"] for g in got if isinstance(g, dict)]
        assert kinds == ["bad_pass"], got                # one failed pass: reported, service kept going
        bad = [g for g in got if isinstance(g, dict)][0]
        assert bad["recovered"] is True and bad["exc"] == "RuntimeError"
        assert "preacher" not in json.dumps(bad)
        assert any(f.startswith("riah_translate/engine.py:") for f in bad["frames"]), bad["frames"]

        got.clear()
        calls = []

        def session(cfg):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("five bad passes")
        eng._session = session
        eng._run(cfg)
        sent = [g for g in got if isinstance(g, dict)]
        assert [g["kind"] for g in sent] == ["restart"], got
        assert sent[0]["recovered"] is False and "five" not in json.dumps(sent[0])
    finally:
        report._reporter, A.MicSource, E.StreamingTranscriber, E.load_whisper, E.choose_whisper = saved


def test_operator_switch():
    """The tick box: only a real 'on' switches it on, it is saved, and it takes effect at once."""
    import types
    from riah_translate.app import App
    seen, saved_calls = [], []
    fake = types.SimpleNamespace(settings=types.SimpleNamespace(data={}, save=lambda: saved_calls.append(1)))
    real = report.switch
    try:
        report.switch = lambda on, folder=None: seen.append(on)
        assert App.reports(fake, dict(on=True)) == dict(ok=True, on=True)
        assert App.reports(fake, dict(on="yes")) == dict(ok=True, on=False)
        assert App.reports(fake, {}) == dict(ok=True, on=False)
    finally:
        report.switch = real
    assert seen == [True, False, False] and len(saved_calls) == 3
    assert fake.settings.data["crashReports"] is False


def test_no_key_in_the_source():
    # The key comes from the build (report-key.txt) or RIAH_TRANSLATE_REPORT_KEY, never the source.
    src = (Path(report.__file__)).read_text(encoding="utf-8")
    assert "ctw1_" not in src
    import os
    real_file, real_env = report.KEY_FILE, os.environ.pop("RIAH_TRANSLATE_REPORT_KEY", None)
    with tempfile.TemporaryDirectory() as d:
        try:
            report.KEY_FILE = Path(d) / "report-key.txt"
            assert report._ingest_key() == ""                                    # plain checkout
            report.KEY_FILE.write_text("ctw1_from-the-build-0123456789\n")
            assert report._ingest_key() == "ctw1_from-the-build-0123456789"
            report.KEY_FILE.write_text("not a key; rm -rf /\n")
            assert report._ingest_key() == ""
            os.environ["RIAH_TRANSLATE_REPORT_KEY"] = "ctw1_from-the-env-0123456789"
            assert report._ingest_key() == "ctw1_from-the-env-0123456789"
        finally:
            report.KEY_FILE = real_file
            os.environ.pop("RIAH_TRANSLATE_REPORT_KEY", None)
            if real_env is not None:
                os.environ["RIAH_TRANSLATE_REPORT_KEY"] = real_env

        # No key: ticking the box sends nothing and starts nothing.
        r, got = quiet(key="")
        r.switch(True, Path(d))
        assert r.on is False and r._thread is None
        r.event("bad_pass", RuntimeError("x"), recovered=True)
        time.sleep(0.05)
        assert got == []


if __name__ == "__main__":
    tests =[(k, v) for k, v in list(globals().items()) if k.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"PASS ({len(tests)} checks)")
