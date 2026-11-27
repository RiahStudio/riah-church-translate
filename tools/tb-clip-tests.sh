#!/usr/bin/env bash
# Runs the recorded-clip checks on the Thinkbox (CPU only, 4 cores - a fair stand-in for a church laptop).
#   node "_shared-agent-rules/bin/thinkbox-run.mjs" --bg rt-clips --cwd "Riah Translate" -- bash tools/tb-clip-tests.sh [quick]
set -uo pipefail
PY="$HOME/venvs/riah-translate/bin/python"
mkdir -p tests/results
[ -f tests/clips/sermon-en-room.wav ] || bash tools/make-room-clip.sh
status=0
run() { name="$1"; shift; echo; echo "=== $name ==="; "$PY" tests/clip_test.py "$@" --out "tests/results/$name.jsonl" || status=1; }
HINTS="Chiang Mai, Nicodemus"
# The Words box is filled the way a church would. Thai + Chinese together is how the church runs it.
"$PY" tests/test_logic.py || status=1
"$PY" tests/test_isolate.py || status=1
if [ "${1:-}" = "board" ]; then
  # A sound board feed set too low (30 dB down) or too high (18 dB up, clipping).
  ffmpeg -y -loglevel error -i tests/clips/sermon-en.wav -af volume=-30dB -ar 16000 -ac 1 tests/clips/sermon-en-quiet.wav
  ffmpeg -y -loglevel error -i tests/clips/sermon-en.wav -af volume=18dB -ar 16000 -ac 1 tests/clips/sermon-en-hot.wav
  run en-th-quiet tests/clips/sermon-en-quiet.wav --script tests/clips/sermon-en.txt --to th --hints "$HINTS"
  run en-th-hot tests/clips/sermon-en-hot.wav --script tests/clips/sermon-en.txt --to th --hints "$HINTS"
  exit $status
fi
run en-th-clean tests/clips/sermon-en.wav --script tests/clips/sermon-en.txt --to th --hints "$HINTS"
if [ "${1:-}" != "quick" ]; then
  run en-thzh-clean tests/clips/sermon-en.wav --script tests/clips/sermon-en.txt --to th zh --hints "$HINTS"
  run en-th-nodrafts tests/clips/sermon-en.wav --script tests/clips/sermon-en.txt --to th --hints "$HINTS" --no-drafts
  run en-th-room tests/clips/sermon-en-room.wav --script tests/clips/sermon-en.txt --to th --max-wer 0.25 --hints "$HINTS"
fi
exit $status
