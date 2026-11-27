"""Local web server: the operator page (/), the church screen (/screen), and the live event stream.

Only this computer can change anything. With --lan, other devices on the church Wi-Fi may open the
screen (/screen) read-only, e.g. a second computer at the projector or phones.
"""
from __future__ import annotations

import json
import mimetypes
import queue
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__
from .langs import public_list

WEB = Path(__file__).with_name("web")
LOCAL = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
LOCAL_NAMES = {"127.0.0.1", "localhost", "::1"}
MAX_BODY = 64 * 1024
MAX_NOTES = 36 * 1024 * 1024     # sermon notes up to 25 MB, sent as base64 text
# Pages only load from this app, and no other site can frame the operator page.
CSP = ("default-src 'self'; img-src 'self' data:; font-src 'self' data:; frame-ancestors 'self'; "
       "base-uri 'none'; form-action 'none'")


class Api:
    """What the HTTP layer may ask the app to do. Implemented in app.py."""

    def state(self): ...
    def devices(self): ...
    def start(self, body): ...
    def stop(self): ...
    def clear(self): ...
    def display(self, body): ...
    def glossary(self, body): ...
    def langs(self, body): ...
    def open_screen(self): ...
    def open_transcripts(self): ...
    def notes(self, body): ...
    def reports(self, body): ...
    def problem_report(self): ...
    def send_problem_report(self, body): ...
    def show_problem_file(self): ...


def make_handler(hub, api: Api):
    _sub_lock = threading.Lock()
    _subscribers = {}
    _sub_total = 0

    class Handler(BaseHTTPRequestHandler):
        server_version = f"RiahTranslate/{__version__}"
        timeout = 20                 # a stuck or silent connection lets go of its thread

        def log_message(self, fmt, *args):  # keep the console quiet
            pass

        # -------------------------------------------------------------- helpers
        def _send(self, code, body=b"", ctype="application/json; charset=utf-8", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if ctype.startswith("text/html"):
                self.send_header("Content-Security-Policy", CSP)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode())

        def _file(self, rel):
            p = (WEB / rel).resolve()
            if WEB.resolve() not in p.parents or not p.is_file():
                return self._send(404, b"not found", "text/plain")
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript",):
                ctype += "; charset=utf-8"
            self._send(200, p.read_bytes(), ctype)

        def _local(self):
            return self.client_address[0] in LOCAL

        @staticmethod
        def _hostname(value):
            v = (value or "").strip().lower()
            if "://" in v:
                v = v.split("://", 1)[1].split("/", 1)[0]
            if v.startswith("["):
                return v[1:].split("]", 1)[0]
            return v.rsplit(":", 1)[0] if v.count(":") == 1 else v

        @staticmethod
        def _host_ok(value):
            """A non-loopback client must address this computer by IP, .local name, or hostname."""
            host = Handler._hostname(value).lower()
            if not host:
                return False
            if host.endswith(".local"):
                return True
            if host == socket.gethostname().lower():
                return True
            try:
                socket.inet_aton(host)
                return True
            except OSError:
                pass
            try:
                socket.inet_pton(socket.AF_INET6, host)
                return True
            except OSError:
                pass
            return False

        def _foreign(self):
            """A web page from another site reaching this app through the operator's own browser
            (by a link, a form, or DNS rebinding). The operator page itself is always on 127.0.0.1."""
            if not self._local():
                # Another device on the church wifi (--lan) must name this computer by its address,
                # its .local name or its own name: a rebinding site's name is turned away.
                return not self._host_ok(self.headers.get("Host"))
            if self._hostname(self.headers.get("Host")) not in LOCAL_NAMES:
                return True
            origin = self.headers.get("Origin")
            return bool(origin) and origin != "null" and self._hostname(origin) not in LOCAL_NAMES

        # -------------------------------------------------------------- routes
        def do_GET(self):
            if self._foreign():
                return self._send(403, b"forbidden", "text/plain")
            path = self.path.split("?")[0]
            if path in ("/", "/control"):
                return self._file("control.html") if self._local() else self._file("screen.html")
            if path == "/screen":
                return self._file("screen.html")
            if path == "/events":
                return self._events()
            if path == "/api/state":
                return self._json(api.state() if self._local() else hub.snapshot())
            if path == "/api/langs":
                return self._json(public_list())
            if path == "/api/devices":
                return self._json(api.devices() if self._local() else [])
            if path == "/api/outputs":
                return self._json(api.outputs() if self._local() else [])
            if path == "/api/voices":
                return self._json(api.voices() if self._local() else dict(available=False, voices={}))
            if path == "/api/problem-report":
                if not self._local():
                    return self._send(404, b"not found", "text/plain")
                return self._json(api.problem_report())
            # page assets (icon, css, js, vendor/...); /static/ is an older alias
            rel = path[len("/static/"):] if path.startswith("/static/") else path.lstrip("/")
            if rel.startswith("control") and not self._local():
                return self._send(404, b"not found", "text/plain")
            return self._file(rel)

        do_HEAD = do_GET

        def do_POST(self):
            if not self._local():
                return self._json(dict(error="Only the computer running Riah Church Translate can change it."), 403)
            # Other sites can't send JSON here without asking first (CORS), and they never get a yes.
            if self._foreign() or not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self._json(dict(error="forbidden"), 403)
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._json(dict(error="bad length"), 400)
            path = self.path.split("?")[0]
            if n < 0 or n > (MAX_NOTES if path == "/api/notes" else MAX_BODY):
                return self._json(dict(error="too large"), 413)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except (json.JSONDecodeError, UnicodeDecodeError):
                return self._json(dict(error="bad json"), 400)
            if not isinstance(body, dict):
                return self._json(dict(error="bad json"), 400)
            routes = {
                "/api/start": lambda: api.start(body),
                "/api/stop": api.stop,
                "/api/restart": api.restart,
                "/api/clear": api.clear,
                "/api/display": lambda: api.display(body),
                "/api/glossary": lambda: api.glossary(body),
                "/api/langs": lambda: api.langs(body),
                "/api/open-screen": api.open_screen,
                "/api/open-transcripts": api.open_transcripts,
                "/api/notes": lambda: api.notes(body),
                "/api/reports": lambda: api.reports(body),
                "/api/problem-report": lambda: api.send_problem_report(body),
                "/api/show-problem-file": api.show_problem_file,
                "/api/voice-test": lambda: api.voice_test(body),
            }
            fn = routes.get(path)
            if not fn:
                return self._send(404, b"not found", "text/plain")
            try:
                return self._json(fn() or dict(ok=True))
            except Exception as e:
                return self._json(dict(error=str(e)), 500)

        def _events(self):
            nonlocal _sub_total
            ip = self.client_address[0]
            other = ip not in LOCAL         # this computer's own pages are never turned away
            with _sub_lock:
                if other and (_subscribers.get(ip, 0) >= 10 or _sub_total >= 100):
                    return self._send(503, b"too many screens", "text/plain")
                if other:
                    _subscribers[ip] = _subscribers.get(ip, 0) + 1
                    _sub_total += 1
            q = None
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                q = hub.subscribe()
                self.wfile.write(b"retry: 1500\n\n")
                while hub.has(q):   # dropped for falling behind: end, so the screen reconnects fresh
                    try:
                        ev = q.get(timeout=15)   # a ping every 15 s, inside the 20 s socket timeout
                        self.wfile.write(b"data: " + json.dumps(ev, ensure_ascii=False).encode() + b"\n\n")
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass
            finally:
                if q is not None:
                    hub.unsubscribe(q)
                if other:
                    with _sub_lock:
                        _subscribers[ip] -= 1
                        if _subscribers[ip] <= 0:
                            del _subscribers[ip]
                        _sub_total -= 1

    return Handler


def serve(hub, api, host="127.0.0.1", port=8765, tries=20):
    """Starts the web server on `port`, or the next free one if another program has it."""
    for p in range(port, port + tries):
        try:
            httpd = ThreadingHTTPServer((host, p), make_handler(hub, api))
            break
        except OSError:
            if p == port + tries - 1:
                raise
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd
