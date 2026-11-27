"""A stand-in for engine.Engine that can be told, through a file, to misbehave the way the real one could:
crash, hang, ignore Restart, or say nothing for ever (silence is not a fault). Used by test_isolate.py.
Runs inside the engine process, so it only talks to the app through hub.publish, like the real one."""
import os
import threading
import time

STALL = 1.5      # the fake's own deadline for its loop to turn (the real one is engine.STALL_LIVE)


class FakeEngine:
    def __init__(self, hub):
        self.hub = hub
        self.thread = None
        self.stop_flag = threading.Event()
        self.restart_flag = threading.Event()
        self._live = False
        self._deadline = 0.0
        self.seg = 0
        self.next_id = 0
        self.cfg = {}
        self.restarts = 0

    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def progress(self):
        self._deadline = time.monotonic() + STALL

    def health(self):
        return dict(running=self.running, live=self._live, restart=self.restart_flag.is_set(),
                    overdue=round(max(0.0, time.monotonic() - self._deadline), 1) if self.running else 0.0)

    def mode(self):
        try:
            with open(self.cfg["fake"]["ctl"], encoding="utf-8") as f:
                return f.read().strip() or "ok"
        except OSError:
            return "ok"

    def start(self, cfg):
        self.stop()
        self.stop_flag.clear()
        self.restart_flag.clear()
        self.cfg = cfg
        if self.mode() == "crash_on_start":
            os._exit(3)
        self.progress()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        if self.running:
            self.stop_flag.set()
            self.thread.join(15)

    def restart(self):
        if not (self.running and self._live):
            return False
        self.restart_flag.set()
        return True

    def set_targets(self, src, tgts):
        self.hub.publish(dict(type="langs", src=src, tgts=tgts))

    def set_notes(self, notes):
        pass

    def _run(self):
        resume = self.cfg.get("resume") or {}
        self.seg = int(resume.get("seg", 0))
        self.next_id = int(resume.get("next_id", 1000))
        self.hub.publish(dict(type="status", state="listening", detail="Listening."))
        self._live = True
        last = 0.0
        while not self.stop_flag.is_set():
            mode = self.mode()
            if mode == "crash":
                os._exit(1)
            if mode == "hang":
                threading.Event().wait()            # a native call that never comes back
            if mode == "end":
                self.hub.publish(dict(type="status", state="error", detail="Couldn't start."))
                self._live = False
                return
            self.progress()
            if self.restart_flag.is_set() and mode != "ignore_restart":
                self.restart_flag.clear()
                self.restarts += 1
                self.hub.publish(dict(type="status", state="listening", detail="Restarted."))
            if mode != "silence" and time.monotonic() - last > 0.15:
                last = time.monotonic()
                self.next_id += 1
                self.hub.publish(dict(type="line", id=self.next_id, seg=self.seg, src=f"line {self.seg}", lag=0.1,
                                      keepDraft=False))
                self.seg += 1
            self.stop_flag.wait(0.05)
        self.hub.publish(dict(type="status", state="stopped", detail="Stopped."))
