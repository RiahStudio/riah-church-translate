"""Entry point for the packaged app (PyInstaller). Same as `python -m riah_translate`."""
import sys

# Every platform writes a log next to the models (2026-11-26: a Mac dropped out of the live
# screen mid-sermon and there was nothing on disk to say why - a Mac app's stdout goes
# nowhere, so "it has a stdout" was never the same as "a person can read it").
try:
    from riah_translate.models import app_data_dir

    folder = app_data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    _log = open(folder / "riah-translate.log", "w", encoding="utf-8", buffering=1)
except Exception:
    import os

    _log = None
if _log is not None:
    sys.stdout = sys.stdout or _log
    sys.stderr = sys.stderr or _log
    if sys.platform == "darwin":
        # The bundled Mac app can hold a stdout that nothing reads; send tracebacks to the file.
        sys.stdout = _log
        sys.stderr = _log

from riah_translate.app import main

if __name__ == "__main__":
    main()
