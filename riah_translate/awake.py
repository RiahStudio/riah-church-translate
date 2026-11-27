"""Keeps the church computer's display from sleeping while subtitles are running.

The church screen page also asks its browser for a wake lock, but the app's own window can't
always get one, and a projector going dark mid-sermon is the worst way to fail. So while the
service is live and "Keep the screen from sleeping" is ticked: on macOS we run Apple's `caffeinate`
tool, which stops by itself when the app quits; on Windows we tell the system the display is in use
(SetThreadExecutionState), which also ends when the app quits. Nothing happens on Linux.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading


class KeepAwake:
    def __init__(self, wanted, every: float = 5.0):
        """wanted(): should the display stay awake right now?"""
        self.wanted, self.every = wanted, every
        self.proc = None
        self.held = False                  # Windows: display request in force
        self._stop = threading.Event()

    def start(self):
        if sys.platform in ("darwin", "win32"):
            threading.Thread(target=self._run, daemon=True, name="keep-awake").start()
        return self

    def _run(self):
        while not self._stop.wait(self.every):
            try:
                self._apply(bool(self.wanted()))
            except Exception:
                pass
        self._apply(False)

    def _apply(self, on: bool):
        if sys.platform == "win32":
            # Called only from the keep-awake thread: the request belongs to the thread that made it.
            if on != self.held:
                import ctypes
                ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x1, 0x2
                flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED if on else 0)
                ctypes.windll.kernel32.SetThreadExecutionState(ctypes.c_uint(flags))
                self.held = on
            return
        alive = self.proc is not None and self.proc.poll() is None
        if on and not alive:
            # -d display, -i system idle; -w ends it when this app's process ends.
            self.proc = subprocess.Popen(["caffeinate", "-d", "-i", "-w", str(os.getpid())],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif not on and alive:
            self.proc.terminate()
            self.proc = None

    def close(self):
        self._stop.set()
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
