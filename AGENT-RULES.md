# Riah Translate — rules for agents

Live sermon subtitles for churches. The pastor's words and the translation run side by
side on the church screen, so nobody has to stop every sentence for an interpreter.
Captain asked for it on 2026-11-22 ("Can we build a cheap one?", "build the translate
app for Mac"). Read `README.md` for how it works and `RESEARCH.md` for why it is built
this way.

## How it fits together

- `riah_translate/asr.py` turns speech into lines. faster-whisper re-reads the last few
  seconds about once a second, and a word only goes on screen once two passes agree
  (LocalAgreement-2, from ufal/whisper_streaming). A line ends at a full stop, a pause,
  or 22 words. The buffer always restarts where the last shown line ended
  (`shown_until`); without that the first words of the next sentence get dropped.
- `riah_translate/mt.py` translates each line with NLLB-200 (CTranslate2, int8). When
  NLLB's own score is poor (< -0.75) it retries with the first letter's case flipped
  and keeps the better one; that fixed real wrong lines on the test sermon.
  `tidy()` removes the spaces NLLB puts between Thai words.
- `engine.py` runs listening and translating on two threads; `hub.py` fans events out
  to every open screen over `/events` (server-sent events).
- `server.py` serves the operator page (`/`, this computer only) and the church screen
  (`/screen`). Only this computer can change anything. Keep the guards: a foreign Host
  or Origin gets 403, POSTs must be JSON (≤64 KB), HTML carries a strict CSP, so no
  inline scripts or `style=` attributes in the pages.
- `web/screen.*` is the projector view: two columns, one row per line so the original
  and its translation stay level, rows glide up (FLIP), translations fade in after
  the glide. `web/control.*` is the operator page.

## Working on it

- Preview without models or a microphone: start `riah-translate-replay` from the root
  `.claude/launch.json` (port 5266). It replays `tests/results/en-th-clean.jsonl`.
- Never use `--ui browser` on Captain's PC (it opens an OS browser window). Use
  `--ui none` and the browser pane.
- Ports 8765 and 8766 belong to other programs on the PC; the app moves to the next
  free port on its own.
- Speech tests are heavy. Run them on the Thinkbox, never on the PC:
  `node "_shared-agent-rules/bin/thinkbox-run.mjs" --bg rt-clips --cwd "Riah Translate" -- bash tools/tb-clip-tests.sh`
  First time there: `tools/thinkbox-setup.sh`. Results land in `tests/results/`.
- Never play audio to check anything. Tests feed recorded files straight into the
  pipeline; nothing is ever played out loud.
- Testing animations in a hidden browser pane: animations sit at time 0 because no
  frames are drawn. Scrub `animation.currentTime` by hand instead of waiting.
- Shell scripts must keep LF line endings (`.gitattributes` enforces it). A CRLF copy
  fails on the Thinkbox with "set: pipefail: invalid option name".
- Styling: the pages use the live Riah kit (`Riah Studio/brand/design-system/riah-interface.css`)
  when run inside the studio; a shipped app uses the copy in `web/vendor/`, refreshed
  with `tools/sync-riah-kit.sh`. Run `riah-style-check.mjs` on both pages after a
  visual change. The church screen stays pure black with near-white text on purpose
  (projectors wash out grey).
- PyAV 19 dropped an argument faster-whisper's `decode_audio` uses, so
  `audio.py` decodes files with PyAV directly. Keep it that way.

## Licenses (keep it clean)

Code we depend on is MIT, Apache-2.0 or BSD. The NLLB-200 translation model is
CC-BY-NC-4.0: fine for a church using it free, **not** for selling the app. If this
is ever sold, swap the translator first (the LLM option, or another model).
Only add well-known, reputable libraries, and record the license in the requirements file.

## Mac app

Built on GitHub Actions (`.github/workflows/mac-build.yml`, run by hand) because there
is no Mac in the studio. The repo `riah-translate` is **private**; check
`node "_shared-agent-rules/bin/repo-map.mjs" where "Riah Translate"` before any
visibility change. Models download on first launch into
`~/Library/Application Support/Riah Translate/models`.
