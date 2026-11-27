# Riah Church Translate

Live subtitles for a church service. The preacher talks normally, and the church screen
shows what he said on one side and the translation on the other, a line at a time, a few
seconds behind him. No interpreter pausing every sentence, no subscription, no internet
needed once it's set up.

Works on a Mac or a Windows PC. Everything runs on that computer itself.

It also runs in a web browser with nothing to install: riahstudio.com/churchtranslate/live/
(Chrome or Edge on a recent computer). The speech and translation models download into the
browser once and run on the computer's own chip, in the background so the rest of the
computer stays usable; nothing said leaves the room. The site only serves a few small files,
so it costs nothing to host. Built from this folder by `tools/build_browser.py`.

One app, two names: **Riah Church Translate** (the app, and the demo at
riahstudio.com/churchtranslate) and **Riah Live Translate** (the same demo at
riahstudio.com/livetranslate). The folders, the code and the models folder keep the short name
Riah Translate.

## Before Sunday

1. **Get the sound in.** Best: the preacher's mic alone. A sound board with its own USB
   input for every mic: pick the board under **Listen to** and his mic under **Channel**.
   Otherwise a spare aux send with only his mic in it, into a cheap USB audio adapter. The
   board's main mix works too (the band comes through). A microphone near a speaker also
   works, with more mistakes. The app evens out the level by itself: a feed that comes in too
   quiet is turned up, a hot one turned down. If the sound drops out (a cable pulled, the board
   restarted) it keeps trying and picks it back up on its own.
2. **Open the app once at home.** The first launch downloads the speech and translation
   models (about 2 GB, once). After that it works offline.
3. **Words box.** Type the names and places the preacher may say this week
   ("Pastor Somchai, Chiang Mai, Ephesians"). It spells them right more often.
4. **Church words** are built in: Bible book names, Amen, Hallelujah, "let us pray" and
   other phrases come out the way Thai and Chinese churches say them. A Thai and a Chinese
   speaker should still read through them once (`riah_translate/church_words.py`).
5. **Bible names** are built in too: about 1,400 people and places and all 66 books, spelled
   the way the Thai Bible and the Chinese Union Version spell them. When the preacher says
   "Nicodemus" or "Second Corinthians 5", the subtitle uses the Bible's spelling. The list
   (`riah_translate/bible_words.json`) is made from public-domain Bibles by `tools/bible/`.
   It makes names right; it doesn't change speed. On 150 Bible verses it took Thai names
   spelled right from 42% to 60% and Chinese from 73% to 76%; Chinese names sounded out a
   little off (尼哥底马 for 尼哥德慕) are swapped too.
6. **Bible verses.** When the preacher reads a verse, the screen shows that verse from a real
   Bible in Thai or Chinese, with its place, for example "(ยอห์น 3:16)", instead of a machine
   translation. It still listens: if the preacher puts his own words in, that line is
   translated as usual. A verse is caught most surely right after he says where it is ("John
   3:16", "turn with me to Psalm 23"); well-known verses are caught without that too. The
   texts that ship (about 5 MB, `riah_translate/bible/`) are all public domain: English
   Berean Standard Bible, World English Bible and King James Version (to recognise the verse),
   the Thai Freedom Bible (draft) and the Chinese Union Version, Simplified and Traditional.
   Most Thai churches read the Thai Standard Version, which is copyrighted, so the Thai
   wording on screen will differ a little from what people know.
7. **Sermon notes** (txt, md, Word, PowerPoint, OpenOffice or PDF): press **Add notes** on
   the operator page. The app picks out the names to listen for and gets ready for the verses
   they list. Nobody reads them; they stay on this computer and are never sent anywhere or
   written to the logs. It still follows what the preacher actually says, so changes and
   asides come through as spoken. Keynote and Pages files need exporting to PDF or Word first.

The full checklist for a first Sunday is in `SUNDAY-CHECKLIST.md`.

## During the service

- Pick **Spoken language**, **Translate into** and, if you want a second one, **Also into**
  (for example English → Thai, also Chinese). Pick what to **Listen to**, press **Start**.
  The translations can be switched while live; the spoken language needs Stop and Start.
- **Where the subtitles go:** the whole church screen, a bar along the bottom, or a column on
  the right. The bar and the column float on top of PowerPoint, Keynote or ProPresenter running
  full screen; where nobody is talking, the slides show through. In Keynote, tick Settings →
  Slideshow → "Allow Mission Control, Dock and others to use screen" so the bar can sit on top.
- **Show the spoken words too** puts the preacher's own words beside the translations.
  Untick it for a Thai-only screen with bigger text.
- **Open church screen** puts the subtitle view on the projector (on a Mac with a second
  display it goes there full screen). On any browser: press **F** or double-click for full
  screen.
- Words appear as soon as they're heard, in a softer white, and the translation fills in
  while the sentence is still going. When the sentence ends it settles to full white.
- **Hide subtitles** during songs: the screen goes dark, the app keeps listening. Press
  **Show subtitles** when the preaching starts again. **Clear the screen** empties it.
- The operator page shows a live preview of the church screen, plus text size, how many
  lines show and which side the translation sits on.
- The display won't go to sleep while the app is live, on a Mac or a PC (untick **Keep the
  screen from sleeping** to allow it).
- Every service is saved as a text file with the time, what was said and each translation:
  **Saved transcripts** at the bottom of the operator page. Give it to a Thai or Chinese
  speaker to spot wrong words; those go in the church words list.
- When the app is stopped and the screen is empty, the screen shows a quiet Riah card.

## How good is it

Measured on a recorded 80-second test sermon (a clear computer voice reading a short
sermon, clean and with room echo and noise added). A real preacher in a real room will
score a bit lower.

- English heard correctly: about 97–98% of words, clean or through the room mic. Names in
  the Words box come out right.
- On the built Mac app (GitHub's Apple-silicon test machine): each English line appears
  about 2 seconds after it's spoken, its Thai about 3½ seconds; never more than 6.
- On a 4-core Windows laptop CPU: about 3 seconds for English, 4–6 for the translation.
- Translation is good enough to follow along. It is not a trained interpreter: idioms and
  long winding sentences come out flat, and a word is sometimes wrong.

**Thai or Chinese preacher → English** works but needs a much bigger speech model. On an
older laptop it will run behind; a recent Apple-silicon Mac should keep up (not tested yet).

## Extras

- **Translator → Local AI with Ollama**: if the laptop has Ollama installed, a local AI
  model can do the translation instead. Usually more natural, slower, still free.
- **Other devices**: start with `--lan` and phones or a second computer on the church
  Wi-Fi can open `http://<laptop address>:8765/screen` read-only.
- More languages are listed in `riah_translate/langs.py` (Burmese, Lao, Khmer, Korean,
  Japanese, Spanish and more).

## For people working on the code

```
pip install -r requirements-app.txt
python -m riah_translate                       # operator page + church screen
python -m riah_translate --file sermon.mp3     # rehearse with a recording
python -m riah_translate --offline clip.wav --out run.jsonl   # test run, no screen
python -m riah_translate --replay run.jsonl --loop            # show a recorded run
```

Bible names: `tools/bible/fetch.sh` downloads the Bibles, `build_bible_words.py` lines them up
into `bible_words.json`, then `probe_bible_words.py` adds the translator's known misspellings
(always run it after a build; it rewrites the file). `bench_bible_words.py` measures names
right and seconds per line at each beam size. `build_verses.py` packs the verse texts into
`riah_translate/bible/` (it only takes Bibles marked public domain). `python tests/test_known.py`
checks verses and notes without any models.

Demo page (riahstudio.com/churchtranslate): `python tools/build_demo.py <run.jsonl> <out dir>`
turns a recorded run into a static page that plays it on the real church screen.

Tests: `tests/clip_test.py` runs a recorded sermon through everything and checks word
accuracy and delay. `python tests/test_logic.py` checks the app's logic without any models.
`KNOWN-ISSUES.md` lists what isn't right yet or hasn't been tried.

Free and open source under the MIT licence (`LICENSE`); the translation model it downloads is
non-commercial, so it's free for churches but can't be sold as a translation service.

The Mac app is built by GitHub Actions (`.github/workflows/mac-build.yml`), which also runs
the test sermon through the built app and opens its pages. It builds twice: `Riah-Church-Translate-mac`
for Apple-silicon Macs (M1 and newer) and `Riah-Church-Translate-mac-intel` for Intel Macs, macOS 11
(Big Sur) or later; the build fails if any library inside needs newer. On a slow Mac the app
checks its own speed and drops to a lighter speech model (small.en → base.en → tiny.en) and, if finished lines keep coming late, a narrower translation
search. It isn't signed by Apple yet, so the first time:
right-click the app, Open, then System Settings > Privacy & Security > Open Anyway (on macOS 11
and 12: System Preferences > Security & Privacy).

The Windows app is built the same way (`.github/workflows/windows-build.yml`): it runs the test
sermon through the built `.exe`, opens its pages, opens its real window (Edge WebView2, part of
Windows 10 and 11) and takes a picture of it. Download `Riah-Church-Translate-windows`, unzip, and
double-click **Riah Church Translate**. Not signed yet, so the first time Windows says "Windows protected
your PC": More info > Run anyway. Models go in `%APPDATA%\Riah Translate\models`, and a log of
the latest run in `%APPDATA%\Riah Translate\riah-translate.log`. If a PC has no WebView2 the app
opens in the browser instead.

`.github/workflows/demo-check.yml` opens the public demo page in Chromium (Chrome, Edge), WebKit
(Safari) and Firefox, at laptop and phone sizes.
