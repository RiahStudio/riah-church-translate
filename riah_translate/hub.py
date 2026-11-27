"""Shared live state + fan-out to every open screen (Server-Sent Events subscribers)."""
from __future__ import annotations

import queue
import threading
import time
from collections import deque


class Hub:
    def __init__(self, display=None, langs=None):
        self.lock = threading.Lock()
        self.subs: set[queue.Queue] = set()
        self.lines = deque(maxlen=80)   # {id, seg, src, dst: {lang: text}, draft: {lang: text}, lag, t}
        self.partial = ""
        self.seg = None                 # line number the partial row will become
        self.drafts = {}                # lang -> draft translation of the partial row
        self.status = dict(state="stopped", detail="Press Start when the service begins.")
        self.display = dict(fontScale=1.0, swap=False, labels=True, visibleRows=3, wakeLock=False, showSrc=True,
                            hidden=False)
        if display:
            self.display.update(display)
        self.langs = norm_langs(langs or dict(src="en", tgts=["th"]))
        self.stats = dict(lag=None, mtLag=None, level=0.0)
        self.taps = []                  # callables that also get every event (the transcript)

    # ------------------------------------------------------------------ subscribers
    def subscribe(self):
        q = queue.Queue(maxsize=1000)
        with self.lock:
            self.subs.add(q)
            q.put_nowait(self._snapshot())
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.subs.discard(q)

    def has(self, q):
        with self.lock:
            return q in self.subs

    def snapshot(self):
        with self.lock:
            return self._snapshot()

    def _snapshot(self):
        return dict(type="snapshot", lines=[dict(l, dst=dict(l["dst"]), draft=dict(l["draft"])) for l in self.lines],
                    partial=self.partial, seg=self.seg, drafts=dict(self.drafts), status=self.status,
                    display=self.display, langs=self.langs, stats=self.stats)

    # ------------------------------------------------------------------ events
    def publish(self, ev: dict):
        ev.setdefault("t", time.time())
        with self.lock:
            self._apply(ev)
            dead = []
            for q in self.subs:
                try:
                    q.put_nowait(ev)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self.subs.discard(q)
        for tap in self.taps:
            try:
                tap(ev)
            except Exception:
                pass

    def _line(self, seg):
        if seg is None:
            return None
        for line in reversed(self.lines):
            if line.get("seg") == seg:
                return line
        return None

    def _apply(self, ev):
        k = ev["type"]
        if k == "line":
            seg = ev.get("seg")
            keep = dict(self.drafts) if ev.get("keepDraft") and seg == self.seg else {}
            self.lines.append(dict(id=ev["id"], seg=seg, src=ev["src"], dst={}, draft=keep, lag=ev.get("lag"),
                                   t=ev["t"]))
            self.partial, self.drafts = "", {}
            self.seg = None if seg is None else seg + 1
            if ev.get("lag") is not None:
                self.stats["lag"] = ev["lag"]
        elif k == "translation":
            code = ev.get("lang") or self.langs["tgts"][0]   # older recordings had one language, no `lang`
            for line in reversed(self.lines):
                if line["id"] == ev["id"]:
                    line["dst"][code] = ev["dst"]
                    break
            if ev.get("lag") is not None:
                self.stats["mtLag"] = ev["lag"]
        elif k == "draft":
            line = self._line(ev["seg"])
            if line is not None:
                if ev["lang"] not in line["dst"]:
                    line["draft"][ev["lang"]] = ev["text"]
            elif self.seg is None or ev["seg"] == self.seg:
                self.seg = ev["seg"]
                self.drafts[ev["lang"]] = ev["text"]
        elif k == "partial":
            self.partial = ev["text"]
            if ev.get("seg") is not None:
                if ev["seg"] != self.seg:
                    self.drafts = {}
                self.seg = ev["seg"]
        elif k == "status":
            self.status = dict(state=ev["state"], detail=ev.get("detail", ""))
        elif k == "display":
            self.display.update(ev["display"])
        elif k == "langs":
            self.langs = norm_langs(ev)
            self.drafts = {}
        elif k == "clear":
            self.lines.clear()
            self.partial, self.drafts = "", {}
        elif k == "level":
            self.stats["level"] = ev["level"]


def norm_langs(d) -> dict:
    """{src, tgts}. Older recordings and settings had a single `tgt`."""
    tgts = d.get("tgts")
    if tgts is None:
        tgts = [d["tgt"]] if d.get("tgt") else []
    return dict(src=d.get("src", "en"), tgts=list(tgts))
