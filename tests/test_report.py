"""Crash reports (report.py): off by default, sent only when something breaks, never carry words
or folders, never slow the service.
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

ALLOWED = {"v", "kind", "app", "platform", "install", "live", "at", "exc", "frames", "recovered", "n",
           "back", "secs", "closed"}


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
    """A local stand-in for the studio's endpoint: keeps each report and any key it came with."""

    def __init__(self, delay=0.0, status=204, body=b""):
        self.got, self.delay, self.status, self.body = [], delay, status, body
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
                    self.send_header("Content-Length", str(len(outer.body)))
                    self.end_headers()
                    self.wfile.write(outer.body)
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
        r2.event("crash", caught("another"))
        assert wait_for(lambda: calls == ["bad_pass", "crash"]), calls   # the sender lived through it
        r2.stop_thread()

    t = time.perf_counter()
    assert post("http://127.0.0.1:9/nothing-here", {"kind": "crash"}, timeout=1) is False
    slow = Studio(delay=3)
    try:
        assert post(slow.url, {"kind": "crash"}, timeout=0.5) is False   # gives up, no exception
    finally:
        slow.close()
    assert time.perf_counter() - t < 3

    # A person's send counts only when the studio itself says so (not a sign-in page, not any 200).
    for status, body, want in ((201, b'{"ok":true}', True), (200, b"<html>Sign in</html>", False),
                               (200, b"", False), (201, b'{"ok":false}', False), (401, b'{"ok":true}', False)):
        s = Studio(status=status, body=body)
        try:
            assert post(s.url, {"kind": "user"}, timeout=2, want_ok=True) is want, (status, body)
        finally:
            s.close()


def test_reports_reach_the_studio():
    """Nothing on a normal day; a failure right away; a restart once it's listening again, saying so."""
    studio = Studio()
    try:
        with tempfile.TemporaryDirectory() as d:
            r = Reporter(url=studio.url)
            r._hooks = True
            r.switch(True, Path(d))
            r.status("loading")
            r.status("listening")
            time.sleep(0.3)
            assert studio.got == [], studio.got          # opening and listening: nothing at all

            r.event("bad_pass", caught(), recovered=True)
            assert wait_for(lambda: len(studio.got) == 1), studio.got
            auth, bad = studio.got[0]
            assert auth is None and bad["kind"] == "bad_pass" and bad["recovered"] is True   # no key
            assert len(bad["install"]) == 12 and bad["app"] and bad["platform"] and bad["n"] == 1

            r.event("restart", caught("five bad passes"), recovered=False)
            r.status("reconnecting")
            r.status("loading")
            time.sleep(0.3)
            assert len(studio.got) == 1                  # held: it says whether it came back
            r.status("listening")
            assert wait_for(lambda: len(studio.got) == 2), studio.got
            again = studio.got[1][1]
            assert again["kind"] == "restart" and again["back"] is True and again["live"] is True
            assert isinstance(again["secs"], int) and again["recovered"] is False
            time.sleep(0.3)
            assert len(studio.got) == 2                  # no extra 'back': it was in the same report

            r.switch(False)
            r.event("crash", caught())
            r.event("restart", caught())
            time.sleep(0.4)
            assert len(studio.got) == 2                  # off again: silent
            r.stop_thread()
    finally:
        studio.close()


def test_not_back_in_time_then_back():
    """Still down after the wait: the report goes saying so, and one 'back' follows when it listens."""
    now = [1000.0]
    r, got = quiet(clock=lambda: now[0])
    with tempfile.TemporaryDirectory() as d:
        r.switch(True, Path(d))
        r.status("listening")
        e = caught()
        r.event("restart", e, recovered=False)
        for _ in range(5):
            r.event("restart", e, recovered=False)       # it keeps failing while down: counted
        now[0] += report.WAIT_BACK_SEC + 1
        r._put(("mark",))                                # (the real sender wakes on its own timer)
        assert wait_for(lambda: len(got) == 1), got
        assert got[0]["kind"] == "restart" and got[0]["back"] is False and got[0]["n"] == 6
        assert got[0]["live"] is False
        assert wait_for(lambda: json.loads((Path(d) / report.FILE).read_text())["open"]["down"] is True)

        now[0] += 600
        r.status("loading")
        r.status("listening")
        assert wait_for(lambda: len(got) == 2), got
        assert got[1]["kind"] == "back" and got[1]["secs"] == 721 and got[1]["live"] is True
        r.status("reconnecting")
        r.status("listening")                            # a sound input dropping out isn't a failure
        time.sleep(0.2)
        assert len(got) == 2
        assert set(got[1]) <= ALLOWED and set(got[0]) <= ALLOWED
        r.stop_thread()


def drain(r):
    items = []
    while not r._q.empty():
        items.append(r._q.get_nowait())
    return items


def test_a_run_that_stopped_suddenly():
    """If the app vanished mid-service (no clean quit), the next run says so once it's listening
    again; a clean quit says nothing; quitting while down says 'back' on the next run."""
    now = [50.0]
    with tempfile.TemporaryDirectory() as d:
        r, got = quiet(clock=lambda: now[0])
        r._start_thread = lambda: None
        r._put = got.append
        r.switch(True, Path(d))
        ident = r.install
        r.live = True
        r._write_marker(r._open_now())                   # what the sender writes while listening

        r2, got2 = quiet(clock=lambda: now[0])
        r2._start_thread = lambda: None
        r2.switch(True, Path(d))
        assert r2.install == ident and got2 == []        # held until it's listening again
        assert [i for i in drain(r2) if isinstance(i, dict)] == []
        now[0] += 40
        r2.status("listening")
        sent = [i for i in drain(r2) if isinstance(i, dict)]
        assert [s["kind"] for s in sent] == ["lost"], sent
        assert sent[0]["back"] is True and sent[0]["secs"] == 40 and "exc" not in sent[0]
        assert set(sent[0]) <= ALLOWED
        r2.closing()

        r3, got3 = quiet()
        r3._start_thread = lambda: None
        r3.switch(True, Path(d))
        r3.status("listening")
        assert [i for i in drain(r3) if isinstance(i, dict)] == [] and r3.install == ident
        stored = json.loads((Path(d) / report.FILE).read_text())
        assert set(stored) <= {"install", "open", "down"}

        # Quit while a restart was still waiting: it goes now, marked closed, and the next run
        # says when it is listening again.
        r3.event("restart", caught(), recovered=False)
        r3.closing()
        assert [g["kind"] for g in got3] == ["restart"] and got3[0]["closed"] is True and got3[0]["back"] is False
        assert json.loads((Path(d) / report.FILE).read_text()).get("down") is True
        r4, _ = quiet()
        r4._start_thread = lambda: None
        r4.switch(True, Path(d))
        r4.status("listening")
        sent = [i for i in drain(r4) if isinstance(i, dict)]
        assert [s["kind"] for s in sent] == ["back"] and "secs" not in sent[0], sent


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
            assert [g["kind"] for g in got] == ["crash"], got
            assert got[0]["exc"] == "RuntimeError" and got[0]["recovered"] is False
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
        assert [g for g in got if isinstance(g, dict)] == [], got        # held until it's listening
        held = [h for _, h in r._held]
        assert [g["kind"] for g in held] == ["restart"], held
        assert held[0]["recovered"] is False and "five" not in json.dumps(held[0])
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


def test_no_key_anywhere():
    # Every build can send, with nothing to set up (Captain, 2026-11-27): no key in the source, the
    # builds or the requests.
    root = Path(report.__file__).resolve().parents[1]
    src = Path(report.__file__).read_text(encoding="utf-8")
    assert "ctw1_" not in src and "Authorization" not in src and "report-key" not in src
    for f in (".github/workflows/mac-build.yml", ".github/workflows/windows-build.yml", "packaging/riah-translate.spec"):
        p = root / f
        if p.exists():                                   # the public copy has the same files
            assert "REPORT_KEY" not in p.read_text(encoding="utf-8") and "report-key" not in p.read_text(encoding="utf-8"), f
    with tempfile.TemporaryDirectory() as d:
        # Ticking the box works in any build.
        r, got = quiet()
        r.switch(True, Path(d))
        assert r.on is True and r.can_send() is True
        r.event("bad_pass", caught(), recovered=True)
        assert wait_for(lambda: len(got) == 1), got
        r.stop_thread()


SECRETS = ("sermon", "John", "pastor", "Users", "secret", "Documents", "Claude Cowork", "\\")


def test_problems_are_kept_with_reports_off():
    """A person's report needs to know what broke, so the list is kept even with the tick off.
    It stays on this computer: nothing is sent and no thread starts."""
    now = [100.0]
    with tempfile.TemporaryDirectory() as d:
        r, got = quiet(clock=lambda: now[0])
        r.switch(False, Path(d))
        r.status("listening")
        e = caught()
        for _ in range(3):
            r.event("bad_pass", e, recovered=True)
        r.event("restart", caught("five bad passes"), recovered=False)
        r.status("reconnecting")
        now[0] += 14
        r.status("listening")
        assert got == [] and r._thread is None and r.on is False
        log = [e for e in r._log]
        assert [x["kind"] for x in log] == ["bad_pass", "restart"], log
        assert log[0]["n"] == 3 and log[1]["back"] is True and log[1]["secs"] == 14
        saved = json.loads((Path(d) / report.LOG_FILE).read_text(encoding="utf-8"))
        text = json.dumps(saved)
        for s in SECRETS:
            assert s not in text, (s, text)
        assert all(set(x) <= set(report.LOG_KEYS) for x in saved["problems"]), saved

        # The app vanished while listening: the next run lists a sudden stop.
        r.event("crash", caught())
        r.live = True
        r._write_marker(r._open_now())
        r2, _ = quiet()
        r2.switch(False, Path(d))
        assert [x["kind"] for x in r2._log] == ["bad_pass", "restart", "crash", "lost"], r2._log
        assert r2.install == r.install
        r2.status("listening")
        assert r2._log[-1]["back"] is True
        r2.closing()                                     # a clean quit: the run after says nothing new
        r3, _ = quiet()
        r3.switch(False, Path(d))
        assert [x["kind"] for x in r3._log] == ["bad_pass", "restart", "crash", "lost"]

        # Anything odd in the file is dropped; old problems go after 30 days.
        bad = dict(v=1, problems=[dict(kind="crash", at="2020-01-01T00:00:00Z"),
                                  dict(kind="bad_pass", at=report.now_iso(), exc="x y", frames=["/Users/p/a.py:1", "a.py:2"],
                                       note="the sermon"), dict(kind="evil", at=report.now_iso()), "nope"])
        (Path(d) / report.LOG_FILE).write_text(json.dumps(bad), encoding="utf-8")
        r4, _ = quiet()
        r4.switch(False, Path(d))
        assert len(r4._log) == 1 and r4._log[0]["frames"] == ["a.py:2"] and "exc" not in r4._log[0]
        assert "note" not in r4._log[0]


def test_problem_report_says_only_what_broke():
    now = [100.0]
    with tempfile.TemporaryDirectory() as d:
        r, _ = quiet(clock=lambda: now[0])
        r.switch(False, Path(d))
        empty = r.problem_report()
        assert empty["kind"] == "user" and empty["problems"] == []
        assert "Nothing has broken" in report.problem_text(empty)

        r.event("restart", caught(), recovered=False)
        now[0] += 14
        r.status("listening")
        for i in range(40):                              # far more than fit: newest first, under 4 KB
            kind = type(f"BrokeNumber{i}", (Exception,), {})
            try:
                raise kind(i)
            except kind as k:
                now[0] += report.SAME_EVERY_SEC + 1
                r.event("crash", k)
        p = r.problem_report()
        body = json.dumps(p, separators=(",", ":"))
        assert len(body) <= report.REPORT_MAX_BYTES, len(body)
        assert set(p) <= ALLOWED | {"problems"}, p
        assert 1 <= len(p["problems"]) <= report.IN_REPORT
        assert all(set(x) <= set(report.LOG_KEYS) and len(x.get("frames", [])) <= report.REPORT_FRAMES
                   for x in p["problems"])
        text = report.problem_text(p)
        for s in SECRETS:
            assert s not in body and s not in text, s

        r2, _ = quiet(clock=lambda: now[0])
        r2.event("restart", caught(), recovered=False)
        now[0] += 14
        r2.status("listening")
        for _ in range(2):
            r2.event("bad_pass", caught(), recovered=True)
        text = report.problem_text(r2.problem_report())
        assert "What broke, newest first:" in text and "(2 times, last at " in text
        assert "The service broke and restarted itself. Back up and listening after 14 s." in text
        assert "Error: RuntimeError. Where: " in text and "test_report.py:" in text
        assert text.rstrip().endswith("no folders and nothing typed.")


def test_send_problem_sends_once_or_saves_a_file():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "Downloads"
        sent = []
        r, got = quiet(send_user=lambda p: sent.append(p) or True)
        r.event("bad_pass", caught(), recovered=True)
        a = r.send_problem(out)
        assert a["sent"] is True and len(sent) == 1 and sent[0]["kind"] == "user"
        assert sent[0]["problems"][0]["kind"] == "bad_pass" and got == []
        assert r.send_problem(out)["sent"] is True and len(sent) == 1    # a second press: not twice
        assert not out.exists()

        r2, _ = quiet(send_user=lambda p: False)          # the studio didn't take it
        b = r2.send_problem(out)
        assert b["sent"] is False and b["saved"] is True and b["why"] == "failed"
        f = out / b["file"]
        assert f.read_text(encoding="utf-8") == b["text"] + "\n" and b["folder"] == "Downloads"
        assert b["file"].startswith("Riah Translate problem report ") and r2.last_file() == f
        c = r2.send_problem(out)                          # never over the last one
        assert c["file"] != b["file"] and (out / b["file"]).exists()

        def offline(p):
            raise OSError("no connection")
        r3, _ = quiet(send_user=offline)                  # no connection: saved all the same
        e = r3.send_problem(out)
        assert e["sent"] is False and e["why"] == "failed" and e["saved"] is True

        stuck = Path(d) / "a-file"
        stuck.write_text("x")
        g = r3.send_problem(stuck)                        # can't save either: says so, never raises
        assert g["sent"] is False and g["saved"] is False and g["text"]


def test_problem_report_through_the_app_server():
    """Only this computer can read or send it, and the send is a JSON POST like every other change."""
    import socket
    import urllib.error
    import urllib.request
    from riah_translate.hub import Hub
    from riah_translate.server import serve

    calls = []

    class FakeApi:
        def __getattr__(self, name):                     # every other route: does nothing
            return lambda *a, **k: None

        def problem_report(self):
            return dict(text="Riah Church Translate problem report", canSend=True)

        def send_problem_report(self, body):
            calls.append(body)
            return dict(sent=False, saved=True, file="x.txt", folder="Downloads", text="t", why="failed")

        def show_problem_file(self):
            calls.append("show")

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    httpd = serve(Hub(), FakeApi(), port=port)
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/api/problem-report", timeout=5) as r:
            assert json.loads(r.read())["canSend"] is True

        def send(path, headers):
            req = urllib.request.Request(base + path, data=b"{}", method="POST", headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, None
        assert send("/api/problem-report", {"Content-Type": "application/json"})[1]["saved"] is True
        assert send("/api/show-problem-file", {"Content-Type": "application/json"})[0] == 200
        assert send("/api/problem-report", {"Content-Type": "text/plain"})[0] == 403
        assert send("/api/problem-report", {"Content-Type": "application/json", "Origin": "https://evil.example"})[0] == 403
        assert calls == [{}, "show"], calls
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_repeating_problem_stays_one_line():
    """One failure every 11 minutes for hours must not push a rarer one off the list, and a Stop
    after a restart isn't counted as hours down."""
    now = [100.0]
    with tempfile.TemporaryDirectory() as d:
        r, _ = quiet(clock=lambda: now[0])
        r.switch(False, Path(d))
        r.status("listening")
        r.event("crash", caught())
        for _ in range(30):
            now[0] += report.SAME_EVERY_SEC + 60
            r.event("bad_pass", caught(), recovered=True)
        assert [x["kind"] for x in r._log] == ["crash", "bad_pass"] and r._log[1]["n"] == 30, r._log
        r.event("crash", caught())                       # the older one again: back on top
        assert [x["kind"] for x in r._log] == ["bad_pass", "crash"] and r._log[1]["n"] == 2

        r.event("restart", caught("x"), recovered=False)
        r.status("reconnecting")
        r.status("stopped")                              # someone pressed Stop
        now[0] += 7200
        r.status("listening")
        last = r._log[-1]
        assert last["kind"] == "restart" and "back" not in last and "secs" not in last, last
        assert "after 2 h" not in report.problem_text(r.problem_report())

        # Seen again on another day: the text gives that day too, not just a time.
        e = dict(kind="bad_pass", at="2026-11-20T10:00:00Z", last="2026-11-27T10:00:00Z", n=9)
        p = dict(v=1, kind="user", app="0.3.0", platform="x", install="a1b2c3d4e5f6", live=True,
                 at="2026-11-27T10:05:00Z", problems=[e])
        assert ", last at " + report._when(e["last"]) in report.problem_text(p)

    # Two presses at once send once.
    calls = []

    def slow(p):
        calls.append(p)
        time.sleep(0.2)
        return True
    r2 = Reporter("https://x.test/r", send=lambda p: True, send_user=slow)
    outs = []
    ts = [threading.Thread(target=lambda: outs.append(r2.send_problem())) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(calls) == 1 and [o["sent"] for o in outs] == [True, True], (calls, outs)


def test_a_report_from_the_engine_process_carries_only_a_name_and_file_lines():
    """The engine process sends the error's type name and file:line frames; anything else it tries to
    send (a folder, a message, a long string) is dropped."""
    r, _ = quiet()
    r.install = "a1b2c3d4e5f6"
    p = r.payload("crash", parts=("RuntimeError", ["engine.py:120", "isolate.py:88"]), recovered=True)
    assert p["exc"] == "RuntimeError" and p["frames"] == ["engine.py:120", "isolate.py:88"], p
    assert set(p) <= ALLOWED, p
    bad = r.payload("crash", parts=("Boom: John said hi", ["/Users/john/secret.py:3", "C:\\x\\y.py:2",
                                                           "x" * 200, "ok.py:7"]))
    text = json.dumps(bad)
    assert "exc" not in bad and bad["frames"] == ["ok.py:7"], bad
    for secret in ("John", "Users", "secret", "\\"):
        assert secret not in text, (secret, text)
    assert r.payload("crash", parts=None).get("frames") is None


if __name__ == "__main__":
    tests =[(k, v) for k, v in list(globals().items()) if k.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"PASS ({len(tests)} checks)")
