"""Model download and caching (first run only).

Models live in the Hugging Face cache. The Mac app points that cache at
~/Library/Application Support/Riah Translate/models so it is easy to find and delete.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_whisper_cache = {}


def app_data_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Riah Translate"
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home())) / "Riah Translate"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "riah-translate"
    base.mkdir(parents=True, exist_ok=True)
    return base


def use_app_model_dir():
    """Frozen app: keep models in the app's own data folder instead of the shared HF cache."""
    os.environ.setdefault("HF_HOME", str(app_data_dir() / "models"))
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")


class DownloadFailed(RuntimeError):
    """Shown to the operator as is, so it is written in plain words."""


# Rough download sizes, so the operator knows how long the first Start will take.
SIZES = {"faster-whisper-tiny.en": "75 MB", "faster-whisper-base.en": "150 MB", "faster-whisper-small.en": "480 MB", "faster-whisper-small": "480 MB",
         "faster-whisper-large-v3-turbo": "1.6 GB", "nllb-200-distilled-1.3B-ct2-int8": "1.4 GB"}


# Each model is fetched at a fixed, checked version, so a later change to a model page can't change
# what a church gets. A name not listed here (a developer trying another model) takes the newest.
REVISIONS = {
    "Systran/faster-whisper-tiny.en": "0d3d19a32d3338f10357c0889762bd8d64bbdeba",
    "Systran/faster-whisper-base.en": "3d3d5dee26484f91867d81cb899cfcf72b96be6c",
    "Systran/faster-whisper-small.en": "d1d751a5f8271d482d14ca55d9e2deeebbae577f",
    "Systran/faster-whisper-small": "536b0662742c02347bc0e980a01041f333bce120",
    "dropbox-dash/faster-whisper-large-v3-turbo": "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
    "OpenNMT/nllb-200-distilled-1.3B-ct2-int8": "70f572adafa4794890ce7826156a4209717855af",
}


def ensure_model(repo: str, label: str, on_status=None) -> str:
    from huggingface_hub import snapshot_download
    rev = REVISIONS.get(repo)
    try:
        return snapshot_download(repo, revision=rev, local_files_only=True)
    except Exception:
        pass
    size = SIZES.get(repo.split("/")[-1])
    if on_status:
        about = f" (about {size})" if size else ""
        on_status("loading", f"Downloading the {label}{about}. This happens once, the first time, and can take "
                             "a few minutes. Keep the internet on until it says Live.")
    try:
        return snapshot_download(repo, revision=rev)
    except Exception as e:
        raise DownloadFailed(f"Couldn't download the {label}. Check the internet connection, then press "
                             "Start again. It carries on from where it stopped.") from e


def whisper_repo(name: str) -> str:
    if "/" in name:
        return name
    special = {"large-v3-turbo": "dropbox-dash/faster-whisper-large-v3-turbo",   # was mobiuslabsgmbh
               "turbo": "dropbox-dash/faster-whisper-large-v3-turbo"}
    return special.get(name, f"Systran/faster-whisper-{name}")


FAST_ENOUGH_SEC = 0.8   # one quick test pass; a full live pass costs a bit more than this


def _pass_seconds(model) -> float:
    """How long one Whisper pass takes on this computer (6 s of faint noise, second of two runs)."""
    import numpy as np
    x = (np.random.default_rng(0).standard_normal(16000 * 6) * 0.01).astype(np.float32)
    took = 0.0
    for _ in range(2):
        t0 = time.perf_counter()
        segs, _ = model.transcribe(x, language="en", beam_size=1, without_timestamps=True, max_new_tokens=4,
                                   vad_filter=False, condition_on_previous_text=False, temperature=0.0)
        list(segs)
        took = time.perf_counter() - t0
    return took


def choose_whisper(src: str, device: str = "cpu", threads: int = 4, on_status=None, log=None) -> str:
    """'Auto' speech model: the most accurate one this computer can run live.
    English: small.en when a pass is quick enough (Apple silicon, recent Intel), else base.en (older
    laptops), else tiny.en (very old or very busy Macs: rougher words, but it keeps up)."""
    from .langs import default_whisper_model
    name = default_whisper_model(src)
    if name != "small.en" or device == "cuda":
        return name
    if on_status:
        on_status("loading", "Checking this computer's speed…")
    secs = _pass_seconds(load_whisper("small.en", device, threads, on_status))
    pick = "small.en" if secs <= FAST_ENOUGH_SEC else "base.en"
    note = f"small.en pass {secs:.2f}s"
    if pick == "base.en" and secs > FAST_ENOUGH_SEC * 2.5:
        # base.en is about 3x quicker than small.en; when small.en was far too slow, check base.en too.
        base = _pass_seconds(load_whisper("base.en", device, threads, on_status))
        note += f", base.en pass {base:.2f}s"
        if base > FAST_ENOUGH_SEC:
            pick = "tiny.en"
    if pick != "small.en":
        _whisper_cache.pop(("small.en", device), None)   # free the memory on a small machine
    if log:
        log(f"speed check: {note} -> using {pick}")
    return pick


def load_whisper(name: str, device: str = "cpu", threads: int = 4, on_status=None):
    key = (name, device)
    if key not in _whisper_cache:
        from faster_whisper import WhisperModel
        path = ensure_model(whisper_repo(name), "speech model", on_status)
        if on_status:
            on_status("loading", "Loading the speech model…")
        compute = "int8_float16" if device == "cuda" else "int8"
        _whisper_cache[key] = WhisperModel(path, device=device, compute_type=compute, cpu_threads=threads)
    return _whisper_cache[key]


def forget_whisper(name: str, device: str = "cpu"):
    """Drop the loaded speech model so the next load_whisper reads it fresh from disk. The last
    repair when passes keep failing: a model left in a bad state (memory pressure, a half-failed
    pass) is replaced instead of reused."""
    _whisper_cache.pop((name, device), None)
