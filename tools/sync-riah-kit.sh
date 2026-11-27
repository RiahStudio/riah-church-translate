#!/usr/bin/env bash
# Refreshes the app's copy of the Riah interface kit and fonts from the live studio files.
# The app ships on its own (a church Mac has no studio folder), so it carries a copy; run this
# whenever MASTER.md / riah-interface.css change. The server prefers the live kit when it can see it.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
studio="$here/../Riah Studio"
kit="$studio/brand/design-system/riah-interface.css"
fonts="$studio/app/public/usage/assets/fonts"
dest="$here/riah_translate/web/vendor"
mkdir -p "$dest/fonts"
cp "$kit" "$dest/riah-interface.css"
cp "$fonts/fonts.css" "$fonts"/*.woff2 "$fonts"/LICENSE-*.txt "$dest/fonts/"
echo "synced kit + fonts into $dest"
