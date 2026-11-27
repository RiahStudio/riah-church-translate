"""Is a newer version out? Asked once, in the background, when the app starts.

Versions are dated, like 2026.11.28 (a second one the same day: 2026.11.28.2). The app asks GitHub which
release is the latest (the public repository the downloads come from) and compares. If a newer one is
out, the operator page shows a quiet line with one button that opens the download page, which picks
the right file for the computer. Nothing is sent but the request itself; offline, a slow network or any
odd answer just means no line, and it can never touch the service.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

from . import __version__
from .report import _ssl_context

LATEST_URL = os.environ.get("RIAH_TRANSLATE_LATEST_URL") or \
    "https://api.github.com/repos/RiahStudio/riah-church-translate/releases/latest"
PAGE = "https://riahstudio.com/churchtranslate/"      # where the one-button download lives
TIMEOUT = 6
MAX_BYTES = 262144

_VERSION = re.compile(r"v?(\d{1,4}(?:\.\d{1,4}){1,3})")


def parse(v) -> tuple | None:
    """'v2026.11.28' or '0.3.1' -> (2026, 11, 28). None for anything else."""
    m = _VERSION.fullmatch(v.strip()) if isinstance(v, str) else None
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def newer(latest, mine=__version__) -> bool:
    a, b = parse(latest), parse(mine)
    return a is not None and b is not None and a > b


def _fetch() -> dict:
    req = urllib.request.Request(LATEST_URL, headers={
        "Accept": "application/vnd.github+json", "User-Agent": f"RiahTranslate/{__version__}"})
    ctx = _ssl_context() if LATEST_URL.lower().startswith("https:") else None
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
        return json.loads(r.read(MAX_BYTES).decode("utf-8"))


def check(mine=__version__, fetch=None) -> dict | None:
    """{'version': '2026.11.29', 'url': PAGE} if a newer version is out; otherwise None. Never raises."""
    try:
        data = (fetch or _fetch)()
        tag = data.get("tag_name") if isinstance(data, dict) else None
        if isinstance(data, dict) and (data.get("draft") or data.get("prerelease")):
            return None
        if newer(tag, mine):
            return dict(version=".".join(str(n) for n in parse(tag)), url=PAGE)
    except Exception:
        pass
    return None
