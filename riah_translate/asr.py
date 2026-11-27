"""Streaming speech recognition on top of faster-whisper.

Whisper is not a streaming model: it transcribes a chunk of audio. To get live subtitles we keep a
rolling buffer of the speech that has not been shown yet, re-transcribe it every second, and only
trust a word once two passes in a row agree on it ("local agreement", the method from
ufal/whisper_streaming, MIT). Agreed words are cut into subtitle lines at sentence ends, at a pause,
or when a line gets too long, and the audio behind a finished line is dropped from the buffer.

What the room sees:
  * the line being spoken now, dimmed, updating every second (the "partial");
  * finished lines, solid, which never change once shown.
"""
from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

SR = 16000

SENT_END = re.compile(r"[.!?。！？]['\"”’)\]]*$")
CLAUSE_END = re.compile(r"[,;:，；：、]['\"”’)\]]*$")
ABBREV = {"mr.", "mrs.", "ms.", "dr.", "st.", "rev.", "vs.", "e.g.", "i.e.", "etc.", "no.", "ch.", "vv.", "v."}

# Whisper's well-known inventions on silence or music. A line that is only one of these is dropped.
HALLUCINATIONS = {
    "thank you.", "thanks for watching!", "thank you for watching.", "thanks for watching.",
    "please subscribe.", "subtitles by the amara.org community", "you", "bye.", "so.",
    "ขอบคุณที่รับชม", "ขอบคุณครับ", "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目", "字幕由amara.org社区提供",
}


@dataclass
class Word:
    start: float
    end: float
    text: str  # Whisper style: carries its own leading space in spaced languages


def _norm(t: str) -> str:
    return re.sub(r"[\W_]+", "", t.lower())


def _join(words) -> str:
    return "".join(w.text for w in words).strip()


def _drop_echoes(words: list[Word], before: Word | None) -> list[Word]:
    """Removes scraps Whisper makes out of the end of the previous word in a pause, e.g. "son. on. ."""
    out: list[Word] = []
    for w in words:
        prev = out[-1] if out else before
        n = _norm(w.text)
        if not n:
            continue
        if prev is not None and w.start - prev.end < 0.15:
            p = _norm(prev.text)
            if len(n) < len(p) and p.endswith(n):
                continue
            # A bare number glued onto the end of a sentence is a scrap of its last word
            # ("chapter 3. 8." when a turned-up feed rings on after "three").
            if n.isdigit() and SENT_END.search(prev.text.strip()):
                continue
        out.append(w)
    return out


def _sounds_like(a: str, b: str) -> bool:
    """Two normalized words Whisper could make from the same sound: the same word, one the
    other's ending, or one letter apart ("night" / "right")."""
    if not a or not b:
        return False
    if a == b or b.endswith(a) or a.endswith(b):
        return True
    return len(a) == len(b) >= 4 and sum(x != y for x, y in zip(a, b)) == 1


def _drop_repeats(words: list[Word]) -> list[Word]:
    """For the dimmed partial only: Whisper sometimes says a phrase twice ("good people. people.")."""
    out: list[Word] = []
    for w in words:
        out.append(w)
        for n in (3, 2, 1):
            if len(out) >= 2 * n and [_norm(x.text) for x in out[-n:]] == [_norm(x.text) for x in out[-2 * n:-n]]:
                if n > 1 or SENT_END.search(out[-1].text.strip()):
                    del out[-n:]
                    break
    return out


class HypothesisBuffer:
    """Local agreement of two passes (adapted from ufal/whisper_streaming, MIT)."""

    def __init__(self):
        self.buffer: list[Word] = []          # last pass's words that are not confirmed yet
        self.tail: deque[Word] = deque(maxlen=5)  # last confirmed words, to spot repeats
        self.last_time = 0.0                  # end of the last confirmed word (absolute seconds)

    def insert(self, words: list[Word]):
        new = [w for w in words if w.start > self.last_time - 0.1]
        new = _drop_echoes(new, self.tail[-1] if self.tail else None)
        if new and self.tail and abs(new[0].start - self.last_time) < 1.0:
            # Whisper often re-hears the last confirmed word or two at the buffer start.
            for n in range(min(len(self.tail), len(new), 5), 0, -1):
                if [_norm(w.text) for w in list(self.tail)[-n:]] == [_norm(w.text) for w in new[:n]]:
                    new = new[n:]
                    break
        return new

    def agree(self, new: list[Word]) -> list[Word]:
        confirmed = []
        while new and self.buffer and _norm(new[0].text) == _norm(self.buffer[0].text):
            w = new.pop(0)
            self.buffer.pop(0)
            confirmed.append(w)
        self.buffer = new
        self._mark(confirmed)
        return confirmed

    def take_all(self) -> list[Word]:
        """Speaker paused: accept the unconfirmed tail as heard."""
        words, self.buffer = self.buffer, []
        self._mark(words)
        return words

    def _mark(self, words):
        for w in words:
            self.tail.append(w)
            self.last_time = max(self.last_time, w.end)


class StreamingTranscriber:
    def __init__(self, model, language: str, spaced: bool = True, hints: str = "",
                 beam_size: int = 1, pause_sec: float = 0.9, max_words: int = 22, max_chars: int = 50,
                 max_buffer_sec: float = 20.0):
        self.model = model
        self.language = language
        self.spaced = spaced
        self.hints = hints.strip()
        self.beam_size = beam_size
        self.pause_sec = pause_sec
        self.max_words = max_words
        self.max_chars = max_chars
        self.max_buffer_sec = max_buffer_sec

        self.audio = np.zeros(0, dtype=np.float32)
        self.offset = 0.0           # absolute time of self.audio[0]
        self.hyp = HypothesisBuffer()
        self.pending: list[Word] = []   # confirmed words not yet shown as a finished line
        self.history = deque(maxlen=3)  # last finished lines, used as Whisper's context prompt
        self.shown_until = 0.0          # end of the last finished line (absolute seconds)
        self.sentence_open = False      # the last finished line stopped mid-sentence
        self.last_word: Word | None = None   # last word of the last finished line
        self.last_transcribe_sec = 0.0
        self._vad = None

    # ------------------------------------------------------------------ audio in
    def push(self, chunk: np.ndarray):
        self.audio = np.concatenate([self.audio, chunk.astype(np.float32, copy=False)])

    @property
    def now(self) -> float:
        return self.offset + len(self.audio) / SR

    # ------------------------------------------------------------------ repair
    def recover(self, hard: bool = False):
        """A pass failed: put the hearing back in a state the next pass can work with, without
        losing the clock (line times stay right). Never raises.

        Soft: forget the half-agreed words, drop anything odd in the audio buffer (NaN, inf) and
        keep only the newest two seconds of it. Words already agreed are kept and still shown.
        Hard: also start the hearing state afresh, as if the transcriber were new (forgets the
        words waiting to become a line, the context prompt and the repeat guard)."""
        try:
            keep = int(2.0 * SR)
            audio = np.nan_to_num(np.asarray(self.audio, dtype=np.float32).reshape(-1)[-keep:],
                                  nan=0.0, posinf=0.0, neginf=0.0)
            end = self.now
            self.audio = audio
            self.offset = end - len(audio) / SR
        except Exception:
            self.audio = np.zeros(0, dtype=np.float32)
        try:
            self._vad = None
            self.hyp.buffer = []
            if hard:
                self.hyp = HypothesisBuffer()
                self.hyp.last_time = self.offset
                self.pending = []
                self.history.clear()
                self.shown_until = self.offset
                self.sentence_open = False
                self.last_word = None
        except Exception:
            self.hyp = HypothesisBuffer()
            self.pending = []

    def flush(self):
        """Finish whatever has been heard as lines, without another speech pass (so it is instant).
        The Restart button uses it so the last words aren't lost. Never raises."""
        try:
            self.pending += self.hyp.take_all()
            return self._cut(force=True)
        except Exception:
            self.pending = []
            return []

    # ------------------------------------------------------------------ one pass
    def step(self, final: bool = False):
        """Transcribe the buffer once. Returns (finished_lines, partial_text)."""
        lines = []
        if len(self.audio) >= int(0.3 * SR):
            silent_tail = self._silent_tail_sec()
            if silent_tail >= len(self.audio) / SR - 0.05 and not self.hyp.buffer and not self.pending:
                # Nothing but silence in the buffer: skip Whisper (it invents words on silence).
                self._trim_to(self.now - 0.5)
            else:
                words = self._transcribe()
                new = self.hyp.insert(words)
                self.pending += self.hyp.agree(new)
                if final or silent_tail >= self.pause_sec:
                    self.pending += self.hyp.take_all()
                lines += self._cut(force=final or silent_tail >= self.pause_sec)
        if final:
            self.pending += self.hyp.take_all()
            lines += self._cut(force=True)
        self._trim()
        return lines, self._capital(_join(_drop_repeats(self.pending + self.hyp.buffer)))

    def pending_text(self) -> str:
        """Words Whisper has agreed on that aren't a finished line yet (what a draft translates)."""
        return self._capital(_join(self.pending))

    def _prompt(self):
        # The operator's words go first and are never cut; the last lines fill the rest (Whisper
        # reads about 220 tokens of prompt, so keep it short).
        hints = self.hints[:240]
        if self.history:
            said = " ".join(self.history)
        elif self.language == "en":
            said = "Good morning, everyone. Let's open our Bibles together."  # nudges punctuation
        else:
            said = ""
        said = said[-max(80, 400 - len(hints)):]
        return " ".join(p for p in (hints, said) if p) or None

    def _transcribe(self) -> list[Word]:
        t0 = time.perf_counter()
        segments, _ = self.model.transcribe(
            self.audio, language=self.language, task="transcribe", beam_size=self.beam_size,
            word_timestamps=True, condition_on_previous_text=False, initial_prompt=self._prompt(),
            temperature=0.0, vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500, speech_pad_ms=300),
        )
        words = []
        for seg in segments:
            if seg.no_speech_prob > 0.8 and seg.avg_logprob < -0.7:
                continue
            if seg.text.strip().lower() in HALLUCINATIONS:
                continue
            for w in seg.words or []:
                words.append(Word(w.start + self.offset, w.end + self.offset, w.word))
        self.last_transcribe_sec = time.perf_counter() - t0
        return words

    def _silent_tail_sec(self) -> float:
        """Seconds of non-speech at the end of the buffer (Silero VAD, bundled with faster-whisper)."""
        tail = self.audio[-int(4 * SR):]
        try:
            from faster_whisper.vad import VadOptions, get_speech_timestamps
            speech = get_speech_timestamps(tail, VadOptions(min_silence_duration_ms=250, speech_pad_ms=60))
            last_end = speech[-1]["end"] if speech else 0
        except Exception:  # fall back to a plain loudness check
            frame = int(0.05 * SR)
            n = len(tail) // frame
            if n == 0:
                return 0.0
            rms = np.sqrt((tail[: n * frame].reshape(n, frame) ** 2).mean(axis=1))
            loud = np.nonzero(rms > max(0.01, np.percentile(rms, 90) * 0.15))[0]
            last_end = (loud[-1] + 1) * frame if len(loud) else 0
        return (len(tail) - last_end) / SR

    # ------------------------------------------------------------------ lines out
    def _size(self, words) -> int:
        return len(words) if self.spaced else len(_join(words))

    def _limit(self) -> int:
        return self.max_words if self.spaced else self.max_chars

    def _cut(self, force: bool):
        out = []
        while self.pending:
            cut = None
            for i, w in enumerate(self.pending):
                t = w.text.strip()
                if SENT_END.search(t) and t.lower() not in ABBREV:
                    cut = i + 1
                    break
                if self._size(self.pending[: i + 1]) >= self._limit():
                    # Too long without a full stop: break at the last comma if there is a decent one.
                    clause = [j for j in range(i + 1) if CLAUSE_END.search(self.pending[j].text.strip())]
                    good = [j for j in clause if self._size(self.pending[: j + 1]) >= self._limit() * 0.4]
                    cut = (good[-1] + 1) if good else i + 1
                    break
            if cut is None:
                if not force:
                    break
                cut = len(self.pending)
            words, self.pending = self.pending[:cut], self.pending[cut:]
            text = self._capital(_join(words))
            if text and text.lower() not in HALLUCINATIONS and not self._scrap(words):
                out.append(dict(text=text, start=words[0].start, end=words[-1].end))
                self.history.append(text)
                self.sentence_open = not SENT_END.search(text)
                self.last_word = words[-1]
            self.shown_until = max(self.shown_until, words[-1].end)
        return out

    def _scrap(self, words) -> bool:
        """A line of one or two words made from the tail of the line before ("Chapter 3." then "3.",
        "...our failure." then "Failure."). Real short lines like "Amen." take longer to say."""
        if len(words) > 2:
            return False
        if words[-1].end - words[0].start < 0.25:
            return True
        last = self.last_word
        return (last is not None and words[0].start - last.end < 2.5
                and _sounds_like(_norm(words[0].text), _norm(last.text)))

    def _capital(self, text: str) -> str:
        """A new sentence starts with a capital (Whisper often lower-cases it after the prompt)."""
        if self.sentence_open or not text:
            return text
        i = next((k for k, c in enumerate(text) if c.isalpha()), None)
        return text if i is None else text[:i] + text[i].upper() + text[i + 1:]

    def _trim(self):
        # The buffer starts where the last finished line ended. If old, already-shown speech stays in
        # it, Whisper hears it again and stamps the next sentence's first words with the old times,
        # and those words get thrown away as repeats ("You can bring..." came out "bring...").
        if self.shown_until > self.offset:
            self._trim_to(self.shown_until)
        if not self.pending and not self.hyp.buffer and self.hyp.last_time > self.offset:
            self._trim_to(self.hyp.last_time)
        if len(self.audio) / SR > self.max_buffer_sec:
            # Whisper is stuck (no agreement for a long time): show what we have and move on.
            self.pending += self.hyp.take_all()
            self._trim_to(self.now - 2.0)

    def _trim_to(self, t: float):
        t = min(max(t, self.offset), self.now)
        cut = int((t - self.offset) * SR)
        if cut > 0:
            self.audio = self.audio[cut:]
            self.offset += cut / SR
