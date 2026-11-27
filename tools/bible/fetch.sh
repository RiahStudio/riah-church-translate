#!/bin/bash
# Fetches the open Bibles the Bible word list is built from (ebible.org, verse-per-line).
# Usage: bash fetch.sh [folder]   (default ~/bible-data). The texts stay out of git.
set -e
dest="${1:-$HOME/bible-data}"
mkdir -p "$dest" && cd "$dest"
for id in engwebp engbsb eng-kjv thafb thaKJV cmn-cu89s cmn-cu89t; do
  [ -s "${id}_vpl.zip" ] || curl -sfL -o "${id}_vpl.zip" "https://ebible.org/Scriptures/${id}_vpl.zip" || echo "FAIL $id"
  mkdir -p "$id" && unzip -o -q "${id}_vpl.zip" -d "$id"
done
# Book names come from the USFM headers (\h / 	oc lines).
for id in thafb thaKJV cmn-cu89s cmn-cu89t; do
  [ -s "${id}_usfm.zip" ] || curl -sfL -o "${id}_usfm.zip" "https://ebible.org/Scriptures/${id}_usfm.zip" || echo "FAIL $id usfm"
  mkdir -p "$id/usfm" && unzip -o -q "${id}_usfm.zip" -d "$id/usfm"
done
ls -la "$dest"
for id in engwebp engbsb eng-kjv thafb thaKJV cmn-cu89s cmn-cu89t; do echo "== $id"; ls "$id" | head -5; done
