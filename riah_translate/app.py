"""Riah Church Translate: live sermon subtitles, original and translation side by side on the church screen.

    python -m riah_translate                      # operator page + church screen
    python -m riah_translate --from en --to th    # pick the language pair
    python -m riah_translate --file sermon.mp3    # rehearse with a recording, played as if live
    python -m riah_translate --offline clip.wav --out events.jsonl   # test run, no screen
    python -m riah_translate --replay events.jsonl                    # show a recorded run on screen
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import webbrowser

from . import __version__, report
from .awake import KeepAwake
from .engine import Engine, be_gentle, run_offline
from .church_words import church_glossary
from .hub import Hub
from .langs import LANGS
from .models import app_data_dir, use_app_model_dir
from .server import Api, serve
from .transcript import Transcript

SETTINGS_DEFAULTS = dict(src="en", tgts=["th"], input=None, channel=None, mt="nllb", model=None, beam=1, churchWords=True,
                         llmModel="qwen3:8b", llmUrl="http://127.0.0.1:11434", voice={},
                         crashReports=False)    # off until someone ticks it on this computer (report.py)
DISPLAY_KEYS = {"fontScale", "swap", "labels", "visibleRows", "wakeLock", "showSrc", "hidden", "layout"}
# Where the subtitles go on the church screen: all of it, a bar along the bottom over the slides, or a
# column on the right beside them. The bar and column are their own small window on top of the slideshow.
LAYOUTS = ("full", "bar", "side")
BAR, SIDE = 0.28, 0.30      # how much of the church screen the bar (height) and column (width) take; control.js too
MAX_TGTS = 2


def clean_tgts(src, tgts) -> list[str]:
    """Up to two translation languages, known, not the spoken one, no repeats."""
    if isinstance(tgts, str):
        tgts = [tgts]
    out = []
    for t in tgts or []:
        if t not in LANGS:
            raise ValueError("Unknown language")
        if t != src and t not in out:
            out.append(t)
    return out[:MAX_TGTS]


def _channel_number(text):
    """--channel counts from 1, the way the sound board labels them."""
    n = int(text)
    if not 1 <= n <= 64:
        raise argparse.ArgumentTypeError("channels count from 1 (the first) up to 64")
    return n


def clean_channel(c):
    """One input channel (0 = the first), or None for both channels of a stereo feed together."""
    if c is None or isinstance(c, bool):
        return None
    try:
        c = int(c)
    except (TypeError, ValueError):
        return None
    return c if 0 <= c < 64 else None


def _lan_ip() -> str:
    """This computer's address on the church network (no packets are sent)."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class Settings:
    def __init__(self):
        self.path = app_data_dir() / "settings.json"
        self.gpath = app_data_dir() / "glossary.json"
        self.data = dict(SETTINGS_DEFAULTS, display={})
        self.glossary = dict(hints="", fix={})
        for p, target in ((self.path, self.data), (self.gpath, self.glossary)):
            try:
                target.update(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                pass
        old = self.data.pop("tgt", None)            # settings from 0.1 had one translation language
        if old and self.data["tgts"] == SETTINGS_DEFAULTS["tgts"]:
            self.data["tgts"] = [old]

    def save(self):
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.gpath.write_text(json.dumps(self.glossary, ensure_ascii=False, indent=2), encoding="utf-8")


class App(Api):
    def __init__(self, args):
        self.args = args
        self.settings = Settings()
        s = self.settings.data
        for k, a in (("src", args.src), ("tgts", args.tgt), ("model", args.model), ("mt", args.mt), ("input", args.input)):
            if a is not None:
                s[k] = a
        if args.channel is not None:
            s["channel"] = args.channel - 1
        s["channel"] = clean_channel(s.get("channel"))
        s["tgts"] = clean_tgts(s["src"], s["tgts"])
        # On the church computer (Mac or PC) the display stays awake while live unless that's switched off.
        display = dict(dict(wakeLock=sys.platform in ("darwin", "win32")), **(s.get("display") or {}))
        self.hub = Hub(display=display, langs=dict(src=s["src"], tgts=s["tgts"]))
        self.engine = Engine(self.hub)
        self.transcript = Transcript(app_data_dir() / "Transcripts")
        self.hub.taps.append(self.transcript.event)
        self.awake = KeepAwake(lambda: self.engine.running and self.hub.display.get("wakeLock")).start()
        self.open_screen_hook = None
        self.relayout_hook = None
        self.notes_info = None         # the sermon notes in use: file name and what was found, never the text
        report.switch(s.get("crashReports") is True, app_data_dir())

    # ---------------------------------------------------------------- Api
    def state(self):
        snap = self.hub.snapshot()
        snap.update(settings={k: v for k, v in self.settings.data.items() if k != "display"},
                    glossary=self.settings.glossary, running=self.engine.running, version=__version__,
                    canOpenWindow=self.open_screen_hook is not None, notes=self.notes_info,
                    lanUrl=f"http://{_lan_ip()}:{self.args.port}/screen" if self.args.lan else None)
        return snap

    def devices(self):
        from .audio import list_input_devices
        return list_input_devices()

    def outputs(self):
        """The sound outputs a language can be spoken into (the headphone system's channels)."""
        from .voice import list_outputs
        return list_outputs()

    def voices(self):
        """Which languages have a voice installed on this computer, and which voices they have."""
        from .voice import available, list_voices
        return dict(available=available(), voices=list_voices())

    def voice_test(self, body):
        from .voice import test_voice
        lang = body.get("lang") or "th"
        dev = body.get("device")
        return test_voice(lang, body.get("voice"), None if dev in (None, "") else int(dev),
                          body.get("text"))

    def _glossary(self):
        """The operator's words, plus the built-in church words unless they're switched off."""
        mine = self.settings.glossary
        if not self.settings.data.get("churchWords", True):
            return mine
        return church_glossary(mine)

    def start(self, body):
        s = self.settings.data
        for k in ("src", "tgts", "input", "channel", "mt", "model", "llmModel", "llmUrl", "churchWords", "voice"):
            if k in body:
                s[k] = body[k]
        s["channel"] = clean_channel(s.get("channel"))
        if not str(s.get("llmUrl") or "http://").lower().startswith(("http://", "https://")):
            raise ValueError("The local AI address must start with http:// or https://")
        if s["src"] not in LANGS:
            raise ValueError("Unknown language")
        s["tgts"] = clean_tgts(s["src"], s["tgts"])
        self.settings.save()
        self.hub.publish(dict(type="langs", src=s["src"], tgts=s["tgts"]))
        cfg = dict(s, glossary=self._glossary(), device=self.args.device, file=self.args.file)
        self.engine.start(cfg)
        try:                              # the record is a bonus: it never stops the subtitles
            self.transcript.begin(s["src"], s["tgts"])
        except OSError as e:
            print(f"Transcript not saved: {e}", flush=True)
        return dict(ok=True)

    def langs(self, body):
        """Change which translations show, also mid-service (the spoken language needs a restart)."""
        s = self.settings.data
        if body.get("src") in LANGS and not self.engine.running:
            s["src"] = body["src"]
        s["tgts"] = clean_tgts(s["src"], body.get("tgts", s["tgts"]))
        self.settings.save()
        self.hub.publish(dict(type="langs", src=s["src"], tgts=s["tgts"]))
        self.engine.set_targets(s["src"], s["tgts"])
        return dict(ok=True, tgts=s["tgts"])

    def stop(self):
        threading.Thread(target=self.engine.stop, daemon=True).start()
        return dict(ok=True)

    def restart(self):
        """The sound person's Restart: a fresh sound input and hearing in about a second. The
        screen keeps its lines."""
        return dict(ok=self.engine.restart())

    def clear(self):
        self.hub.publish(dict(type="clear"))

    def reports(self, body):
        """Crash reports on or off for this computer, at once (not on the next Start)."""
        on = body.get("on") is True
        self.settings.data["crashReports"] = on
        self.settings.save()
        report.switch(on, app_data_dir())
        return dict(ok=True, on=on)

    def problem_report(self):
        """What "Send a problem report" holds, in plain words, for the person to read first."""
        return report.problem_report()

    def send_problem_report(self, body=None):
        """The person's one press: sent to the studio, or saved as a file to email or message."""
        return report.send_problem()

    def show_problem_file(self):
        """Shows the saved problem report in Finder / Explorer, picked out."""
        import os
        import subprocess
        path = report.last_file()
        if path is None or not path.is_file():
            raise ValueError("There's no saved problem report to show yet.")
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(path)])
            elif os.name == "nt":
                subprocess.Popen(f'explorer /select,"{path}"')      # one argument, or Explorer ignores it
            else:
                subprocess.Popen(["xdg-open", str(path.parent)])
        except OSError:
            raise ValueError(f"Couldn't open the folder. The report is at {path}") from None
        return dict(ok=True)

    def display(self, body):
        d = {k: v for k, v in body.items() if k in DISPLAY_KEYS}
        if "layout" in d and d["layout"] not in LAYOUTS:
            del d["layout"]
        moved = "layout" in d and d["layout"] != (self.hub.display.get("layout") or "full")
        # "Hidden" lasts for this run only, so the app never opens with a dark screen.
        self.settings.data.setdefault("display", {}).update({k: v for k, v in d.items() if k != "hidden"})
        self.settings.save()
        self.hub.publish(dict(type="display", display=d))
        if moved and self.relayout_hook:
            self.relayout_hook(d["layout"])

    def notes(self, body):
        """Sermon notes, sent as the file itself. Only what helps the listening is kept (the names to
        listen for and the Bible passages to expect), in memory, for this run of the app. The notes'
        text is never shown, saved or logged."""
        if body.get("clear"):
            self.notes_info = None
            self.engine.set_notes(None)
            return dict(ok=True, notes=None)
        import base64
        import binascii
        from .notes import read_notes
        name = str(body.get("name") or "notes.txt").replace("\\", "/").rsplit("/", 1)[-1][:120]
        try:
            data = base64.b64decode(str(body.get("data") or ""), validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("That file didn't come through whole. Try adding it again.") from None
        got = read_notes(name, data)
        self.engine.set_notes(dict(hints=got["hints"], refs=list(got["refs"])))
        self.notes_info = dict(name=name, names=len([h for h in got["hints"].split(",") if h.strip()]),
                               refs=len(got["refs"]))
        return dict(ok=True, notes=self.notes_info)

    def glossary(self, body):
        g = self.settings.glossary
        if "hints" in body:
            g["hints"] = str(body["hints"])[:2000]
        if "fix" in body and isinstance(body["fix"], dict):
            g["fix"] = body["fix"]
        self.settings.save()

    def open_transcripts(self):
        """Shows the folder of saved service transcripts in Finder / Explorer."""
        import os
        import subprocess
        folder = self.transcript.folder
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            elif os.name == "nt":
                os.startfile(str(folder))
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except OSError:
            raise ValueError(f"Couldn't open the transcripts folder. It's at {folder}") from None
        return dict(ok=True, folder=str(folder))

    def open_screen(self):
        layout = self.hub.display.get("layout") or "full"
        if self.open_screen_hook:
            self.open_screen_hook(layout)
            return dict(ok=True, opened="window")
        return dict(ok=True, opened="browser", layout=layout)


def replay(hub, path, speed=1.0, loop=False):
    """Plays a recorded run (events.jsonl from --offline) into the screens with its real timing."""
    events = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    summary = next((e for e in events if e.get("type") == "summary" and e.get("tgts")), None)
    while True:
        hub.publish(dict(type="clear"))
        if summary:                       # show the languages the run was recorded with
            hub.publish(dict(type="langs", src=summary.get("src", hub.langs["src"]), tgts=summary["tgts"]))
        hub.publish(dict(type="status", state="listening", detail="Replaying a recorded run."))
        timed = sorted((e for e in events if "at" in e), key=lambda e: e["at"])
        t0 = time.monotonic()
        for e in timed:
            delay = e["at"] / speed - (time.monotonic() - t0)
            if delay > 0:
                time.sleep(delay)
            hub.publish({k: v for k, v in e.items() if k not in ("at",)})
        hub.publish(dict(type="partial", text="", seg=None))
        hub.publish(dict(type="status", state="stopped", detail="Replay finished."))
        if not loop:
            return
        time.sleep(4)


def parse(argv=None):
    p = argparse.ArgumentParser(prog="riah-translate", description="Live sermon subtitles, side by side.")
    p.add_argument("--from", dest="src", choices=sorted(LANGS), help="language being spoken (default en)")
    p.add_argument("--to", dest="tgt", nargs="+", choices=sorted(LANGS), metavar="LANG",
                   help="one or two languages to show beside it, e.g. --to th zh (default th)")
    p.add_argument("--model", help="Whisper model: small.en (default for English), large-v3-turbo, medium…")
    p.add_argument("--mt", choices=["nllb", "llm", "none"], help="translator (default nllb)")
    p.add_argument("--input", type=int, help="input device number (see the operator page)")
    p.add_argument("--channel", type=_channel_number, help="listen to one channel of the input (1 = the first)")
    p.add_argument("--device", default="cpu", help="cpu (default) or cuda")
    p.add_argument("--file", help="play this recording as if it were live")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--lan", action="store_true", help="let other devices on the network open /screen")
    p.add_argument("--ui", choices=["window", "browser", "none"], default=None)
    p.add_argument("--autostart", action="store_true", help="start listening right away")
    p.add_argument("--offline", metavar="AUDIO", help="test: run a recording through the pipeline, no screen")
    p.add_argument("--out", help="with --offline: write events here (JSON lines)")
    p.add_argument("--beam", type=int, default=1)
    p.add_argument("--hints", help="with --offline: names and words that may come up (like the Words box)")
    p.add_argument("--no-church-words", action="store_true", help="with --offline: skip the built-in church words")
    p.add_argument("--no-drafts", action="store_true", help="with --offline: translate finished lines only")
    p.add_argument("--replay", metavar="EVENTS", help="show a recorded run (from --offline) on the screen")
    p.add_argument("--loop", action="store_true", help="with --replay: repeat")
    p.add_argument("--version", action="version", version=__version__)
    return p.parse_args(argv)


def main(argv=None):
    args = parse(argv)
    if getattr(sys, "frozen", False):
        use_app_model_dir()

    if args.offline:
        mine = dict(hints=args.hints or "", fix={})
        events = run_offline(args.offline, args.src or "en", args.tgt or ["th"], model_name=args.model,
                             mt_kind=args.mt or "nllb", out_path=args.out, device=args.device, beam=args.beam,
                             glossary=mine if args.no_church_words else church_glossary(mine),
                             drafts=not args.no_drafts)
        print(json.dumps(events[-1]))
        return

    be_gentle()
    app = App(args)
    host = "0.0.0.0" if args.lan else "127.0.0.1"
    httpd = serve(app.hub, app, host, args.port)
    args.port = httpd.server_address[1]
    control = f"http://127.0.0.1:{args.port}/"
    screen = f"http://127.0.0.1:{args.port}/screen"
    print(f"Riah Church Translate {__version__}\n  operator page: {control}\n  church screen: {screen}", flush=True)

    if args.replay:
        threading.Thread(target=replay, args=(app.hub, args.replay, 1.0, args.loop), daemon=True).start()
    elif args.autostart or args.file:
        app.start({})

    ui = args.ui or ("window" if getattr(sys, "frozen", False) else "browser")
    if ui == "window":
        try:
            run_windows(app, control, screen)
            return
        except Exception as e:  # no window toolkit (e.g. a PC without Edge WebView2): use the browser
            print(f"No app window ({e.__class__.__name__}: {e}); opening the browser instead.", flush=True)
            app.open_screen_hook = None
            ui = "browser"
    if ui == "browser":
        webbrowser.open(control)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        app.engine.stop()


def run_windows(app, control, screen):
    """Native windows via pywebview (WKWebView on the Mac, Edge WebView2 on Windows).
    Closing the operator window quits."""
    import webview

    title = "Riah Church Translate — church screen"
    opened = []          # the church screen window, once open (one at a time)

    def open_screen(layout="full"):
        for w in opened[:]:
            try:
                w.destroy()
            except Exception:
                pass
        opened.clear()
        screens = list(getattr(webview, "screens", []) or [])
        # The church screen is the second display; with one display (a mirrored projector) it's this one.
        target = screens[1] if len(screens) > 1 else (screens[0] if screens else None)
        if layout in ("bar", "side") and target is not None:
            w, h = int(target.width), int(target.height)
            if layout == "bar":
                ww, wh = w, round(h * BAR)
                x, y = 0, h - wh
            else:
                ww, wh = round(w * SIDE), h
                x, y = w - ww, 0
            if sys.platform != "darwin":       # Windows places windows on the whole desktop, the Mac per display
                x, y = x + int(target.x), y + int(target.y)
            # Frameless, on top of the slideshow, see-through where there are no words, and it never takes
            # the keyboard from the slides (the clicker keeps working).
            win = webview.create_window(title, f"{screen}?layout={layout}", x=x, y=y, width=ww, height=wh,
                                        screen=target, frameless=True, on_top=True, focus=False,
                                        transparent=True, background_color="#000000", min_size=(160, 120))
            if sys.platform == "darwin":
                win.events.shown += lambda: _over_full_screen(win)
        else:
            kw = dict(fullscreen=len(screens) > 1, background_color="#000000")
            if len(screens) > 1:
                kw["screen"] = target
            win = webview.create_window(title, screen, **kw)
        opened.append(win)
        win.events.closed += lambda: opened.remove(win) if win in opened else None

    def relayout(layout):
        if opened:           # the screen is up: put it where it now belongs
            open_screen(layout)

    app.open_screen_hook = open_screen
    app.relayout_hook = relayout
    win = webview.create_window("Riah Church Translate", control, width=1180, height=820, min_size=(860, 640),
                                background_color="#0f0f11")
    win.events.closed += lambda: app.engine.stop()
    # On Windows ask for Edge WebView2 by name: without it pywebview would quietly use old Internet Explorer.
    webview.start(gui="edgechromium" if sys.platform == "win32" else None)


def _over_full_screen(win):
    """Mac: let the bar show on top of a slideshow playing full screen (its own Space)."""
    try:
        from PyObjCTools import AppHelper

        def go():
            ns = win.native
            ns.setCollectionBehavior_(ns.collectionBehavior() | (1 << 0) | (1 << 8))   # all Spaces, full-screen aux
        AppHelper.callAfter(go)
    except Exception as e:
        print(f"Bar may not show over a full-screen slideshow ({e.__class__.__name__}: {e})", flush=True)
