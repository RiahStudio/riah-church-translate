"""The speech engine in a process of its own, so the app can never go down with it.

Everything that can hang or crash for real sits in one place: the sound input (PortAudio), Whisper and
the translator (CTranslate2), the voice. A bug there used to take the whole app with it, church screen
included; a call that never comes back can't be stopped from a thread. So the engine runs in a second
process and the app (the windows, the church screen, the lines already on it) stays in this one:

  * The engine process dies, or goes quiet (see engine.STALL_LIVE), or its Restart is ignored: the
    watcher below kills it and starts a new one, which carries on the same service, with the screen's
    lines and numbering untouched. The church screen shows "reconnecting" for a few seconds.
  * Silence and mumbling are not hangs. The engine's live loop turns every fifth of a second whatever the
    sound is; only a loop that stops turning counts.
  * A process that crashes every time it starts is tried again with growing pauses (never more than
    BACKOFF_CAP seconds apart); only Stop ends a service.
  * If a second process can't be started on some computer at all, the engine runs inside the app as it
    always did (the old way), so this can only make things safer.

The two processes talk over one pipe: the engine's events go to the screens, the app's commands (start,
stop, restart, ...) go the other way, and the engine says it is alive twice a second.
"""
from __future__ import annotations

import importlib
import os
import queue
import sys
import threading
import time
import traceback
from collections import deque

from . import report

FACTORY = "riah_translate.engine:Engine"
HB_EVERY = 0.5          # the engine process says it is alive this often (seconds)
HB_LIVE = 10            # a live engine that has said nothing for this long is stuck, whole process
HB_LOAD = 300           # before it is live it may be busy loading models for much longer
READY_WITHIN = 90       # a new engine process has this long to start up
RESTART_WAIT = 4        # the Restart button's quick restart gets this long before a hard one
STOP_WAIT = 6           # Stop gets this long before the engine process is ended
BACKOFF_CAP = 15        # most seconds between starting a failing engine process again
CRASH_WINDOW = 120      # starts closer together than this count towards the backoff
LOG_MAX = 2_000_000


# ============================================================================ the engine's process
def worker_main(conn, opts=None):
    """The engine process. Never returns to the caller: it ends when the app goes away or says quit."""
    try:
        _worker(conn, opts or {})
    except BaseException:                  # EOFError / OSError: the app is gone. Anything else: say so.
        try:
            if not isinstance(sys.exc_info()[1], (EOFError, OSError, SystemExit)):
                traceback.print_exc()
        except Exception:
            pass
    finally:
        for f in (sys.stdout, sys.stderr):
            try:
                f.flush()
            except Exception:
                pass
        os._exit(0)


def _worker(conn, opts):
    for p in opts.get("paths") or []:
        if p not in sys.path:
            sys.path.insert(0, p)
    _redirect_output(opts.get("log"))
    if getattr(sys, "frozen", False):
        from .models import use_app_model_dir
        use_app_model_dir()

    lock = threading.Lock()

    def send(msg):
        try:
            with lock:
                conn.send(msg)
        except Exception:                  # the app has gone: so does this process
            os._exit(0)

    class ChildHub:
        def publish(self, ev):
            send(("ev", ev))

    # The engine reports to report.py as it always did; here those calls travel to the app, which owns the
    # one report (the person's problem list, the on/off switch). Status comes along in the events.
    def forward_event(kind, exc=None, recovered=None, parts=None):
        if exc is not None:
            parts = (report.exc_name(exc), report.frames(exc.__traceback__))
        send(("report", kind, parts, recovered))
    report.event = forward_event
    report.status = lambda state: None

    mod, _, name = (opts.get("factory") or FACTORY).partition(":")
    engine = getattr(importlib.import_module(mod), name)(ChildHub())
    gen = [0]
    send(("ready", os.getpid()))

    def beat():
        while True:
            g = gen[0]                     # read before health(): a Start landing in between must not make an
            try:                           # idle reading look like the new service already ended
                h = engine.health()
            except Exception:
                h = dict(running=False, live=False, restart=False, overdue=0.0)
            send(("hb", g, h))
            time.sleep(HB_EVERY)
    threading.Thread(target=beat, daemon=True).start()

    while True:
        msg = conn.recv()                  # EOFError when the app is gone
        cmd = msg[0]
        try:
            if cmd == "start":
                engine.start(msg[1])
                gen[0] = msg[2]
            elif cmd == "stop":
                engine.stop()
            elif cmd == "restart":
                engine.restart()
            elif cmd == "targets":
                engine.set_targets(msg[1], msg[2])
            elif cmd == "notes":
                engine.set_notes(msg[1])
            elif cmd == "quit":
                return
        except Exception:
            traceback.print_exc()


def _redirect_output(path):
    """The app has no console to show this process's output on (a Windows window app, a Mac app): it goes
    to a file next to the models, small enough to never matter."""
    try:
        if path:
            mode = "w" if os.path.exists(path) and os.path.getsize(path) > LOG_MAX else "a"
            f = open(path, mode, encoding="utf-8", buffering=1)
            sys.stdout = sys.stderr = f
        elif sys.stdout is None or sys.stderr is None:
            f = open(os.devnull, "w")
            sys.stdout = sys.stdout or f
            sys.stderr = sys.stderr or f
    except Exception:
        pass


# ============================================================================ the app's side
class EngineProxy:
    """What the app uses in place of engine.Engine: the same start / stop / restart / running, backed by
    the engine process, which it watches and starts again whenever it is needed."""

    def __init__(self, hub, factory=FACTORY, log=None, spawn=None, inline=None):
        self.hub = hub
        self.opts = dict(factory=factory, log=log, paths=[p for p in sys.path[:1] if p])
        self.spawn_hook = spawn            # tests: a function(opts) -> (process, connection)
        self.inline_factory = inline       # the engine to run inside the app if there can be no second process
        self.hb_live, self.hb_load = HB_LIVE, HB_LOAD
        self.ready_within, self.restart_wait, self.stop_wait = READY_WITHIN, RESTART_WAIT, STOP_WAIT
        self.backoff_cap, self.crash_window = BACKOFF_CAP, CRASH_WINDOW

        self.lock = threading.RLock()
        self.wanted = False                # a service is on: only Stop turns this off (or one that can't start)
        self.closed = False
        self.cfg: dict = {}
        self.notes = None
        self.gen = 0                       # which start the engine process is on (its heartbeat echoes it)
        self.last_line = None              # (seg, id) of the last line shown: a new process carries on from it
        self.restart_at = None
        self.recent = deque()              # when engine processes were started (for the backoff)
        self.never_ready = 0               # engine processes in a row that never came up
        self.inline = None                 # the in-app engine, once there is no second process
        self.proc = self.conn = self.outbox = None
        self.cid = 0                       # which engine process (messages from older ones are ignored)
        self.respawn_due = None
        self.watcher = None
        self._clear_child()

    # ------------------------------------------------------------------ what the app calls
    @property
    def running(self):
        if self.inline is not None:
            return self.inline.running
        return self.wanted

    def warm(self):
        """Gets the engine process up and its libraries loaded before the first Start (it just waits)."""
        with self.lock:
            if self.inline is None and not self.closed and self.proc is None:
                self._begin_watching()
                self._spawn()

    def start(self, cfg):
        with self.lock:
            self.cfg = dict(cfg)
            self.last_line = None
            self.restart_at = None
            self.respawn_due = None
            self.gen += 1
            self.wanted = True
            self._begin_watching()
            if self.inline is not None:
                self.inline.start(self.cfg)
                return
            if not self._alive() and not self._spawn():
                return self._go_inline()
            self._send(("start", self.cfg, self.gen))
            self._send(("notes", self.notes))

    def stop(self):
        """Back to idle, quickly: the engine process is asked to stop and given STOP_WAIT seconds; one
        that is stuck is ended (a new one waits, ready, for the next Start)."""
        with self.lock:
            self.wanted = False
            self.restart_at = None
            self.respawn_due = None
            inline = self.inline
            if inline is None:
                asked = time.monotonic()
                self.gen += 1                              # heartbeats from before this are not about now
                if self._alive():
                    self._send(("stop",))
                else:
                    # Between engine processes (one had just stopped and the next was due): there is no
                    # engine to say "stopped", so say it here, or the page would stay on "reconnecting".
                    self._say("stopped", "Stopped.")
                    self._warm_again()
                    return
        if inline is not None:
            return inline.stop()
        end = asked + self.stop_wait
        while time.monotonic() < end:
            with self.lock:
                if not self._alive() or (self.hb_at > asked + 0.1 and not self.hb["running"]):
                    return
            time.sleep(0.05)
        with self.lock:
            if self._alive() and self.hb.get("running"):
                self._kill()
                self._say("stopped", "Stopped.")
                self._warm_again()

    def restart(self):
        """The sound person's Restart: the engine restarts its hearing at once (about a second, the screen
        keeps its lines). If the engine does not take it up, the watcher starts a new engine process."""
        with self.lock:
            if self.inline is not None:
                return self.inline.restart()
            if self.wanted and self.respawn_due is not None:
                self.respawn_due = time.monotonic()  # in the pause before a new engine process: try now
                return True
            if not (self.wanted and self._alive()):
                return False                       # (the engine itself ignores it if it isn't live yet)
            self.restart_at = time.monotonic()
            self._send(("restart",))
            return True

    def set_targets(self, src, tgts):
        with self.lock:
            if self.inline is not None:
                return self.inline.set_targets(src, tgts)
            self.cfg["tgts"] = list(tgts)
            if self._alive():
                self._send(("targets", src, list(tgts)))

    def set_notes(self, notes):
        with self.lock:
            self.notes = notes
            if self.inline is not None:
                return self.inline.set_notes(notes)
            if self._alive():
                self._send(("notes", notes))

    def close(self):
        """The app is quitting."""
        with self.lock:
            self.closed = True
            self.wanted = False
            if self.inline is not None:
                return self.inline.stop()
            self._send(("quit",))
            self._kill(grace=0.5)

    # ------------------------------------------------------------------ the engine process
    def _clear_child(self):
        self.ready = False
        self.dead = False
        self.hb = dict(running=False, live=False, restart=False, overdue=0.0)
        self.hb_at = 0.0
        self.hb_gen = -1
        self.spawned_at = time.monotonic()

    def _alive(self):
        return self.proc is not None and not self.dead and self.proc.is_alive()

    def _spawn(self):
        """Starts an engine process. False when this computer can't (then the engine runs in the app)."""
        self._kill()
        try:
            if self.spawn_hook:
                proc, conn = self.spawn_hook(self.opts)
            else:
                import multiprocessing as mp
                ctx = mp.get_context("spawn")
                conn, child_conn = ctx.Pipe(duplex=True)
                proc = ctx.Process(target=worker_main, args=(child_conn, self.opts), daemon=True)
                proc.start()
                child_conn.close()
        except Exception:
            traceback.print_exc()
            return False
        self.cid += 1
        self.proc, self.conn = proc, conn
        self._clear_child()
        now = time.monotonic()
        self.recent.append(now)
        while self.recent and now - self.recent[0] > self.crash_window:
            self.recent.popleft()
        self.outbox = queue.Queue()
        threading.Thread(target=self._sender, args=(self.cid, conn, self.outbox), daemon=True).start()
        threading.Thread(target=self._reader, args=(self.cid, conn), daemon=True).start()
        return True

    def _kill(self, grace=0.0):
        proc, conn = self.proc, self.conn
        self.proc = self.conn = None
        self.cid += 1                      # whatever the old process says from now on is ignored
        if self.outbox is not None:
            self.outbox.put(None)
            self.outbox = None
        if proc is not None:
            try:
                if grace and proc.is_alive():
                    proc.join(grace)
                if proc.is_alive():
                    proc.kill()
                proc.join(2)
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        self._clear_child()
        self.dead = True

    def _send(self, msg):
        box = self.outbox
        if box is not None:
            box.put(msg)

    def _sender(self, cid, conn, box):
        """Commands go out from a thread of their own: one that waits on a stuck engine can't hold up the watcher."""
        while True:
            msg = box.get()
            if msg is None:
                return
            try:
                conn.send(msg)
            except Exception:
                with self.lock:
                    if cid == self.cid:
                        self.dead = True
                return

    def _reader(self, cid, conn):
        while True:
            try:
                msg = conn.recv()
            except Exception:
                break
            with self.lock:
                if cid != self.cid:
                    return
                kind = msg[0]
                if kind == "hb":
                    self.hb_at, self.hb_gen, self.hb = time.monotonic(), msg[1], msg[2]
                    continue
                if kind == "ready":
                    self.ready, self.never_ready = True, 0
                    self.hb_at = time.monotonic()
                    continue
            if kind == "ev":
                self._event(msg[1])
            elif kind == "report":
                report.event(msg[1], None, msg[3], msg[2])
        with self.lock:
            if cid == self.cid:
                self.dead = True

    def _event(self, ev):
        try:
            if ev.get("type") == "line":
                self.last_line = (ev.get("seg"), ev.get("id"))
            elif ev.get("type") == "status":
                report.status(ev.get("state", ""))
            self.hub.publish(ev)
        except Exception:
            traceback.print_exc()

    def _say(self, state, detail):
        self.hub.publish(dict(type="status", state=state, detail=detail))
        report.status(state)

    def _warm_again(self):
        if not self.closed and self.inline is None:
            self._spawn()

    def _go_inline(self):
        """No second process on this computer: the engine runs in the app (as before this existed)."""
        from .engine import Engine
        print("Can't start a separate engine process; running the engine inside the app.", flush=True)
        self._kill()
        self.inline = (self.inline_factory or Engine)(self.hub)
        self.inline.set_notes(self.notes) if self.notes else None
        if self.wanted:
            self.inline.start(self.cfg)

    # ------------------------------------------------------------------ the watcher
    def _begin_watching(self):
        if self.watcher is None:
            self.watcher = threading.Thread(target=self._watch, daemon=True)
            self.watcher.start()

    def _watch(self):
        while not self.closed:
            time.sleep(0.25)
            try:
                with self.lock:
                    self._tick()
            except Exception:
                traceback.print_exc()

    def _tick(self):
        if self.inline is not None or self.closed:
            return
        now = time.monotonic()
        if not self.wanted:
            return
        if self.respawn_due is not None:               # waiting out the pause before a new engine process
            if now < self.respawn_due:
                return
            self.respawn_due = None
            if not self._spawn():
                return self._go_inline()
            self._resume()
            return
        why = None
        hb, ready = self.hb, self.ready
        if self.proc is None or not self._alive():
            why = "EngineExited"
        elif not ready:
            if now - self.spawned_at > self.ready_within:
                why = "EngineNeverStarted"
        elif now - self.hb_at > (self.hb_live if hb.get("live") else self.hb_load):
            why = "EngineStuck"
        elif hb.get("running") and hb.get("overdue", 0) > 0:
            why = "EngineStalled"
        elif self.restart_at is not None and now - self.restart_at > self.restart_wait:
            if self.hb_at > self.restart_at + 1.0 and hb.get("restart"):
                why = "EngineIgnoredRestart"
            elif self.hb_at > self.restart_at + 1.0:
                self.restart_at = None
        if why is None and self.hb_gen == self.gen and not hb.get("running") and ready:
            self.wanted = False                        # the service ended on its own (it said why on screen)
            return
        if why is not None:
            self._recover(why, now)

    def _recover(self, why, now):
        """The engine process is gone or stuck: end it, start another, carry on the same service."""
        print(f"[engine process] {why}: starting it again", flush=True)
        if why == "EngineNeverStarted":
            self.never_ready += 1
        self._kill()
        report.event("restart", None, False, (why, []))
        self._say("reconnecting", "The speech engine stopped. Starting it again…")
        while self.recent and now - self.recent[0] > self.crash_window:
            self.recent.popleft()
        n = len(self.recent)
        self.respawn_due = now + (0.3 if n < 2 else min(2 ** (n - 2), self.backoff_cap))
        if self.never_ready >= 2:                      # twice in a row nothing came up: don't keep waiting for it
            self._go_inline()
            self.respawn_due = None

    def _resume(self):
        """A new engine process carries on the service the last one was running."""
        cfg = dict(self.cfg)
        if self.last_line is not None and self.last_line[0] is not None:
            cfg["resume"] = dict(seg=self.last_line[0] + 1, next_id=self.last_line[1])
        self.gen += 1
        self.restart_at = None
        self._send(("start", cfg, self.gen))
        if self.notes:
            self._send(("notes", self.notes))


def make_engine(hub, isolate=True, log=None):
    """The engine the app runs: in its own process (best), or inside the app if asked or if need be."""
    if isolate:
        return EngineProxy(hub, log=log)
    from .engine import Engine
    return Engine(hub)
