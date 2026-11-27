#!/usr/bin/env bash
# Makes the "room" version of a test clip: the clean recording plus echo and background noise,
# closer to a microphone in a hall than a clean soundboard feed. Runs on a Linux test machine.
set -euo pipefail
in="${1:-tests/clips/sermon-en.wav}"
out="${2:-tests/clips/sermon-en-room.wav}"
ffmpeg -y -loglevel error -i "$in" -f lavfi -i "anoisesrc=color=pink:amplitude=0.035:sample_rate=16000" \
  -filter_complex "[0:a]aecho=0.8:0.6:60|140:0.35|0.2,volume=0.9[v];[v][1:a]amix=inputs=2:duration=first:normalize=0[m]" \
  -map "[m]" -ar 16000 -ac 1 "$out"
echo "wrote $out"
