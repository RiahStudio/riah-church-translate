# How live conference subtitles work, and what we built

Written 2026-11-22 for Captain's ask: replace back-and-forth interpreting at church with
subtitles on the screen, like the good ones at Chinese tech conferences.

## What conferences actually use

The subtitles at big conferences are a chain of three things: the stage audio goes into
speech recognition, each finished sentence goes through machine translation, and the
result is drawn on the big screen, original and translation together. In China that's
usually iFlytek or Tencent; elsewhere Wordly, Interprefy, KUDO or Microsoft's live
translator. They are good mostly because they get a clean feed straight from the sound
desk and they're tuned for the event's names and jargon. All of them are paid
services, charged by the hour or the event, and they need internet.

So the cheap version is the same chain, run on a laptop with free models.

## Free pieces we looked at

| Piece | What it is | License | Verdict |
|---|---|---|---|
| faster-whisper | OpenAI's Whisper speech model, made fast on a normal CPU | MIT | **Used** for hearing |
| whisper_streaming | The trick for live Whisper: only show words two passes agree on | MIT | **Used** (the method, rewritten) |
| WhisperLiveKit | Ready-made live-caption web server on Whisper | Apache-2.0 | Closest to a fork, but built for a graphics card and has no side-by-side screen; borrowed ideas instead |
| WhisperLive | Live Whisper server | MIT | Same story |
| whisper.cpp | Whisper in C++, very fast on Macs | MIT | Good future swap for Apple silicon |
| NLLB-200 (Meta) | One translation model for 200 languages, Thai and Chinese included | CC-BY-NC-4.0 | **Used** for translating; free for a church, can't be sold |
| Argos Translate | Offline translation | MIT | Weaker Thai |
| SeamlessStreaming (Meta) | Speech-to-translated-speech in one model | CC-BY-NC | Needs a big graphics card; too heavy for a church laptop |
| LiveCaptions | Desktop caption app | GPL | Linux and English only |

All are well-known projects with public code.

## What we picked, and why

faster-whisper hears the preacher, NLLB-200 translates each line, and our own small
web page draws the two columns. Everything runs on the laptop: no account, no monthly
bill, no internet after the first download (~2 GB). It's a small Python app with a
Mac build, so a church can install it like any other app.

We didn't fork a whole project because none had what Captain asked for: side-by-side
columns sized for a projector, Thai handled properly (Thai has no spaces between words,
and the models get that wrong), and a Mac app. Taking the proven parts and writing the
glue was less work than bending a fork.

## What the test sermon showed

The test sermon is 80 seconds written for this (`tests/clips/sermon-en.txt`), read by
the Windows text-to-speech voice "Microsoft David" at rate −1, saved as 16 kHz mono
(`sermon-en.wav`). The room version adds echo and background noise. A computer voice is
easier than a real preacher, so treat these as best-case numbers.

- English heard correctly: 97–98% of words, clean and through the room version.
- Built Mac app on GitHub's Apple-silicon runner: original line ~2 s after it's spoken
  (worst 4 s), Thai ~3.4 s (worst 6 s). The speed check picked the fast speech model
  there; a real M-series Mac is quicker than that shared test machine.
- 4-core laptop CPU (the Thinkbox, a fair stand-in for an older church laptop): ~3 s for
  the original, 4–6 s for the translation. Slower when something else is running.
- Fixed along the way: first words of a sentence getting dropped, stray one-word lines
  ("On.", "3.", "Failure.") made from the end of the line before, and a few lines NLLB got
  badly wrong because of capital letters.

Thai or Chinese **spoken** (translated into English) needs Whisper's big multilingual
model. It works, but a CPU-only laptop runs behind; a recent Mac should be fine.

## Advice from Hermes that's built in

- Take the sound from the sound desk, not a room mic.
- Keep a words list for names and Bible books (the Words box).
- Show the line still being spoken dimmer, so people know it may change.
- Never cover the original column; give both languages the same text size.

## If we want it better later

- A local AI translator (Ollama, free) for more natural Thai. Already an option.
- whisper.cpp with Apple's Metal for faster, more accurate hearing on Macs.
- A church-wide glossary of fixed translations (the app already reads one from
  `glossary.json`, there's just no screen for it yet).
