#!/usr/bin/env bash
# Pulls real sermons by Filipino pastors preaching in English, for the accent tests.
# Run on the Thinkbox (yt-dlp lives in ~/venvs/ytdlp):
#   node "_shared-agent-rules/bin/thinkbox-run.mjs" --cwd "Riah Translate" -- bash tools/fetch-accent-clips.sh
# Files land in tests/clips/real/ (ignored by git: these sermons aren't ours to share).
set -uo pipefail
Y="$HOME/venvs/ytdlp/bin/yt-dlp"
OUT=tests/clips/real
mkdir -p "$OUT"
# name | video id | section to keep (empty = whole video)
CLIPS="
tanchi-full|ILmAUuhuKoQ|
dennis-sy|F3mCaRlkBD0|20:00-26:00
saquing|4zlYBmjNFBw|45:00-51:00
eliscupidez|m0_0Rehi5js|15:00-21:00
"
echo "$CLIPS" | while IFS='|' read -r name id part; do
  [ -n "$name" ] || continue
  [ -f "$OUT/$name.wav" ] && { echo "have $name"; continue; }
  args=()
  [ -n "$part" ] && args=(--download-sections "*$part")
  "$Y" -q --no-warnings -f bestaudio "${args[@]}" -o "$OUT/$name.%(ext)s" \
    --ignore-errors --sleep-subtitles 8 --write-auto-subs --sub-langs en-orig --sub-format vtt \
    "https://www.youtube.com/watch?v=$id" || echo "note: $name had errors (often just the captions)"
  ls "$OUT/$name".* 2>/dev/null | grep -qvE ".(vtt|wav)$" || { echo "FAILED $name"; continue; }
  src=$(ls "$OUT/$name".* | grep -vE '\.(vtt|wav)$' | head -1)
  ffmpeg -nostdin -loglevel error -y -i "$src" -ar 16000 -ac 1 "$OUT/$name.wav" && rm -f "$src"
  echo "got $name: $(ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT/$name.wav") s"
done
ls -la "$OUT"
