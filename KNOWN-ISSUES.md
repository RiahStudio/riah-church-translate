# Known issues

What we know isn't right yet, or hasn't been tried. Worth reading before a church relies on it.

## Translation

- It is machine translation. Good enough to follow a sermon, not a trained interpreter: idioms,
  jokes and long winding sentences come out flat, and a word is sometimes wrong. Have someone who
  reads the language watch the first Sunday.
- Thai Bible verses come from the Thai Freedom Bible (a public-domain draft). Most Thai churches
  read the Thai Standard Version, which is copyrighted, so the wording on screen differs a little
  from what people know.
- Accuracy numbers in the README come from a recorded test sermon read by a computer voice. A real
  preacher in a real room will score lower. Only a few real sermons have been tried so far.
- A Thai or Chinese preacher translated into English needs a much bigger speech model and has not
  been tested on a slow computer.

## The app

- A single failed speech pass used to end the whole service: the subtitle screen went
  idle and the operator was back at the language screen mid-sermon (seen on a Mac,
  2026-11-26). Fixed, and hardened on 2026-11-27 so that only Stop ends a live service:
  every step of the live loop (meters, auto-level, translation jobs, drafts, screen updates)
  is contained, so a fault costs that step, not the sermon. A failed speech pass costs only
  that pass's words and climbs a ladder: tidy, start the hearing afresh (3 in a row), load
  the speech model again (6), rebuild the session (10). The sound input and the translations
  keep running throughout, and once the service has been live it starts again after any
  error (1 s, then 2, 4, up to 8 s apart). Mumbling, silence, stepping off the mic and
  bumps are ordinary input, not faults: nothing resets or restarts on them (a test plays six
  minutes of them), and the mic only reopens when no sound arrives at all, i.e. the input
  itself is gone. A **Restart** button (next to Stop, for the sound person) starts the hearing
  afresh at once, finishes the words heard so far as lines first, and swaps in a new sound
  input when it has opened (it can take a second or two; if it won't open, the old one carries
  on). The screen keeps its lines. The speech engine runs in its own process, watched by the app:
  if it crashes outright (a segfault, the system ending it) or stops turning for 10 s (a speech
  pass that hangs), the app ends it, starts a new one and carries on the same service: the screen
  keeps its lines and the numbering carries on (it takes as long as loading the models, about 30
  s on a 4-core test machine, longer on a slow laptop). Silence and mumbling never count as a
  hang: the engine reports that its loop is turning, not that someone is talking. If Restart is
  ignored the app swaps the engine after 4 s; if Stop can't get an answer it ends the engine
  after 6 s. A process that keeps crashing is restarted with growing pauses (up to 15 s), never
  in a tight loop. If a second process can't be started on that computer, the engine runs inside
  the app as before (`--no-isolate` does that on purpose). Tests inject faults into every stage,
  including a ten-minute random-fault run and `tests/test_isolate.py` (crash, hang, silence, a
  Restart or Stop that gets no answer, crash loop). The app itself crashing (not the engine) still
  needs a relaunch by hand; the log is `riah-translate.log` (engine: `riah-engine.log`) next to
  the models (`~/Library/Application Support/Riah Translate` on a Mac) so any failure leaves its
  traceback on disk. The preacher's Mac build predates all of this.
- Problem reports are sent by the person, never automatically: after a problem the app offers
  **Send a problem report**, shows exactly what would go (what broke and when; never sound, words,
  notes or anything typed), and sends it to riahstudio.com on their click. If they are offline it
  saves a small file they can send later. Nothing is sent without that click.

- The Mac and Windows builds are not signed yet. macOS and Windows both warn on first open; the
  Sunday checklist says how to get past it.
- The subtitle bar and side column that sit on top of PowerPoint, Keynote or ProPresenter have been
  checked in the app's own preview, not yet on a real projector with each slideshow program running
  full screen. Keynote needs "Allow Mission Control, Dock and others to use screen" ticked.
- Keynote and Pages files can't be added as sermon notes directly; export them to PDF or Word.

## The browser version

- Needs a recent computer and Chrome or Edge. The first start downloads several hundred MB of
  models into the browser; after that they load from the browser's cache.
- By default it runs a smaller speech model than the desktop app picks on a fast computer, so
  expect a few more wrong words. "More accurate" switches to the bigger one; it needs a strong graphics card.
- Safari and Firefox are untested.

## When not to use it

- Anything where a wrong word could do harm: legal, medical, or pastoral counselling. Use a human
  interpreter for those.
- Selling it as a paid translation service: the translation model's licence is non-commercial.
