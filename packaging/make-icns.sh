#!/usr/bin/env bash
# Mac only: turns the app icon (Riah Icons pack, riah-ext-translate.svg) into packaging/icon.icns.
# Needs rsvg-convert (brew install librsvg), sips and iconutil (built into macOS).
set -euo pipefail
cd "$(dirname "$0")/.."
SVG=riah_translate/web/icon.svg
SET=$(mktemp -d)/icon.iconset
mkdir -p "$SET"
rsvg-convert -w 1024 -h 1024 "$SVG" -o "$SET/icon_512x512@2x.png"
for s in 16 32 128 256 512; do
  sips -z $s $s "$SET/icon_512x512@2x.png" --out "$SET/icon_${s}x${s}.png" >/dev/null
  d=$((s * 2))
  [ $d -le 1024 ] && sips -z $d $d "$SET/icon_512x512@2x.png" --out "$SET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$SET" -o packaging/icon.icns
echo "packaging/icon.icns"
