#!/usr/bin/env bash
# One-time setup on the Thinkbox for test runs: a private venv plus the two default models.
# Run from the PC with:
#   node "_shared-agent-rules/bin/thinkbox-run.mjs" --bg rt-setup --cwd "Riah Translate" -- bash tools/thinkbox-setup.sh
set -euo pipefail
VENV="$HOME/venvs/riah-translate"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r requirements-engine.txt
"$VENV/bin/python" - <<'PY'
from huggingface_hub import snapshot_download
for repo in ("Systran/faster-whisper-small.en", "OpenNMT/nllb-200-distilled-1.3B-ct2-int8"):
    print(repo, "->", snapshot_download(repo), flush=True)
PY
"$VENV/bin/pip" list 2>/dev/null | grep -iE "whisper|ctranslate|tokenizers|onnxruntime|^av " || true
