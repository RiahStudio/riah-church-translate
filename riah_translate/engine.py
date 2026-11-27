"""Runs one live session: audio in -> streaming Whisper -> finished lines -> translations -> screens.

While the preacher is still mid-sentence, the words Whisper has already agreed on are translated
too ("drafts"), so the Thai starts appearing a second or two after the English instead of waiting
for the end of the line. Each line has a `seg` number: the partial row on screen and its drafts carry
the number of the line they will become.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import traceback
from collections import deque

import numpy as np

from . import report
from .asr import SR, StreamingTranscriber
from .langs import lang
from .models import DownloadFailed, choose_whisper, forget_whisper, load_whisper
from .mt import make_translator


class PassesFailed(RuntimeError):
    """The speech model failed pass after pass and every repair in place failed too. The retry loop
    always starts the service again after this (it is never a choice that can't work)."""


_faults: dict[str, int] = {}


def _log_fault(what: str):
    """Log a fault that was contained (call inside the except block): the full traceback for the
    first few of each kind, then one in fifty, so a fault that repeats all sermon can't fill the disk."""
    n = _faults[what] = _faults.get(what, 0) + 1
    if n <= 3 or n % 50 == 0:
        print(f"[contained] {what} (#{n})", flush=True)
        traceback.print_exc()


def _contained(what: str, fn, *args, default=None, **kw):
    """Run one step of the live loop that must never end the service (a meter, a draft, a log
    line, a close). If it fails, say so in the log and carry on with `default`."""
    try:
        return fn(*args, **kw)
    except Exception:
        _log_fault(what)
        return default


def _note(fn, fallback: str, *args) -> str:
    """The operator's message, whatever the exception looked like."""
    try:
        return fn(*args)
    except Exception:
        return fallback


def plain_error(e: Exception) -> str:
    """What the operator sees when Start fails: what happened and what to try. Details go to the log."""
    if isinstance(e, DownloadFailed):
        return str(e)
    name, text = e.__class__.__name__, str(e)
    if name == "PortAudioError" or "PortAudio" in text or "InputStream" in text:
        return ("Couldn't open the microphone or sound input. Check the cable, pick it under Listen to, "
                "and on a Mac allow Riah Church Translate in System Settings > Privacy & Security > Microphone.")
    if isinstance(e, FileNotFoundError):
        return "Couldn't find that recording."
    if isinstance(e, MemoryError) or "out of memory" in text.lower():
        return "This computer ran out of memory. Close other programs, or pick Fast under Speech model."
    if "11434" in text or "ollama" in text.lower():
        return "The AI translator (Ollama) isn't answering. Start Ollama, or switch Translator back to the built-in one."
    return "Something went wrong while starting. Press Start again; if it keeps happening, restart the app."


def retry_note(e: Exception, wait: int) -> str:
    """What the operator sees while the app puts itself back together after a problem mid-service."""
    if isinstance(e, DownloadFailed):
        return f"{e} Trying again in {wait} s."
    name, text = e.__class__.__name__, str(e)
    if name == "PortAudioError" or "PortAudio" in text or "InputStream" in text:
        return f"Lost the sound input. Reconnecting in {wait} s…"
    if isinstance(e, MemoryError) or "out of memory" in text.lower():
        return f"This computer ran short of memory. Starting again in {wait} s…"
    if "11434" in text or "ollama" in text.lower():
        return f"The AI translator (Ollama) isn't answering. Trying again in {wait} s…"
    return f"Something stopped working. Starting again in {wait} s…"


def be_gentle():
    """The church computer is also running the slides, the livestream, the song lyrics. Ask the
    system to let those go first: the subtitles use whatever the computer has spare, which is
    plenty, and never make the rest of the computer stutter. Once per app."""
    try:
        if sys.platform == "win32":
            import ctypes
            k = ctypes.windll.kernel32
            k.SetPriorityClass(k.GetCurrentProcess(), 0x4000)     # BELOW_NORMAL_PRIORITY_CLASS
        elif hasattr(os, "nice"):
            os.nice(4)
    except Exception as e:
        print(f"couldn't lower the app's priority: {e}", flush=True)


MIN_CHUNK = 1.0      # seconds of new audio between Whisper passes
DRAFT_MIN = 3        # words agreed before the first draft of a line
DRAFT_STEP = 2       # new words before the next draft
BUSY = 0.85          # skip drafts while a Whisper pass takes this share of MIN_CHUNK (slow computer)

# What a run of failed Whisper passes climbs through (one pass a second, so about one rung a second).
# The sound input and the translations keep running the whole time; only that pass's words are lost.
FAILS_FRESH = 3      # failed passes in a row before the hearing starts afresh (a soft tidy before that)
FAILS_RELOAD = 6     # ... before the speech model is loaded again from disk
FAILS_RESTART = 10   # ... before the whole session is rebuilt by the retry loop (PassesFailed)
RETRY_CAP = 8        # most seconds between restarts after a problem (30 for a download that keeps failing)


def fast_cores() -> int:
    """Cores worth giving the models. On a Mac with Apple silicon only the performance cores: the
    efficiency cores are much slower, and a model split across both waits for the slow ones. On an
    Intel Mac the real cores, not the hyper-threads."""
    n = os.cpu_count() or 4
    if sys.platform == "darwin":
        import subprocess
        for key in ("hw.perflevel0.physicalcpu", "hw.physicalcpu"):
            try:
                v = int(subprocess.run(["sysctl", "-n", key], capture_output=True, text=True, timeout=2).stdout)
            except (OSError, ValueError, subprocess.SubprocessError):
                continue
            if v > 0:
                return min(v, n)
    return n


def _threads():
    # Whisper gets the fast cores (it runs about once a second); translation runs between passes.
    # On a PC a quarter of the cores (at least one) stays free for the slides and everything else; a
    # Mac's efficiency cores are already left to the other programs (fast_cores leaves them out).
    n = fast_cores()
    spare = 0 if sys.platform == "darwin" else max(1, n // 4)
    return max(1, min(8, n - spare)), max(1, min(4, n // 2))


def _words(text: str) -> list[str]:
    return re.sub(r"[^\w\s']", " ", text.lower()).split()


def covers(line: str, start: str) -> bool:
    """Does `line` begin with every word of `start`? (A draft is only kept for the line it belongs to.)"""
    a, b = _words(line), _words(start)
    if not b:
        return False
    if len(a) == 1 and len(b) == 1:          # unspaced scripts: compare characters
        return a[0].startswith(b[0])
    return a[: len(b)] == b


def _size(text: str, spaced: bool) -> int:
    return len(text.split()) if spaced else len(re.sub(r"\s", "", text)) // 3


class Translations:
    """Background translator, so the original column never waits. Finished lines go first, into
    every language on screen at once; then the newest draft (older drafts are simply dropped)."""

    def __init__(self, translator, src, tgts, glossary, on_final, on_draft, spaced=True, drafts=True, verses=None):
        self.translator, self.src, self.glossary = translator, src, glossary
        self.verses = verses or (lambda: None)     # -> verses.Verses, once the Bible has loaded
        self.tgts = list(tgts)
        self.on_final, self.on_draft = on_final, on_draft
        self.spaced = spaced
        self.drafts = drafts and getattr(translator, "drafts", False)
        self.cv = threading.Condition()
        self.finals: deque = deque()
        self.draft_job = None                  # (seg, text): the newest only
        self.asked = (None, 0)                 # (seg, size) of the last draft asked for
        self.state = dict(seg=None, src="", pieces={})   # what the latest draft says
        self.cut: dict[int, str] = {}          # seg -> finished line text
        self.context: list[str] = []
        self.closed = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    # ---------------------------------------------------------------- from the listening thread
    def set_targets(self, tgts):
        with self.cv:
            self.tgts = list(tgts)
            self.state = dict(seg=None, src="", pieces={})

    def draft_covers(self, seg, text) -> bool:
        with self.cv:
            return self.state["seg"] == seg and covers(text, self.state["src"])

    def add_line(self, line_id, seg, text):
        with self.cv:
            self.cut[seg] = text
            for old in [k for k in self.cut if k < seg - 20]:
                del self.cut[old]
            self.finals.append((line_id, seg, text, list(self.context[-3:])))
            self.context.append(text)
            if self.draft_job and self.draft_job[0] <= seg:
                self.draft_job = None
            self.cv.notify()

    def want_draft(self, seg, text):
        if not self.drafts or not text or not self.tgts:
            return
        n = _size(text, self.spaced)
        with self.cv:
            last_seg, last_n = self.asked
            if n < DRAFT_MIN or (last_seg == seg and n - last_n < DRAFT_STEP):
                return
            self.asked = (seg, n)
            self.draft_job = (seg, text)
            self.cv.notify()

    def close(self, timeout=30):
        with self.cv:
            self.closed = True
            self.draft_job = None
            self.cv.notify()
        self.thread.join(timeout)

    # ---------------------------------------------------------------- worker
    def _run(self):
        while True:
            with self.cv:
                while not self.finals and not self.draft_job and not self.closed:
                    self.cv.wait()
                if self.finals:
                    kind, job = "final", self.finals.popleft()
                elif self.draft_job:
                    kind, job, self.draft_job = "draft", self.draft_job, None
                else:
                    return
                tgts = list(self.tgts)
                st = self.state
            if not tgts:
                continue
            try:
                if kind == "final":
                    self._final(job, tgts, st)
                else:
                    self._draft(job, tgts, st)
            except Exception:              # one bad line must not stop the translations
                traceback.print_exc()

    def _final(self, job, tgts, st):
        line_id, seg, text, ctx = job
        tgts = self._bible(line_id, text, tgts)
        if not tgts:
            return
        keep = st["pieces"] if st["seg"] == seg and covers(text, st["src"]) else None
        t0 = time.perf_counter()
        try:
            res = self.translator.translate_multi(text, self.src, tgts, context=ctx, glossary=self.glossary, keep=keep)
        except Exception as e:  # show the original rather than nothing
            print(f"translation failed for line {line_id}: {e}", flush=True)
            res = {t: (text, None) for t in tgts}
        secs = time.perf_counter() - t0
        for t in tgts:
            self.on_final(line_id, t, res[t][0], secs)

    def _bible(self, line_id, text, tgts):
        """When the preacher reads a verse as written, the screen shows that verse from the Thai or
        Chinese Bible instead of a machine translation. Anything of his own in the line (an aside,
        a change of words) and it is translated as usual. Returns the languages still to translate."""
        v = self.verses() if self.src == "en" else None
        if v is None:
            return tgts
        try:
            hit = v.hear(text, tgts)
        except Exception:
            traceback.print_exc()
            return tgts
        if not hit:
            return tgts
        from .verses import display
        done = set(hit.get("langs") or [])
        for t in tgts:
            if t in done:      # "" = more of a verse already on screen: nothing new to show
                self.on_final(line_id, t, display(hit, t) or "", 0.0)
        return [t for t in tgts if t not in done]

    def _draft(self, job, tgts, st):
        seg, text = job
        with self.cv:
            if seg in self.cut:              # the line is finished; its final is on the way
                return
        keep = st["pieces"] if st["seg"] == seg and covers(text, st["src"]) else None
        try:
            res = self.translator.translate_multi(text, self.src, tgts, glossary=self.glossary, keep=keep, draft=True)
        except Exception as e:
            print(f"draft failed: {e}", flush=True)
            return
        with self.cv:
            line = self.cut.get(seg)
            if line is not None and not covers(line, text):
                return                       # the line ended before these words; drop it
            self.state = dict(seg=seg, src=text, pieces={t: p for t, (_, p) in res.items() if p})
        for t in tgts:
            self.on_draft(seg, t, res[t][0], text)


class Engine:
    """One live service at a time. If anything breaks mid-service (the sound input goes away, a model
    hiccups), it puts itself back together and carries on, without anyone pressing a button."""

    def __init__(self, hub):
        self.hub = hub
        self.thread = None
        self.stop_flag = threading.Event()
        self.restart_flag = threading.Event()   # the sound person's Restart button
        self.translator_cache = {}
        self.mt: Translations | None = None
        self.tr = None
        self.seg = 0
        self.next_id = 0
        self._live = False            # has this run gone live yet? (once it has, only Stop ends it)
        self.verses = None            # verses.Verses once the Bible has loaded (in the background)
        self._verses_loading = False
        self.notes = None             # what the sermon notes gave: {"hints", "refs"}; never the notes

    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def start(self, cfg: dict):
        self.stop()
        self.stop_flag.clear()
        self.restart_flag.clear()
        if cfg.get("verses", True) and cfg.get("src") == "en":
            self._load_verses()
        self.thread = threading.Thread(target=self._run, args=(cfg,), daemon=True)
        self.thread.start()

    def stop(self):
        if self.running:
            self.stop_flag.set()
            self.thread.join(15)

    def restart(self):
        """The sound person's Restart button: a new sound input and a fresh hearing, within a
        second, with the screen's lines, the numbering and the translations carried on. Also cuts
        short the pause before a retry. False when no service is live to restart."""
        if not (self.running and self._live) or self.stop_flag.is_set():
            return False
        self.restart_flag.set()
        return True

    @staticmethod
    def _spawn(fn):
        threading.Thread(target=fn, daemon=True).start()

    def _sleep(self, secs):
        """The pause before a retry. True when Stop was pressed; Restart cuts the pause short."""
        end = time.monotonic() + secs
        while not self.stop_flag.is_set():
            left = end - time.monotonic()
            if left <= 0 or self.restart_flag.is_set():
                return False
            self.stop_flag.wait(min(left, 0.1))
        return True

    def set_targets(self, src, tgts):
        """Change the translation languages without stopping (e.g. turn Chinese on mid-service)."""
        mt = self.mt
        if mt is not None and self.running:
            mt.set_targets([t for t in tgts if t != src])

    def _load_verses(self):
        if self.verses is not None or self._verses_loading:
            return
        self._verses_loading = True

        def load():
            try:
                from .verses import BIBLE_LANGS, Verses
                v = Verses.load(langs=BIBLE_LANGS)
                if v is not None:
                    v.expect_refs((self.notes or {}).get("refs"))
                self.verses = v
            except Exception:
                traceback.print_exc()
            finally:
                self._verses_loading = False
        threading.Thread(target=load, daemon=True).start()

    def set_notes(self, notes):
        """Sermon notes added, or removed (None). Used at once, also mid-service."""
        self.notes = notes
        v = self.verses
        if v is not None:
            v.expect_refs((notes or {}).get("refs"))
        tr = self.tr
        if tr is not None:
            tr.hints = self._hints(getattr(tr, "base_hints", ""))

    def _hints(self, mine):
        """The notes' names first (they are this week's), then the Words box."""
        theirs = (self.notes or {}).get("hints", "")
        return ", ".join(p for p in (theirs.strip(" ,"), (mine or "").strip(" ,")) if p)

    def _status(self, state, detail=""):
        self.hub.publish(dict(type="status", state=state, detail=detail))
        report.status(state)

    def _voice(self, cfg):
        """The spoken translation, if the operator set one up. A voice problem never stops the
        subtitles: no speaker means no sound, not no service."""
        plan = cfg.get("voice") or {}
        tgts = [t for t in (cfg.get("tgts") or []) if t != cfg.get("src")]
        plan = {t: plan[t] for t in tgts if isinstance(plan.get(t), dict) and plan[t].get("on")}
        if not plan:
            return None
        try:
            from .voice import VoiceOut
            v = VoiceOut(plan, on_note=lambda text: self._status("listening", text))
            self.hub.publish(dict(type="voice", note=v.note()))
            return v
        except Exception as e:
            print(f"spoken translation off: {e}", flush=True)
            return None

    def _translator(self, cfg, threads):
        key = (cfg.get("mt", "nllb"), cfg.get("llmModel"), cfg.get("llmUrl"))
        if key not in self.translator_cache:
            self.translator_cache[key] = make_translator(key[0], on_status=self._status, llm_model=key[1],
                                                         llm_url=key[2], threads=threads)
        return self.translator_cache[key]

    def _run(self, cfg):
        """Runs the service until Stop. After a problem it starts listening again (1 s, then longer if
        it keeps happening, never more than RETRY_CAP s apart). Once the service has been live,
        NOTHING ends it but Stop: whatever the error was, it tries again. Only a service that has
        never gone live can stop with a message (a recording, a language it can't listen to, a
        recording file that isn't there), because trying again can't fix those."""
        self.seg = 0
        self.next_id = int(time.time() * 1000)
        self._live = False
        _contained("reset the Bible", self._reset_verses)
        tries = 0
        while not self.stop_flag.is_set():
            began = time.monotonic()
            self.restart_flag.clear()      # a Restart pressed during the pause is answered by this fresh start
            try:
                self._session(cfg)
                return
            except Exception as e:
                _contained("log the problem", traceback.print_exc)
                if cfg.get("file") or (not self._live and isinstance(e, (ValueError, FileNotFoundError))):
                    self._status("error", _note(plain_error, "Something went wrong while starting. Press Start again.", e))
                    return
                _contained("report the problem", report.event, "restart", e, recovered=False)
                tries = 1 if time.monotonic() - began > 120 else tries + 1
                wait = min(30 if isinstance(e, DownloadFailed) else RETRY_CAP, 2 ** (tries - 1))
                self._status("reconnecting", _note(retry_note, f"Something stopped working. Starting again in {wait} s…", e, wait))
                if self._sleep(wait):
                    break
        self._status("stopped", "Stopped.")

    def _reset_verses(self):
        if self.verses is not None:
            self.verses.reset()
            self.verses.expect_refs((self.notes or {}).get("refs"))

    def _session(self, cfg):
        src_code = cfg["src"]
        tgts = [t for t in cfg.get("tgts") or [cfg.get("tgt", "th")] if t != src_code]
        source = None
        mt = None
        voice = None          # set before anything can fail: the cleanup below needs it
        swap = dict(busy=False, done=None)   # a new sound input being opened for Restart
        swap_lock = threading.Lock()
        cancel = threading.Event()
        try:
            S = lang(src_code)
            if not S["whisper"]:
                raise ValueError(f"{S['name']} can be shown as a translation, but the app can't listen to it yet.")
            asr_threads, mt_threads = _threads()
            self._status("loading", "Getting ready…")
            device = cfg.get("device", "cpu")
            model_name = cfg.get("model") or choose_whisper(src_code, device, asr_threads, on_status=self._status)
            model = load_whisper(model_name, device, asr_threads, on_status=self._status)
            translator = self._translator(cfg, mt_threads)
            mine = cfg.get("glossary", {}).get("hints", "")
            def new_hearing():
                t = StreamingTranscriber(model, S["whisper"], spaced=S["spaced"], hints=self._hints(mine),
                                         beam_size=int(cfg.get("beam", 1)))
                t.base_hints = mine
                return t
            tr = new_hearing()
            self.tr = tr
            self.hub.publish(dict(type="langs", src=src_code, tgts=tgts))

            if cfg.get("file"):
                from .audio import FileSource
                source = FileSource(cfg["file"], realtime=True).start()
                where = "Playing a recording as if it were live."
            else:
                from .audio import MicSource
                source = MicSource(cfg.get("input"), cfg.get("channel")).start()
                where = "Listening."
            where += f" Speech model: {model_name}."
            from .audio import AutoLevel
            level_up = AutoLevel()
            t0 = time.monotonic()
            self._live = True        # from here on only Stop ends the service (see _run)
            self._status("listening", where)
            ends = {}

            def final(line_id, tgt, text, secs):
                lag = time.monotonic() - ends.get(line_id, t0)      # ends: when each line was heard
                self.hub.publish(dict(type="translation", id=line_id, lang=tgt, dst=text, lag=round(lag, 2),
                                      mtSec=round(secs, 2)))
                if voice is not None:      # the headphone system hears the same finished line
                    voice.say(tgt, text)

            def draft(seg, tgt, text, src_text):
                self.hub.publish(dict(type="draft", seg=seg, lang=tgt, text=text, src=src_text))

            verses = (lambda: self.verses) if cfg.get("verses", True) else None
            voice = self._voice(cfg)
            mt = self.mt = Translations(translator, src_code, tgts, cfg.get("glossary", {}), final, draft,
                                        spaced=S["spaced"], drafts=cfg.get("drafts", True), verses=verses)
            def reopen_input():              # for Restart, on its own thread (see the loop)
                try:
                    from .audio import MicSource
                    fresh_source = MicSource(cfg.get("input"), cfg.get("channel")).start()
                except Exception:
                    _log_fault("reopen the sound input")
                    swap["busy"] = False
                    return
                with swap_lock:
                    swap["busy"] = False
                    if cancel.is_set():
                        _contained("close the sound input", fresh_source.stop)
                    else:
                        swap["done"] = fresh_source

            fresh, last_partial, last_level, pass_sec, peak = 0, None, 0.0, 0.0, 0.0
            bad = 0                   # failed Whisper passes in a row
            away = False

            def publish_lines(lines, with_lag=True):
                # One bad line must not cost the others or the numbering: the screens and the
                # translator both count lines by seg.
                for ln in lines:
                    self.next_id += 1
                    line_id, seg = self.next_id, self.seg
                    self.seg += 1
                    try:
                        ends[line_id] = t0 + ln["end"]
                        lag = round(time.monotonic() - ends[line_id], 2) if with_lag else None
                        keep = _contained("draft check", mt.draft_covers, seg, ln["text"], default=False)
                        self.hub.publish(dict(type="line", id=line_id, seg=seg, src=ln["text"], lag=lag, keepDraft=keep))
                    except Exception:
                        _log_fault("show a line")
                    _contained("translate a line", mt.add_line, line_id, seg, ln["text"])

            # THE SERVICE ENDS WHEN THE OPERATOR PRESSES STOP, AND FOR NO OTHER REASON. Every step
            # below is contained: a fault costs that step (one pass's words, one meter reading,
            # one draft), never the sermon. 2026-11-26: on a Mac one failed Whisper pass raised out
            # of this loop and dropped the operator from the live screen back to setup mid-sermon.
            # A failed pass climbs a ladder (see FAILS_*): tidy, start the hearing afresh, load the
            # speech model again, and only then rebuild the whole session (PassesFailed), which the
            # retry loop in _run always starts again. The sound input and translations run throughout.
            while not self.stop_flag.is_set():
                if self.restart_flag.is_set():
                    # The sound person pressed Restart. The hearing starts afresh at once (what was
                    # heard so far is finished as lines first, with no new pass, so nothing is lost
                    # and it takes no time). A new sound input is opened alongside, and swapped in
                    # when it is ready, so no sound is missed: a sound input can take a second or
                    # two to open, and if it won't open the old one just carries on. The screen
                    # keeps its lines and numbering; the translator, speech model and voice stay.
                    self.restart_flag.clear()
                    if not cfg.get("file"):
                        self._status("reconnecting", "Restarting…")
                        publish_lines(_contained("last words", tr.flush, default=[]), with_lag=False)
                        level_up = AutoLevel()
                        tr = new_hearing()
                        self.tr = tr
                        t0 = time.monotonic()
                        fresh, last_partial, last_level, pass_sec, peak, bad, away = 0, "", 0.0, 0.0, 0.0, 0, False
                        _contained("clear the partial", self.hub.publish, dict(type="partial", text="", seg=self.seg))
                        self._status("listening", where)
                        if not swap["busy"]:
                            swap["busy"] = True
                            _contained("reopen the sound input", self._spawn, reopen_input)
                with swap_lock:
                    new_source, swap["done"] = swap["done"], None
                if new_source is not None:
                    old, source = source, new_source
                    _contained("close the sound input", old.stop)
                chunk, finished = source.read(0.2)
                lost = getattr(source, "reconnecting", False)
                if lost != away:          # the sound input dropped out, or came back
                    away = lost
                    self._status("reconnecting" if lost else "listening",
                                 "Lost the sound input. Reconnecting…" if lost else where)
                if len(chunk):
                    try:
                        now = time.monotonic()
                        peak = max(peak, float(np.abs(chunk).max()))
                        if now - last_level > 0.2:
                            self.hub.publish(dict(type="level", level=round(float(np.sqrt(np.mean(chunk ** 2))), 4),
                                                  peak=round(peak, 3)))
                            last_level, peak = now, 0.0
                    except Exception:
                        _log_fault("level meter")
                    chunk = _contained("auto level", level_up, chunk, default=chunk)
                    try:
                        tr.push(chunk)
                        fresh += len(chunk)
                    except Exception:
                        _log_fault("add audio")
                        _contained("repair the hearing", tr.recover, True)
                if fresh >= MIN_CHUNK * SR or finished:
                    fresh = 0
                    try:
                        lines, partial = tr.step(final=finished)
                    except Exception as e:
                        bad += 1
                        if bad <= 3 or bad % 30 == 0:
                            traceback.print_exc()
                        _contained("report the bad pass", report.event, "bad_pass", e, recovered=True)
                        if bad >= FAILS_RESTART:
                            raise PassesFailed(f"{bad} speech passes in a row failed: {e.__class__.__name__}: {e}") from e
                        _contained("repair the hearing", tr.recover, bad >= FAILS_FRESH)
                        if bad == FAILS_RELOAD:
                            forget_whisper(model_name, device)
                            model = tr.model = load_whisper(model_name, device, asr_threads, on_status=self._status)
                            self._status("listening", where)
                        if finished or self.stop_flag.wait(0.05):
                            break
                        continue
                    bad = 0
                    try:
                        pass_sec = 0.7 * pass_sec + 0.3 * tr.last_transcribe_sec
                    except Exception:
                        _log_fault("pass timing")
                    publish_lines(lines)
                    try:
                        if partial != last_partial:
                            self.hub.publish(dict(type="partial", text=partial, seg=self.seg))
                            last_partial = partial
                        if pass_sec < BUSY * MIN_CHUNK:
                            mt.want_draft(self.seg, tr.pending_text())
                    except Exception:
                        _log_fault("partial or draft")
                if finished:
                    break
            if self.stop_flag.is_set():
                # Show what was being said when Stop was pressed.
                publish_lines(_contained("last words", lambda: tr.step(final=True)[0], default=[]), with_lag=False)
            _contained("close the translator", mt.close)
            _contained("clear the partial", self.hub.publish, dict(type="partial", text="", seg=self.seg))
            self._status("stopped", "Recording finished." if cfg.get("file") and not self.stop_flag.is_set() else "Stopped.")
        finally:
            self.mt = None
            self.tr = None
            if voice is not None:
                _contained("close the voice", voice.close, 2)
            if mt is not None:
                _contained("close the translator", lambda: mt.thread.is_alive() and mt.close(1))
            if source:
                _contained("close the sound input", source.stop)
            with swap_lock:               # a Restart's new input that was still opening must not outlive the service
                cancel.set()
                spare, swap["done"] = swap["done"], None
            if spare is not None:
                _contained("close the sound input", spare.stop)


# ---------------------------------------------------------------------- offline (tests, CI)
def run_offline(path, src, tgts, model_name=None, mt_kind="nllb", out_path=None, glossary=None, device="cpu",
                beam=1, log=print, drafts=True):
    """Feeds a recording through the same pipeline on a simulated clock.

    The clock behaves like live audio on this computer: a Whisper pass can only start once its audio
    has 'arrived', and audio keeps arriving while Whisper works. Translations queue behind each other
    on their own clock, and a draft is only made when the translator is free, as in the live app. So
    `lag` is an honest estimate of how far behind the speaker the screen would be on this hardware.
    """
    from .audio import load_file
    if isinstance(tgts, str):
        tgts = [tgts]
    tgts = [t for t in tgts if t != src] if mt_kind != "none" else []
    S = lang(src)
    asr_threads, mt_threads = _threads()
    t_load = time.perf_counter()
    model_name = model_name or choose_whisper(src, device, asr_threads, log=log)
    model = load_whisper(model_name, device, asr_threads)
    translator = make_translator(mt_kind if tgts else "none", threads=mt_threads)
    drafts = drafts and getattr(translator, "drafts", False)
    log(f"models ready in {time.perf_counter() - t_load:.1f}s")
    audio = load_file(path)
    dur = len(audio) / SR
    tr = StreamingTranscriber(model, S["whisper"], spaced=S["spaced"], hints=(glossary or {}).get("hints", ""),
                              beam_size=beam)
    from .audio import AutoLevel
    level_up = AutoLevel()                                 # as live: a feed sent too low or too hot is evened out
    events, clock, pos, ctx, n, seg = [], 0.0, 0, [], 0, 0
    asr_busy = 0.0
    mt_free_at = 0.0
    state = dict(seg=None, src="", pieces={})
    asked = (None, 0)
    seg_drafts: list[dict] = []     # draft events of the current seg
    while True:
        clock = max(clock, pos / SR + MIN_CHUNK)           # wait for at least MIN_CHUNK of new audio
        upto = min(len(audio), int(clock * SR))
        tr.push(level_up(audio[pos:upto]))
        pos = upto
        final = pos >= len(audio)
        t0 = time.perf_counter()
        lines, partial = tr.step(final=final)
        spent = time.perf_counter() - t0
        asr_busy += spent
        clock += spent
        for ln in lines:
            n += 1
            lag = clock - ln["end"]
            for d in [d for d in seg_drafts if d["at"] > clock and not covers(ln["text"], d["src"])]:
                events.remove(d)     # finished after the line ended and doesn't fit it: the live app drops it
            kept = state["seg"] == seg and covers(ln["text"], state["src"])
            events.append(dict(type="line", id=n, seg=seg, src=ln["text"], start=round(ln["start"], 2),
                               end=round(ln["end"], 2), at=round(clock, 2), lag=round(lag, 2),
                               keepDraft=kept and any(d["at"] <= clock for d in seg_drafts)))
            if tgts:
                t1 = time.perf_counter()
                res = translator.translate_multi(ln["text"], src, tgts, context=ctx[-3:], glossary=glossary,
                                                 keep=state["pieces"] if kept else None)
                mt_sec = time.perf_counter() - t1
                mt_free_at = max(mt_free_at, clock) + mt_sec     # translations queue behind each other
                for t in tgts:
                    events.append(dict(type="translation", id=n, lang=t, dst=res[t][0], at=round(mt_free_at, 2),
                                       lag=round(mt_free_at - ln["end"], 2), mtSec=round(mt_sec, 2)))
                log(f"[{ln['start']:6.1f}-{ln['end']:6.1f}s] lag {lag:4.1f}s | {ln['text']}\n{'':>17}mt {mt_sec:4.2f}s | "
                    + " | ".join(res[t][0] for t in tgts))
            ctx.append(ln["text"])
            seg += 1
            seg_drafts = []
        events.append(dict(type="partial", text=partial, seg=seg, at=round(clock, 2)))
        pending = tr.pending_text()
        size = _size(pending, S["spaced"])
        if (drafts and tgts and not final and mt_free_at <= clock and tr.last_transcribe_sec < BUSY * MIN_CHUNK
                and size >= DRAFT_MIN and not (asked[0] == seg and size - asked[1] < DRAFT_STEP)):
            asked = (seg, size)
            keep = state["pieces"] if state["seg"] == seg and covers(pending, state["src"]) else None
            t1 = time.perf_counter()
            res = translator.translate_multi(pending, src, tgts, glossary=glossary, keep=keep, draft=True)
            mt_free_at = clock + (time.perf_counter() - t1)
            state = dict(seg=seg, src=pending, pieces={t: p for t, (_, p) in res.items() if p})
            for t in tgts:
                d = dict(type="draft", seg=seg, lang=t, text=res[t][0], src=pending, at=round(mt_free_at, 2))
                events.append(d)
                seg_drafts.append(d)
        if final:
            break
    summary = dict(type="summary", model=model_name, audioSec=round(dur, 1), asrBusySec=round(asr_busy, 1),
                   realtimeFactor=round(asr_busy / dur, 2), lines=n, src=src, tgts=tgts)
    events.append(summary)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return events
