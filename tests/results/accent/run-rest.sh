#!/bin/bash
# Finishes the accent comparison (finished runs are reused).
cd "$(dirname "$0")/../../.."
PY=~/venvs/riah-translate/bin/python
$PY tests/accent_compare.py --models base.en small.en large-v3-turbo Systran/faster-distil-whisper-large-v3 --clips dennis-sy:1200
cp tests/results/accent/summary.json tests/results/accent/summary-dennis.json
$PY tests/accent_compare.py --models base.en small.en --clips saquing:0 eliscupidez:0
cp tests/results/accent/summary.json tests/results/accent/summary-others.json
