# Making Riah Translate faster (ideas, 2026-11-23)

Where it stands: on GitHub's Apple-silicon Mac, an English line shows about 2 seconds after
it's spoken and its Thai about 3½ seconds. On a 4-core Windows laptop, about 3 seconds and 4 to
6. Paid church products sit in the same 2 to 5 second range. Today speech (Whisper small.en)
and translation (NLLB 1.3B) both run on the computer's ordinary processor.

Captain asked two things: how to speed it up, and whether it could run straight off the website.
These are the ideas, best payoff first. None of them are built yet.

## 1. Know the sermon before it's preached

If the pastor shares his notes or slides on Saturday, translate them carefully the night before
(a big model, or a Thai speaker checks them). On Sunday, each sentence he says is matched to the
nearest prepared sentence and that translation shows at once. Lines he reads from his notes
come up with no translation wait at all and in better Thai; anything he improvises goes
through the live path as now. This is the biggest win for the least risk, and it's also the
biggest accuracy win.

## 2. Show the real verse when he reads Scripture

When he says "John 3:16" or starts reading a verse, show that verse from a real Bible in
Thai and Chinese instead of machine-translating it. The Chinese Union Version is public domain.
Thai needs either a licence for a modern Thai Bible or the old public-domain one. Right now the
rule is to ship only the word list, never Bible text, so this one needs Captain's OK and a
licence check first.

## 3. Use the Mac's graphics chip and Neural Engine

Speech recognition is the slow part, and it's the part that suits Apple silicon best.
whisper.cpp with Apple's Core ML, or Whisper on Apple's MLX library, runs several times faster
than on the processor alone. That also leaves the processor free for translation. It only helps
on M-series Macs (Intel Macs and PCs keep today's path). A PC with an NVIDIA graphics card can
already use its card: the app takes `--device cuda`.

## 4. Speech models made for live captions

Whisper was built for recordings. It works live by re-reading the last few seconds again and
again. Newer models are made to stream: they put out each word once as it's heard.

- **Moonshine:** small and quick on an ordinary processor, made for live captions.
- **NVIDIA Parakeet:** English only, very accurate and very fast.
- **Kyutai's streaming speech model:** about half a second behind the speaker.

Any of these could cut the "first words on screen" delay roughly in half. Each needs testing
on accented English before it replaces Whisper.

## 5. Translate in smaller pieces, with a quick draft first

Drafts already appear mid-sentence. A smaller translator (NLLB 600M, or a one-pair Thai
model) could write those drafts faster, with the big one still doing the final line. Splitting
long sentences at commas and "and"/"but" also gets Thai on screen sooner.

## 6. Speech straight to Thai in one step

Meta's SeamlessStreaming turns English speech into Thai text directly, with about 2 seconds of
delay, and skips the hand-off between two models. Its licence is non-commercial, so it fits a
church but not a product the studio sells. Worth a test, not a switch.

## Running straight off the website

It can be done. The browser downloads the speech and translation models once (about 0.5 to
1.5 GB, kept in the browser after that) and runs them on the computer's graphics chip
(transformers.js with WebGPU). Chrome also has a built-in on-device translator; its Thai would
need checking.

For this Sunday the downloaded app is the safer choice. A browser version means a big first
download on the church wifi, Safari only does WebGPU on newer macOS, and a page hitting memory
limits mid-sermon is harder to catch than an app. A good next project once the app has a few
Sundays behind it: the same operator page and church screen, running in the browser.
