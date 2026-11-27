"""The speech engine in its own process (isolate.py): a crash, a hang, a Restart the engine ignores, a
Stop it can't answer, a process that crashes every time, and, above all, that silence and mumbling are
NOT mistaken for any of those. Uses a stand-in engine (fake_engine.py) that misbehaves on command, in a
real second process. No models, no microphone, safe to run anywhere (about a minute):

    python tests/test_isolate.py
"""
import multiprocessing as mp
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from riah_translate import isolate  # noqa: E402
from riah_translate.hub import Hub  # noqa: E402
from riah_translate.isolate import EngineProxy  # noqa: E402


def wait_for(cond, secs=10, what="the condition"):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    raise AssertionError(f"timed out after {secs} s waiting for {what}")


class Rig:
    """A proxy over the fake engine, with the screen's hub, every event, and the file that steers the engine."""

    def __init__(self, **kw):
        self.dir = tempfile.TemporaryDirectory()
        self.ctl = str(Path(self.dir.name) / "mode.txt")
        self.mode("ok")
        self.hub = Hub()
        self.events = []
        self.hub.taps.append(self.events.append)
        self.p = EngineProxy(self.hub, factory="fake_engine:FakeEngine", **kw)
        self.p.opts["paths"] = [str(HERE), str(HERE.parent)]
        self.p.hb_live, self.p.hb_load = 4, 8
        self.p.ready_within, self.p.restart_wait, self.p.stop_wait = 30, 1.5, 2.5
        self.p.backoff_cap = 2
        self.spawns = 0
        real = self.p._spawn

        def counted():
            self.spawns += 1
            return real()
        self.p._spawn = counted

    def mode(self, m):
        Path(self.ctl).write_text(m, encoding="utf-8")

    def start(self):
        self.p.start(dict(src="en", tgts=["th"], fake=dict(ctl=self.ctl)))

    def lines(self):
        return [e for e in self.events if e.get("type") == "line"]

    def states(self):
        return [e["state"] for e in self.events if e.get("type") == "status"]

    def pid(self):
        return self.p.proc.pid if self.p.proc is not None else None

    def more_lines(self, n=3, secs=15):
        have = len(self.lines())
        wait_for(lambda: len(self.lines()) >= have + n, secs, f"{n} more lines")

    def close(self):
        self.p.close()
        self.dir.cleanup()


def test_runs_and_stops():
    r = Rig()
    try:
        r.p.warm()
        wait_for(lambda: r.p.ready, 30, "the engine process to be ready")
        pid = r.pid()
        r.start()
        r.more_lines(5)
        assert r.p.running and "listening" in r.states()
        t = time.monotonic()
        r.p.stop()
        assert time.monotonic() - t < 2.5, "a clean Stop is quick"
        assert not r.p.running and r.states()[-1] == "stopped", r.states()
        assert r.pid() == pid and r.p.proc.is_alive(), "the same engine process waits for the next Start"
        r.start()
        r.more_lines(3)
        assert r.pid() == pid
    finally:
        r.close()


def test_a_crash_is_recovered_and_the_screen_keeps_its_lines():
    r = Rig()
    try:
        r.start()
        r.more_lines(5)
        before_pid, before = r.pid(), len(r.hub.lines)
        r.mode("crash")
        wait_for(lambda: "reconnecting" in r.states(), 10, "the crash to be noticed")
        r.mode("ok")
        r.more_lines(4, 30)
        assert r.p.running and r.pid() != before_pid, "a new engine process carried on"
        assert r.states()[-1] == "listening", r.states()
        assert len(r.hub.lines) >= before, "the lines already on the screen stay"
        segs = [e["seg"] for e in r.lines()]
        ids = [e["id"] for e in r.lines()]
        assert segs == list(range(len(segs))), f"numbering carries on with no gap or repeat: {segs}"
        assert len(set(ids)) == len(ids) and ids == sorted(ids), ids
    finally:
        r.close()


def test_a_hang_is_noticed_and_recovered():
    r = Rig()
    try:
        r.start()
        r.more_lines(3)
        before_pid = r.pid()
        r.mode("hang")                      # the loop stops turning: a native call that never returns
        t = time.monotonic()
        wait_for(lambda: "reconnecting" in r.states(), 15, "the hang to be noticed")
        assert time.monotonic() - t < 10, "noticed within seconds, not minutes"
        r.mode("ok")
        r.more_lines(3, 30)
        assert r.pid() != before_pid and r.p.running
        segs = [e["seg"] for e in r.lines()]
        assert segs == list(range(len(segs))), segs
    finally:
        r.close()


def test_silence_is_not_a_hang():
    """Moments of silence, mumbling, a speaker off the microphone: the loop keeps turning and says nothing.
    Nothing may restart."""
    r = Rig()
    try:
        r.start()
        r.more_lines(3)
        pid, spawns = r.pid(), r.spawns
        r.mode("silence")
        time.sleep(9)                       # six times the engine's own stall limit
        assert r.pid() == pid and r.spawns == spawns, "silence must not restart anything"
        assert "reconnecting" not in r.states() and r.p.running
        r.mode("ok")
        r.more_lines(3)
        assert r.pid() == pid, "the same engine carries on after the quiet"
    finally:
        r.close()


def test_restart_button_quick_and_guaranteed():
    r = Rig()
    try:
        assert r.p.restart() is False, "nothing to restart before a service is live"
        r.start()
        r.more_lines(3)
        pid = r.pid()
        assert r.p.restart() is True
        wait_for(lambda: any(e.get("detail") == "Restarted." for e in r.events), 5, "the quick restart")
        time.sleep(r.p.restart_wait + 1.5)
        assert r.pid() == pid and "reconnecting" not in r.states(), "a Restart the engine answers is not a crash"
        # An engine that does not answer: the app starts a new one anyway.
        r.mode("ignore_restart")
        r.more_lines(2)
        assert r.p.restart() is True
        wait_for(lambda: "reconnecting" in r.states(), 10, "the ignored Restart to be taken over")
        r.mode("ok")
        r.more_lines(3, 30)
        assert r.pid() != pid and r.p.running
    finally:
        r.close()


def test_stop_works_even_when_the_engine_is_stuck():
    r = Rig()
    try:
        r.start()
        r.more_lines(3)
        r.mode("hang")
        time.sleep(0.3)
        t = time.monotonic()
        r.p.stop()
        assert time.monotonic() - t < r.p.stop_wait + 2, "Stop never waits long on a stuck engine"
        assert not r.p.running and r.states()[-1] == "stopped", r.states()
        r.mode("ok")
        r.start()                           # and the next Start works
        r.more_lines(3, 30)
    finally:
        r.close()


def test_a_process_that_crashes_every_time_backs_off_and_still_recovers():
    r = Rig()
    try:
        r.mode("crash_on_start")
        r.start()
        time.sleep(10)
        n = r.spawns
        assert 3 <= n <= 9, f"it keeps trying, with growing pauses, not in a tight loop: {n} starts in 10 s"
        assert r.p.running, "only Stop ends a service"
        r.mode("ok")
        r.more_lines(3, 30)
        assert r.states()[-1] == "listening"
        r.mode("crash")
        wait_for(lambda: r.spawns > n, 15, "another start")
        r.p.stop()
        spawns = r.spawns
        time.sleep(3)
        assert not r.p.running and r.spawns <= spawns + 1, "after Stop nothing keeps restarting"
    finally:
        r.close()


def test_a_service_that_cannot_start_ends_with_its_message():
    r = Rig()
    try:
        r.mode("end")
        r.start()
        wait_for(lambda: not r.p.running, 10, "the service to end on its own")
        assert r.states()[-1] == "error"
        r.mode("ok")
        r.start()                           # pressing Start again works
        r.more_lines(3)
    finally:
        r.close()


def test_no_second_process_means_the_engine_runs_in_the_app():
    sys.path.insert(0, str(HERE))
    from fake_engine import FakeEngine
    r = Rig(inline=FakeEngine)

    def refuse(opts):
        raise OSError("no more processes")
    r.p.spawn_hook = refuse
    try:
        r.start()
        r.more_lines(3)
        assert r.p.inline is not None and r.p.running
        assert r.p.restart() is True
        r.p.stop()
        assert not r.p.running
    finally:
        r.close()


def test_an_engine_process_that_never_comes_up_falls_back_too():
    sys.path.insert(0, str(HERE))
    from fake_engine import FakeEngine
    r = Rig(inline=FakeEngine)
    r.p.ready_within = 1

    def sleeper(opts):
        ctx = mp.get_context("spawn")
        a, b = ctx.Pipe(duplex=True)
        proc = ctx.Process(target=time.sleep, args=(60,), daemon=True)
        proc.start()
        proc.keep_open = b                  # a process that is up but never says it is ready
        return proc, a
    r.p.spawn_hook = sleeper
    try:
        r.start()
        r.more_lines(3, 30)
        assert r.p.inline is not None
    finally:
        r.close()


if __name__ == "__main__":
    mp.freeze_support()
    tests = [(k, v) for k, v in list(globals().items()) if k.startswith("test_")]
    for name, fn in tests:
        t = time.monotonic()
        fn()
        print(f"ok {name} ({time.monotonic() - t:.1f} s)", flush=True)
    print(f"PASS ({len(tests)} checks)")
