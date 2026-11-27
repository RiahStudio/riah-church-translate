"""Audio in: a live input device (soundboard feed or microphone) or a recorded file.

Everything is converted to 16 kHz mono float32, which is what Whisper wants.
"""
from __future__ import annotations

import queue
import threading
import time

import numpy as np

SR = 16000


def resample(x: np.ndarray, sr: int) -> np.ndarray:
    if sr == SR or len(x) == 0:
        return x.astype(np.float32, copy=False)
    ratio = sr / SR
    k = int(round(ratio))
    if abs(ratio - k) < 1e-6 and k > 1:  # 48k / 32k: average k samples (crude low-pass) and decimate
        n = len(x) // k
        return x[: n * k].reshape(n, k).mean(axis=1).astype(np.float32)
    # 44.1k and friends: smooth a little, then linear interpolation
    w = max(1, int(ratio))
    if w > 1:
        x = np.convolve(x, np.ones(w) / w, mode="same")
    n_out = int(len(x) / ratio)
    return np.interp(np.arange(n_out) * ratio, np.arange(len(x)), x).astype(np.float32)


def list_input_devices():
    try:
        import sounddevice as sd
        default_in = sd.default.device[0] if sd.default.device else None
        # Windows lists each microphone once per audio driver (MME, DirectSound, WASAPI...); show only
        # the driver the system default uses. A Mac has one driver, so nothing changes there.
        try:
            host = sd.query_devices(default_in)["hostapi"] if default_in is not None and default_in >= 0 else None
        except Exception:
            host = None
        out = []
        for i, d in enumerate(sd.query_devices()):
            # "Microsoft Sound Mapper" is Windows' stand-in for the default microphone, already offered.
            if (d["max_input_channels"] > 0 and (host is None or d["hostapi"] == host)
                    and not d["name"].startswith("Microsoft Sound Mapper")):
                out.append(dict(id=i, name=d["name"], channels=d["max_input_channels"],
                                default=(i == default_in)))
        return out
    except Exception as e:  # PortAudio missing, no permission, etc.
        return [dict(id=None, name=f"No input devices ({e.__class__.__name__}: {e})", channels=0, default=True)]


class AutoLevel:
    """Evens out the sound level before Whisper, so a feed turned down low (or up hot) is simply
    translated instead of the operator being asked to fix it. It follows the level of speech, slowly,
    and ignores near-silence, so quiet gaps between sentences are never pumped up into hiss."""

    TARGET = 0.06        # speech level Whisper likes (RMS)
    FLOOR = 0.0003       # below this a stretch is silence: the gain stays where it is
    MAX_GAIN = 40.0      # about +32 dB: enough for a feed sent far too low, not enough to make noise loud
    MIN_GAIN = 0.5

    def __init__(self):
        self.speech = None        # running speech level
        self.gain = 1.0

    def __call__(self, x: np.ndarray) -> np.ndarray:
        if len(x) == 0:
            return x
        rms = float(np.sqrt(np.mean(x * x)))
        if rms > self.FLOOR:
            sec = len(x) / SR
            if self.speech is None:
                self.speech = rms
            else:   # rises within about a second (someone starts talking), falls over about five
                k = 1 - np.exp(-sec / (1.0 if rms > self.speech else 5.0))
                self.speech += (rms - self.speech) * k
            want = min(self.MAX_GAIN, max(self.MIN_GAIN, self.TARGET / self.speech))
        else:
            want = self.gain
        ramp = np.linspace(self.gain, want, len(x), dtype=np.float32)   # no clicks when it changes
        self.gain = want
        return np.clip(x * ramp, -1.0, 1.0)


class MicSource:
    """Live input. Opens the device at its own rate and converts, because many interfaces refuse 16 kHz.

    If the input goes away mid-service (a cable pulled, a USB board reset, Windows swapping devices)
    it keeps trying to open the same input again, every couple of seconds, until it is back or the
    operator presses Stop. `reconnecting` says when that is happening."""

    QUIET_SEC = 2.0     # no audio at all for this long: the input is gone

    def __init__(self, device=None, channel: int | None = None):
        self.device, self.channel = device, channel
        self.q: queue.Queue = queue.Queue()
        self.stream = None
        self.realtime = True
        self.name = None              # the input's name, to find it again if its number changes
        self.last = time.monotonic()  # when audio last arrived
        self.reconnecting = False
        self.next_try = 0.0

    def start(self):
        self._open(first=True)
        return self

    def _open(self, first=False):
        import sounddevice as sd
        device = self.device
        if not first:
            try:                      # forget the old device list, or a re-plugged input stays invisible
                sd._terminate()
                sd._initialize()
            except Exception:
                pass
            if self.name is not None:
                found = [i for i, d in enumerate(sd.query_devices())
                         if d["name"] == self.name and d["max_input_channels"] > 0]
                if found:
                    device = found[0]
        info = sd.query_devices(device, "input")
        if first:
            self.name = info["name"] if self.device is not None else None
        self.sr = int(info["default_samplerate"])
        have = int(info["max_input_channels"]) or 1
        channel = self.channel if self.channel is not None and 0 <= self.channel < have else None
        # Both channels of a stereo feed by default; a sound board's own USB input has one channel per
        # microphone, so open up to the one picked (the preacher's mic alone: no music, no room).
        chans = max(min(2, have), (channel or 0) + 1)

        def cb(indata, frames, t, status):
            x = indata[:, channel] if channel is not None else indata[:, :2].mean(axis=1)
            self.q.put(resample(np.asarray(x, dtype=np.float32), self.sr))

        self.stream = sd.InputStream(device=device, channels=chans, samplerate=self.sr,
                                     dtype="float32", blocksize=int(self.sr * 0.1), callback=cb)
        self.stream.start()
        self.last = time.monotonic()

    def read(self, timeout=0.2):
        chunks = []
        try:
            chunks.append(self.q.get(timeout=timeout))
            while True:
                chunks.append(self.q.get_nowait())
        except queue.Empty:
            pass
        now = time.monotonic()
        if chunks:
            self.last = now
            self.reconnecting = False
        elif now - self.last > self.QUIET_SEC and now >= self.next_try:
            self._reopen(now)
        return np.concatenate(chunks) if chunks else np.zeros(0, np.float32), False

    def _reopen(self, now):
        self.reconnecting = True
        self.next_try = now + 2.0
        self._close()
        try:
            self._open()
            print(f"sound input reopened: {self.name or 'default input'}", flush=True)
        except Exception as e:      # not back yet: try again in a moment
            print(f"sound input not back yet ({e.__class__.__name__}: {e})", flush=True)

    def _close(self):
        s, self.stream = self.stream, None
        if s is not None:
            try:
                s.abort(ignore_errors=True)
                s.close(ignore_errors=True)
            except Exception:
                pass

    def stop(self):
        self._close()


def _load_wav(path: str) -> np.ndarray:
    import wave
    with wave.open(path, "rb") as w:
        sr, ch, width, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
    if width == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"unsupported WAV sample width {width}")
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return resample(x, sr).astype(np.float32)


def _load_av(path: str) -> np.ndarray:
    # PyAV directly: faster-whisper's decode_audio passes an argument newer PyAV versions dropped.
    import av
    out = []
    with av.open(path) as f:
        rs = av.AudioResampler(format="s16", layout="mono", rate=SR)
        for frame in f.decode(audio=0):
            for fr in rs.resample(frame):
                out.append(fr.to_ndarray().reshape(-1))
        for fr in rs.resample(None):
            out.append(fr.to_ndarray().reshape(-1))
    if not out:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(out).astype(np.float32) / 32768.0


def load_file(path: str) -> np.ndarray:
    """Any recording (wav, mp3, m4a, mp4...) as 16 kHz mono float32."""
    if path.lower().endswith(".wav"):
        try:
            return _load_wav(path)
        except Exception:
            pass  # e.g. float or compressed WAV: let PyAV handle it
    return _load_av(path)


class FileSource:
    """A recording played as if it were live (realtime=True), for rehearsals and tests."""

    def __init__(self, path: str, realtime: bool = True, start_sec: float = 0.0):
        self.path, self.realtime = path, realtime
        self.audio = load_file(path)[int(start_sec * SR):]
        self.pos = 0
        self.t0 = None

    def start(self):
        self.t0 = time.monotonic()
        return self

    def read(self, timeout=0.2):
        """Returns (audio that has 'arrived' since the last read, finished?)."""
        if self.realtime:
            time.sleep(timeout)
            due = min(len(self.audio), int((time.monotonic() - self.t0) * SR))
        else:
            due = min(len(self.audio), self.pos + int(timeout * SR))
        chunk = self.audio[self.pos:due]
        self.pos = due
        return chunk, self.pos >= len(self.audio)

    def stop(self):
        pass

    @property
    def duration(self):
        return len(self.audio) / SR
