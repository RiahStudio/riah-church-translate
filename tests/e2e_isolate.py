"""The REAL engine through the engine-process split, end to end (heavy: speech models; run it on the
Thinkbox, not the PC):

    node "_shared-agent-rules/bin/thinkbox-run.mjs" --bg rt-e2e --cwd "Riah Translate" -- \
        bash -c '$HOME/venvs/riah-translate/bin/python tests/e2e_isolate.py'

Plays tests/clips/sermon-en.wav in a loop as if live, then (1) kills the engine process outright,
(2) freezes it (a hang), (3) presses Restart, (4) stops. After each the lines must keep coming on the
same screen. Linux/macOS only (uses signals). Nothing is ever played out loud."""
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("E2E_PORT", "5391"))
BASE = f"http://127.0.0.1:{PORT}"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=5) as r:
        return json.loads(r.read())


def post(path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"{}")


def lines():
    return len(get("/api/state").get("lines", []))


def wait_for(cond, secs, what):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        try:
            if cond():
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise AssertionError(f"timed out after {secs} s waiting for {what}")


def children(pid):
    out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout.split()
    return [int(x) for x in out]


def engine_pid(app):
    """The speech engine is the app's child that holds the memory (the models); the multiprocessing
    resource tracker is a tiny sibling, so pick the biggest."""
    best, size = None, 0
    for c in children(app.pid):
        try:
            rss = int(Path(f"/proc/{c}/statm").read_text().split()[1])
        except OSError:
            continue
        if rss > size:
            best, size = c, rss
    return best


def more_lines(n, secs, what):
    have = lines()
    wait_for(lambda: lines() >= have + n, secs, f"{n} {what}")


def main():
    app = subprocess.Popen([sys.executable, "-m", "riah_translate", "--file", str(ROOT / "tests/clips/sermon-en.wav"),
                            "--loop", "--autostart", "--ui", "none", "--port", str(PORT), "--to", "th"],
                           cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        wait_for(lambda: get("/api/state").get("running"), 300, "the service to start")
        more_lines(3, 300, "first lines (models loading)")
        eng = engine_pid(app)
        assert eng, "found no engine process: the engine is not running apart from the app"
        print(f"ok first lines come through; engine process {eng}, app {app.pid}", flush=True)

        os.kill(eng, signal.SIGKILL)
        t = time.monotonic()
        wait_for(lambda: (engine_pid(app) or eng) != eng, 60, "a new engine process")
        more_lines(3, 300, "lines after a crash")
        assert get("/api/state").get("running"), "the service must still be running"
        print(f"ok killed the engine; a new one carried on (back in {time.monotonic() - t:.0f} s)", flush=True)

        eng = engine_pid(app)
        os.kill(eng, signal.SIGSTOP)          # a hang: alive, answering nothing
        t = time.monotonic()
        wait_for(lambda: (engine_pid(app) or eng) != eng, 150, "the frozen engine to be replaced")
        more_lines(3, 300, "lines after a hang")
        print(f"ok froze the engine; replaced and carried on (after {time.monotonic() - t:.0f} s)", flush=True)

        eng = engine_pid(app)
        t = time.monotonic()
        post("/api/restart")
        more_lines(3, 120, "lines after Restart")
        print(f"ok Restart; lines flowing again after {time.monotonic() - t:.0f} s, "
              f"same engine={engine_pid(app) == eng}", flush=True)

        segs = [l.get("seg") for l in get("/api/state")["lines"] if "seg" in l]
        assert segs == sorted(segs) and len(set(segs)) == len(segs), "line numbers repeat or go backwards"

        t = time.monotonic()
        post("/api/stop")
        wait_for(lambda: not get("/api/state").get("running"), 15, "Stop")
        print(f"ok Stop took {time.monotonic() - t:.1f} s", flush=True)
        print("PASS")
    finally:
        app.terminate()
        try:
            app.wait(15)
        except subprocess.TimeoutExpired:
            app.kill()
        left = children(app.pid) if app.poll() is None else []
        tail = (app.stdout.read() if app.stdout else "")[-1500:]
        print("--- app output (tail) ---\n" + tail)


if __name__ == "__main__":
    main()
