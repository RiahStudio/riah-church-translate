# Live translation and captioning products: what we can learn

Date: 2026-11-22. Web pages only, nothing downloaded or signed up for.
Scope: what a small church needs to project Thai subtitles from an English sermon (Filipino-accented preacher), offline on a laptop.

**How much to trust this.** Almost every number below is the vendor's own claim. I found no independent latency tests for the church tools. Gaps I could not fill (pages blocked, thin, or not found): KUDO, Interactio, Breeze, Verbit, Ai-Media LEXI (403 on its help page), Google Meet Thai support, real r/churchtech and ProPresenter-forum threads (search surfaced none), spf.io's own settings docs, Tencent big-screen details. Where something is my own knowledge and not from a page, I say so.

---

## Part 1. Features ranked by value for us

Ranking is for: small church, one Thai projector screen, English preacher with an accent, laptop only, operator is a volunteer.

### 1. Stable text that does not flicker or rewrite (highest value)
- Slator's study of Google Meet, Teams, Zoom and DeepL Voice measured "caption churn", the constant rewriting of text already on screen, and says it raises reading effort. DeepL scored best on stability (88.6/100) and claims 37.6% less churn than Teams and 54.7% less than Zoom. This is DeepL's own write-up of the study, so it is promotional. https://www.deepl.com/sv/blog/deepl-voice-slator-caption-churn
- OmniWord (a church tool) paces translation to natural pauses and punctuation, not word by word, and carries unfinished phrases into the next chunk. https://alternativeto.net/software/omniword/about/
- Microsoft says both recognition and translation use the whole sentence, and suggests sentences of roughly 5 to 15 words for best results. https://www.microsoft.com/en-us/translator/powerpoint.aspx (search summary of Microsoft's guidance)
- Our app already translates only finished lines. That is the right call for Thai on a projector. Keep it. Do not add word-by-word re-translation.

### 2. Show the English as it comes in, translation when the line finishes
- Maestra gives "interim" (still changing) text its own colour so it looks different from settled text. https://help.maestra.ai/live/caption-appearance
- Interprefy offers two modes: a default that shows text within 4 seconds of the speaker finishing a sentence, and an "instant" mode with auto-correction. https://knowledge.interprefy.com/how-do-interprefy-captions-work
- Idea for us: show the in-progress English in a dim colour, then swap it for the final Thai when ready. The room sees something moving right away, which feels more "simultaneous" even though Thai lands a sentence later.

### 3. Operator can correct a line before it goes to the screen (a "moderation" step)
- OneAccord describes an optional window where the host reviews each transcript line before it is translated and sent. https://www.oneaccord.com/blog/ai-church-translation-accuracy
- Wordly and LiveVoice instead lean on glossaries; I found no live-edit feature described for them.
- For a volunteer this is risky if it adds delay. Make it optional: a hold-and-fix toggle for guest speakers or Bible reading, off by default.

### 4. Custom glossary / vocabulary
- Wordly and LiveVoice both say to preload names, scripture references, theological terms and proper nouns. https://www.wordly.ai/blog/church-translation-guide , https://livevoice.io/de/blog/ai-translation-for-churches
- OmniWord has a custom dictionary on paid plans to lock church names and theology terms. https://alternativeto.net/software/omniword/about/
- OneAccord lists Melchizedek, Habakkuk, Jehoshaphat as hard names and shows "every head bowed and eyes closed" heard as "every hedge bowed and nice clothes." https://www.oneaccord.com/blog/ai-church-translation-accuracy
- Value for us: a Thai term list (fixed Thai wording for grace, covenant, etc.) applied after NLLB, plus an English hotword list for the preacher's names. Interprefy adds that glossary ownership (who updates it) is a known gap, so keep it in one editable file. https://www.interprefy.com/resources/blog/what-to-look-for-in-a-good-ai-speech-translator

### 5. Operator hide / pause / blank button
- ProPresenter-style caption props can be toggled on and off with one click, and a remote-control URL (and Stream Deck) can start, stop, show and hide captions from elsewhere. https://conferencecaptioning.tawk.help/article/how-to-add-live-captions-translations-to-propresenter
- PowerPoint has a one-key toggle (J). https://support.microsoft.com/powerpoint/present-with-real-time-automatic-captions-or-subtitles-in-powerpoint
- Needed for us: one big "blank" key for prayer, worship songs, or when text goes wrong.

### 6. Choose which languages show, and style each
- PowerPoint separates "spoken language" from "subtitle language". Hosts choose what the room sees. Same support page as above.
- Maestra lets you set text colour, interim colour, background colour and opacity, alignment (full/left/middle/right), vertical position (top/middle/bottom/lower third) and side padding, with a live preview and reset. https://help.maestra.ai/live/caption-appearance
- Our operator picker (English / Thai / Chinese) plus per-language size and colour matches this.

### 7. Overlay as a browser source (ProPresenter, OBS, vMix)
- Conference Captioning and OmniWord both give a URL you paste into a ProPresenter "Web" prop or an OBS browser source; ProPresenter can show it on the livestream only, not the room screens. https://conferencecaptioning.tawk.help/article/how-to-add-live-captions-translations-to-propresenter , https://alternativeto.net/software/omniword/about/
- Our page is already a browser page. Add a transparent-background lower-third mode and a "text size via window width" behaviour (Conference Captioning's tip: shrink the web box to make text bigger).
- Lower value for this Sunday if the projector is driven directly from the laptop. Fullscreen second-display mode matters more.

### 8. Phones via QR code
- Wordly, LiveVoice, OmniWord, OneAccord all let people scan a QR code and read or hear their language on their own phone. https://www.wordly.ai/blog/church-translation-guide , https://livevoice.io/de/blog/ai-translation-for-churches
- Needs internet for the congregation unless we serve it over the church Wi-Fi from the laptop. Our app is offline-first, so a local-network phone page is possible but is a separate job. Nice to have, not for Sunday.

### 9. Saved transcript and export after the service
- OmniWord exports TXT, CSV, PDF, and SRT on its Pro plan. https://alternativeto.net/software/omniword/about/
- Cheap for us: save every line with timestamp. Also lets the team review mistakes and grow the glossary.

### 10. Speaker labels and colours per speaker
- Maestra has a colour per detected speaker. https://help.maestra.ai/live/caption-appearance
- Low value here: one preacher. Skip.

### Not worth copying
- Translated speech / synthesized voice, voice cloning (Tencent Meeting does voice mimicry, https://news.aibase.com/zh/news/28220). Not asked for.
- Cloud-only features. They would break the "no internet" promise.

---

## Part 2. Pitfalls and what others do about them

### A. Dirty audio is the number one cause of bad output
- LiveVoice: "connect to the sound desk rather than a phone in the back row"; background noise, fast pace and dialect all lower accuracy. https://livevoice.io/de/blog/ai-translation-for-churches
- OneAccord: laptop or phone mics pick up PA hum, AC noise, room echo, amens and soft music; use a clean balanced feed from the sound desk, mic close to mouth, watch for distortion when the speaker gets loud. https://www.oneaccord.com/blog/ai-church-translation-accuracy
- Microsoft: headset mic on the presenting device, reduce background noise. https://support.microsoft.com/powerpoint/present-with-real-time-automatic-captions-or-subtitles-in-powerpoint
- Mitigation for us: take a direct line from the mixer (a clean preacher-only mix, not the full house mix with band and congregation). Do a calibration run with the real preacher before service. Show an input level meter to the operator.

### B. Accent and fast speech
- Vendors say a measured pace and clear enunciation help, and heavy accent or speed hurt. https://blog.palabra.ai/al-speech-translation/real-time-church-service-translation-for-worship/ , https://www.oneaccord.com/blog/ai-church-translation-accuracy
- OneAccord's fix is church-trained models, a sound technician, weekly audio checks and QA fed back into the model. We cannot do that live.
- Independent advice: expect 1.5x to 2x the published word error rate on real domain audio before tuning. https://www.forasoft.com/blog/article/real-time-speech-translation-vendor-benchmarks
- Mitigation for us: use a larger faster-whisper model if the laptop copes, run one rehearsal with this preacher, load his name and common terms as an initial prompt/hotwords, and keep the optional operator-fix toggle.

### C. Silence, music and "ghost" text (Whisper-specific)
- Whisper is known to invent phrases on silence or non-speech, most commonly "thank you" and "thanks for watching". A 2025 paper quantified this (24.76% "thank you"); I only saw that via a secondary write-up. https://yage.ai/share/whisper-repetition-hallucination-en-20260526.html
- In faster-whisper, `vad_filter` is off by default; turning it on is the standard fix, though it can drop quiet or heavily accented speech. Guides also suggest shortening minimum silence to about 500 ms for gaps. https://mixpeek.com/guides/why-does-whisper-make-up-words-during-silence
- A forum report notes "Thank you for watching!" still appearing with VAD on. https://huggingface.co/spaces/aadnk/whisper-webui/discussions/19
- Church vendors say little about worship music (LiveVoice and Wordly pages do not address it; Palabra only notes bilingual members may switch languages for songs). https://www.wordly.ai/blog/church-translation-guide , https://blog.palabra.ai/al-speech-translation/real-time-church-service-translation-for-worship/
- Mitigation: VAD on; a drop-list of known ghost phrases; the blank key during songs; feed only the preacher's mic so the band does not reach Whisper. Do not rely on a vendor doing this for us; none documented it.

### D. Names, scripture and theology words
- Generic engines handle theology badly; glossaries fix how terms are rendered. https://www.oneaccord.com/blog/ai-church-translation-accuracy
- No vendor documented a Bible-verse feature (e.g. auto-replace a quoted verse with the known Thai Bible text). LiveVoice and Wordly just say add scripture references to the glossary. https://livevoice.io/de/blog/ai-translation-for-churches
- Opportunity beyond the vendors (my idea, not found in any source): when the English matches a known verse, show the standard Thai Bible wording instead of machine translation. Larger job; not for Sunday.
- Mitigation now: glossary of Bible book names and key terms in Thai; hold-and-fix toggle during readings. OneAccord says liturgical reading calls for more care.

### E. Delay and "simultaneous" are not what ads suggest
Published figures, all vendor-sourced unless noted:
- LiveVoice: translations usually in 2 to 4 seconds, longer for long sentences. https://livevoice.io/de/blog/ai-translation-for-churches
- Palabra: about 1 to 3 seconds. https://blog.palabra.ai/al-speech-translation/real-time-church-service-translation-for-worship/
- Interprefy captions: up to 4 seconds after sentence end in default mode. https://knowledge.interprefy.com/how-do-interprefy-captions-work
- Tencent Meeting AI interpreting: official "within 3 seconds" (Chinese-English). https://news.aibase.com/zh/news/28220
- iFlytek: fastest first-word response about 2 seconds (their launch claim). https://www.ithome.com/0/891/423.htm
- A SyncWords customer says captions used to lag 4 to 8 seconds before an upgrade. https://www.deepl.com/en/customer-stories/syncwords
- Human captioners: about 2 seconds (3Play) vs broadcast 4 to 6 seconds. https://www.slideshare.net/3Play/the-3play-way-realtime-captioning-in-higher-education
- Independent latency guidance: under 800 ms feels live; past about 2 seconds people start to talk over the feed; the first translated word matters more than the average. https://www.forasoft.com/blog/article/real-time-speech-translation-vendor-benchmarks
- So a laptop doing sentence-by-sentence translation will sit at the same few seconds as the cloud products. That is normal. Honest wording for the church: "subtitles follow a few seconds behind."
- Mitigation: show English immediately (see feature 2), keep chunks short, tell the operator what the lag is.

### F. Machine translation errors on a big screen
- No vendor claims perfect output; Wordly says expect roughly 85 to 95% and recommends a human interpreter for high-stakes moments and Q&A. https://livevoice.io/de/blog/ai-translation-for-churches (also notes AI has limited theological judgment)
- Interprefy: "ideal conditions are not typical"; test with your own content. https://www.interprefy.com/resources/blog/what-to-look-for-in-a-good-ai-speech-translator
- Mitigation: rehearse on a past sermon; have a Thai speaker on the team watch for the first few Sundays and report wrong lines to feed the glossary. Plan a first trial with low stakes (Wordly/LiveVoice advise a midweek trial first).

### G. Readability on the projector (Thai especially)
Hard numbers are scarce. What I found:
- Subtitle standard for Thai (Netflix): 35 characters per line not counting tone marks and top/bottom vowels, maximum 2 lines, one line preferred, up to 17 characters per second for adult content, avoid two short lines, pyramid shape with the longer line at the bottom, white sans-serif, no italics. https://partnerhelp.netflixstudios.com/hc/en-us/articles/220448308 This is for TV; a sermon screen can allow more text, but it shows the reading-speed limit is real.
- Text-height rules of thumb disagree by about five times: 0.007 inch per foot of distance (signage guide), roughly 1/4 inch per 7 feet (broadcast), and 4 mm per metre (projection guide). https://digitalsignage.com/digital_signage/docs/guides/typography-viewing-distance , https://www.proav.de/video/projectionrules.html . For captions, one source says size within a sensible range affects reading speed little if contrast is good. https://md-subs.com/saa-subtitle-font-size
- Practical rule: take the strictest reasonable rule (4 mm per metre of the farthest seat), then walk to the back row and check. Example: back row at 20 m means about 8 cm letter height on the screen.
- Thai marks can clip or collide: tone marks and vowels stack above and below letters; fallback fonts with different metrics change the line box; mark positioning depends on the font's own GPOS data. https://learn.microsoft.com/en-us/typography/script-development/thai , https://bugs.webkit.org/show_bug.cgi?id=25818 , https://devforum.roblox.com/t/bug-with-thai-translation-fonts-via-translator-portal/2397589
- My own knowledge (not from these pages): Thai has no spaces between words, so browsers need a dictionary to wrap lines well. Use a modern browser, set the page language to `th`, and avoid forcing breaks mid-word. W3C's Thai layout note is the reference: https://www.w3.org/TR/thai-lreq/
- Mitigation: pick one good Thai web font bundled with the app (so it works offline and does not fall back), use generous line height, bold weight, white or yellow on solid dark with high opacity, side padding, and keep text out of the screen edges (Maestra exposes "side padding" for exactly this). https://help.maestra.ai/live/caption-appearance

### H. Showing more than one language at once
- PowerPoint and others show one subtitle language per output. Side-by-side English and Thai on one screen is something church tools mostly solve by giving each language its own overlay URL or its own phone view. https://conferencecaptioning.tawk.help/article/how-to-add-live-captions-translations-to-propresenter (checkboxes choose which captions and translations appear)
- Mitigation: our operator toggle for English/Thai/Chinese is the right shape. For far-away reading, default to Thai large with English smaller, rather than equal halves.

### I. Internet and cloud dependence
- PowerPoint, Zoom, Meet, Wordly, LiveVoice, Interprefy all need a fast reliable connection; Microsoft recommends a wired one. https://support.microsoft.com/powerpoint/present-with-real-time-automatic-captions-or-subtitles-in-powerpoint , https://livevoice.io/de/blog/ai-translation-for-churches
- Speech is sent to Microsoft's cloud. Same page.
- Plan gating: Zoom translated captions are an add-on for paid plans or in Enterprise; Meet translated captions are limited to some Workspace editions and the language list has changed; check Thai first. https://www.zoom.com/en/blog/ai-accessible-meetings-captions/ , https://support.google.com/meet/answer/10964115
- PowerPoint's fully supported spoken languages do not include Filipino-accented English as a separate option (English US/UK/CA are listed); accuracy for accent is not documented. Subtitle languages are over 60. Support page above.
- Our offline design avoids all of this. That is a real strength; keep it and say so.

### J. Long services, sleep, crashes
- I found no vendor page describing laptop sleep or long-service failures. Conference Captioning notes its app can restart when streaming with no audio source available. https://conferencecaptioning.tawk.help/article/how-to-add-live-captions-translations-to-propresenter
- An OBS forum report notes a browser source tied to NDI can look stuck when the source is inactive. https://obsproject.com/forum/threads/pro-presenter-7-ndi-plugin.186389/
- Mitigation (general practice, not sourced): disable sleep and screen saver on the laptop during service, plug in power, keep auto-save of the transcript, auto-restart the listener if audio stops, and show a visible "listening / not listening" light to the operator. (Note: the studio's own rule against keeping a PC awake applies to the studio PC; for Sunday's church laptop this is the church's decision.)

### K. Adoption and people
- OneAccord: expect 5 to 15% of potential users on the first service, stabilising by week 4 to 6; put the QR code in the bulletin and have welcome volunteers help. https://blog.palabra.ai/al-speech-translation/real-time-church-service-translation-for-worship/ (same figures reported there)
- Test in the real sanctuary with the real speaker; vendor demos differ from real acoustics. https://www.oneaccord.com/blog/ai-church-translation-accuracy

---

## Part 3. Vendor by vendor (short)

| Product | What I could confirm | Source |
|---|---|---|
| LiveVoice | Phone-based, QR code, audio and captions; 2 to 4 s delay claim; glossary; subtitle screens exist (page PDF unreadable); free plan 3 listeners, 20 AI minutes | https://livevoice.io/de/blog/ai-translation-for-churches |
| Wordly | Church guide; glossary; QR for phones; captions on room monitors; no latency numbers; about 200 US churches | https://www.wordly.ai/blog/church-translation-guide , https://religionnews.com/2025/03/18/live-ai-translation-comes-to-church/ |
| spf.io | Customisable caption size, lower-third or side-by-side; phone translations; church quote by discovery call; no public settings found | https://d8eqd7594n3bq.cloudfront.net/products/captioning-translation-integrations/ , https://learnofchrist.com/resources/spf-io |
| Interprefy | Captions default (4 s after sentence) and instant mode; viewer text size and colour; custom vocabulary; hard to find operator controls | https://knowledge.interprefy.com/how-do-interprefy-captions-work |
| KUDO, Interactio | Only glossary mention (KUDO). Nothing else confirmed | https://www.interprefy.com/resources/blog/what-to-look-for-in-a-good-ai-speech-translator |
| Church apps (Glossa, Hope Translator, ChurchTranslator.AI, OmniWord, OneAccord, Kaleo) | Mostly phone or browser tools; Hope Translator reported working with ProPresenter; OmniWord has overlay URL and dictionary | https://faith.tools/app/1550-hope-translator-live-sermon-translation , https://alternativeto.net/software/omniword/about/ |
| Microsoft PowerPoint live subtitles | Spoken vs subtitle language; 60+ subtitle languages; position above/below/overlaid; font, size, colour, background (Windows/Mac); J toggles; cloud-based | https://support.microsoft.com/powerpoint/present-with-real-time-automatic-captions-or-subtitles-in-powerpoint |
| Zoom | Host enables; viewers pick language and set their own font size; plan add-on | https://www.zoom.com/en/blog/ai-accessible-meetings-captions/ |
| Google Meet | Plan-gated; Thai availability unclear to me; scored lowest (87/100) in the Slator test | https://support.google.com/meet/answer/10964115 , https://www.deepl.com/sv/blog/deepl-voice-slator-caption-churn |
| iFlytek 讯飞同传 | Floating subtitles, caption screen-cast, live-stream subtitle overlay; 2 s first-word claim; 100,000+ term lists; used on big screens at a broadcast conference | https://ai-bot.cn/sites/72334.html , https://www.ithome.com/0/891/423.htm , https://www.pingwest.com/a/317463 |
| Tencent 同传 | Chinese-English only at launch, within 3 s, bilingual bottom subtitles; nothing on big-screen or term lists found | https://news.aibase.com/zh/news/28220 |
| SyncWords | AI live captions in 60+ languages; "instant" display claim; customer says 4 to 8 s lag before upgrade | https://syncwords.com/products/automated-captions , https://www.deepl.com/en/customer-stories/syncwords |
| Ai-Media LEXI, Verbit | Not confirmed (page blocked or no results) | none |
| ProPresenter | No built-in translation; use a Web prop (browser source) as a Props-layer overlay that stays up across slides; can be stream-only | https://conferencecaptioning.tawk.help/article/how-to-add-live-captions-translations-to-propresenter |
| OBS caption plugins | StreamTranslate (browser source, translated subtitles) and Pixelion Captions exist; no detail confirmed | https://obsproject.com/forum/resources/streamtranslate-%E2%80%93-real-time-live-captions-translation-via-browser-source.2433/reviews |

---

## Part 4. Suggested changes before Sunday (small, from the above)
1. One big BLANK / hide key, and a pause key. (Pitfalls C, feature 5)
2. VAD on, ghost-phrase drop-list ("thank you", "thanks for watching"). (C)
3. Clean preacher-only mixer line, level meter on the operator page. (A)
4. Thai font bundled with the app, generous line height, high contrast, safe side margins; test from the back row. (G)
5. Thai-first layout with English smaller; per-language toggle already planned. (H)
6. Short glossary file (names, Bible books, key theology terms) applied before/after translation. (D)
7. Show English straight away in a dim colour; swap to final Thai when done. (feature 2, E)
8. Save a timestamped transcript of every line. (feature 9)
9. Rehearse once with the actual preacher and the real room. (B, F, K)
Later: optional operator line-fix, local phone page, known Bible verse lookup.
