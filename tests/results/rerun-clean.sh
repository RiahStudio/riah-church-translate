#!/bin/sh
cd ~/cowork/"Riah Translate" || exit 1
PY=~/venvs/riah-translate/bin/python
"$PY" tests/clip_test.py tests/clips/sermon-en.wav --script tests/clips/sermon-en.txt --to th --hints "Chiang Mai, Nicodemus" --out tests/results/en-th-clean2.jsonl
