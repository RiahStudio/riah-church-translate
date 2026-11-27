# Live speech translation and subtitles: what to borrow or switch to

Date: 2026-11-22. Method: read READMEs, model cards, paper pages only. Nothing cloned, downloaded or installed.

Honest limits up front:
- GitHub pages fetched do not show "last commit" dates. I give star counts and commit totals; treat "last activity" as unverified unless noted. Check the Activity tab before relying on any project.
- No public benchmark covers Filipino-accented English. Nothing below is a Filipino-accent result.
- I found no published Thai score that puts Typhoon Translate, TranslateGemma or Qwen head to head with NLLB-1.3B. Numbers below are the nearest evidence, labelled as such.

## Our stack today (for reference)
faster-whisper base.en/small.en CPU int8, LocalAgreement-2 (from whisper_streaming), NLLB-200 distilled 1.3B CT2 int8 per finished line, web page on a projector, PyInstaller Mac app. English line about 2.2 s, Thai about 3.4 s (worst 6 s).

## Project table

| Project | URL | Stars / activity | License | Apple silicon | Good at | Borrow |
|---|---|---|---|---|---|---|
| WhisperLiveKit | https://github.com/QuentinFuxa/WhisperLiveKit | 11.3k stars, 988 commits | Apache-2.0 | Yes: mlx-whisper, voxtral-mlx backends, auto-picks MLX on macOS | Complete streaming server: SimulStreaming (AlignAtt) default, LocalAgreement option, Voxtral, built-in NLLB translation, web UI | Whole architecture is the closest match to ours. Read its translation-buffer code and its backend switch |
| NLLW (NoLanguageLeftWaiting) | https://github.com/QuentinFuxa/NoLanguageLeftWaiting | 432 stars | not stated on page | not mentioned | Streaming NLLB (600M/1.3B, CT2 or transformers) with LocalAgreement-style locked prefix; testing 600M draft + 1.3B verify | Translate the growing sentence and lock the stable prefix instead of waiting for a finished line |
| ufal/SimulStreaming | https://github.com/ufal/SimulStreaming | 697 stars, 113 commits | MIT | Not documented; torch only, README says CPU too slow, wants 10 GB GPU | Won IWSLT 2025 simultaneous task; AlignAtt policy; claims about 5x faster than whisper_streaming | Idea: stop decoding when attention reaches the buffer end. Not a direct drop-in on CPU |
| ufal/whisper_streaming | https://github.com/ufal/whisper_streaming | 3.7k stars | MIT | via faster-whisper or mlx-whisper backends | LocalAgreement (what we use) | README says it is outdated, replaced by SimulStreaming |
| collabora/WhisperLive | https://github.com/collabora/WhisperLive | 4.4k stars, 719 commits | MIT | Not stated; backends are faster-whisper, TensorRT, OpenVINO | Server/client, server-side any-to-any translation hook | Little new for Mac |
| obs-localvocal | https://github.com/obs-ai/obs-localvocal | 1.7k stars, 308 commits | GPL-2.0 (copyleft; flag for a closed app) | Yes: arm64, Metal, Core ML encoder | Whisper.cpp with Core ML, partial captions, CT2 translation | Core ML encoder trick; partial-caption handling |
| LiveCaptions-Translator | https://github.com/SakiRinn/LiveCaptions-Translator | 4.1k stars | Apache-2.0 | No, Windows only | Sends incomplete sentences to an LLM; overlay with adjustable number of sentences | Idea only: LLM handles unfinished sentences well |
| Speech-Translate | https://github.com/Dadangdut33/Speech-Translate | 662 stars, 263 commits | MIT | Python install on macOS | Desktop Whisper + translation | Little new |
| RealtimeSTT | https://github.com/KoljaB/RealtimeSTT | 10.3k stars | Apache-2.0 (engines vary) | Weak Mac guidance | Pause-triggered preview decode, tail-anchored decode, fast model for live text plus bigger model for final | Two-model pattern: draft text now, corrected final later |
| Buzz | https://github.com/chidiwilliams/buzz | 23.0k stars | MIT | Apple silicon only now | Polished Mac app, live transcription | UI ideas; not translation-focused |
| SeamlessStreaming | https://github.com/facebookresearch/seamless_communication | 11.9k stars | Models CC-BY-NC 4.0 (non-commercial) | fairseq2 supports Apple silicon | Direct streaming speech-to-text translation, 2.5B | Avoid: non-commercial, heavy, Thai not confirmed |
| sherpa-onnx | https://github.com/k2-fsa/sherpa-onnx | 16.1k stars, 2,245 commits | Apache-2.0 | macOS arm64 yes | Many ONNX models incl. Parakeet TDT v2 int8, Moonshine, streaming zipformer; runs in a packaged app | Easiest way to ship Parakeet or Moonshine inside PyInstaller-free native binary |
| Moonshine | https://github.com/moonshine-ai/moonshine | 11.6k stars, 684 commits | MIT; legacy non-English non-streaming models are non-commercial (flag) | Supports macOS | Streaming encoder; English Open ASR average 6.65 (Medium, 245M), 7.84 (Small, 123M) | Candidate English recognizer (see Q1) |
| Kyutai delayed-streams-modeling | https://github.com/kyutai-labs/delayed-streams-modeling | 3.1k stars | Code MIT/Apache, weights CC-BY 4.0 | Yes via moshi-mlx | 1B en/fr (0.5 s delay), 2.6B en (2.5 s delay), word timestamps | Fast 1B model is a real-time option |
| parakeet-mlx | https://github.com/senstella/parakeet-mlx | 999 stars | Apache-2.0 | Native MLX | Parakeet TDT 0.6B v3, word timestamps via CLI, a streaming method exists | Best accuracy per second on Mac (see Q1) |
| mlx-whisper (mlx-examples) | https://github.com/ml-explore/mlx-examples/tree/main/whisper | n/a | not stated on page | Native | word_timestamps=True | Fast Whisper on GPU |
| Voxtral Mini 4B Realtime | https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602 | model card | Apache-2.0 | Community MLX port | Native streaming, 240 ms to 2.4 s delay; Open ASR mean 7.68 | Heavy (4B, wants 16 GB); no word timestamps mentioned |

Church-specific search results: nothing mature and open. Closest: MinbarLive (https://github.com/mosait/MinbarLive, Islamic sermons, sentence-buffered translation), AnchorCast (open-source Electron church presenter with Whisper STT, listing says no translation), faster-whisper-livestream-translator (https://github.com/JonathanFly/faster-whisper-livestream-translator, MIT), live-translation by AbdullahHendy. Commercial only: Wordly, SyncWords, LiveVoice, OmniWord (ProPresenter overlay URL), CaptionKit, spf.io. Nothing to switch to; one useful idea is the ProPresenter overlay URL (a plain web page fed by WebSocket), which our page already is.

## Q1. Best English recognizer for accented English, real time, Apple silicon, word timestamps

Evidence (Open ASR Leaderboard averages, lower WER is better):
- Parakeet TDT 0.6B v3: average 6.34, AMI 11.31, RTFx 3333. https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3 (CC-BY-4.0, word and segment timestamps)
- Moonshine v2 Medium 6.65, Small 7.84. https://arxiv.org/html/2602.12241v1
- Kyutai stt-2.6b-en 6.4, AMI 12.17, RTFx 88, 2.5 s delay. https://huggingface.co/kyutai/stt-2.6b-en
- Voxtral Realtime 7.68. Whisper large-v3 about 7.44, AMI 15.95. large-v3-turbo 7.83, AMI 16.13. distil-large-v3.5 about 7.1 short-form. Canary-Qwen-2.5B 5.63 (top, but LLM decoder, slower). https://arxiv.org/html/2510.06961v3, https://huggingface.co/blog/open-asr-leaderboard
- Our current models (base.en/small.en) are far below these; the leaderboard does not list them in the excerpts I read, but Whisper small is well behind large-v3.

Accent evidence is thin. AMI (meeting audio with many non-native speakers) is the nearest proxy, and Parakeet v3 is clearly best there (11.3 vs 15.9 to 16.3 for Whisper large-v3, Voxtral, Canary). On L2-ARCTIC read speech, Whisper was among the best of five systems (MER 0.054), but Parakeet was not in the excerpt (https://arxiv.org/abs/2503.06924). On a noisy African-accent subset, Whisper large-v3 (33.8) beat Parakeet v2 (40.9) (https://arxiv.org/pdf/2511.14255). So accent results conflict by dataset. No Filipino data exists in public.

Answer: Parakeet TDT 0.6B v3 via parakeet-mlx (https://github.com/senstella/parakeet-mlx) is the best bet: lowest English WER among streaming-capable small models, word timestamps, native Apple-silicon speed, permissive license. Runner-up: Moonshine v2 Medium (lowest latency, 258 ms on an M3 per its paper) or whisper large-v3-turbo on mlx-whisper if accents prove Whisper's edge. Because public accent data is silent on Filipino English, the only decisive step is a 10-minute test: record or find Filipino-accented sermon audio and run Parakeet v3, large-v3-turbo and Moonshine Medium on it, score WER.
Caveat on streaming: parakeet-mlx's own TODO still lists streaming input although it has a transcribe_stream method; test it. Parakeet is a transducer, so LocalAgreement over word timestamps works the same as with Whisper. Caveat: v3 dips slightly on English vs v2, per a reply in the HF blog thread.

## Q2. Local EN to TH and EN to ZH translators that may beat NLLB-1.3B

No source I could read gives Thai scores for NLLB-1.3B against the candidates, so this is directional.

EN to TH candidates:
- Typhoon Translate 1.5 (SCB 10X), 4B, Qwen3-based, Thai/English both directions, about 2.5 GB at Q4_K_M, Apache-2.0 in metadata but the card text mentions the Gemma license: verify before shipping. Its own evidence is a GPT-4o-mini judge vs earlier versions; no BLEU/COMET published. https://huggingface.co/scb10x/typhoon-translate1.5-4b
- TranslateGemma 4B (Google, Jan 2026): WMT24++ MetricX 5.32 / COMET 81.6 averaged over 55 languages; the 4B reportedly rivals Gemma 3 12B. Gemma license, gated. Thai scores not read. https://huggingface.co/google/translategemma-4b-it, https://arxiv.org/abs/2601.09012. Its en to zh (CN) MetricX 2.66 vs 3.27 for Gemma 3 4B (lower is better).
- Gemma 3 base: FLORES+ en to th spBLEU/COMET 4B 36.4/87.0, 12B 42.0/89.0; en to zh 4B 34.1/87.4, 12B 38.3/88.7 (https://arxiv.org/html/2602.11961v1). NLLB numbers were not in the part I could read.
- ChindaMT (4B, 2B, 0.8B Thai-English; AACL 2026) wins against same-size baselines in pairwise judging, license not stated: https://aclanthology.org/2026.aacl-long.54/
- MADLAD-400 3B (Apache-2.0): 419 languages, no Thai-vs-NLLB numbers on the card; no CT2 listed. https://huggingface.co/google/madlad400-3b-mt
- NLLB 3.3B: about 13 GB vs 5.5 GB for the 1.3B distilled (community card); modest gain expected, not shown.

EN to ZH: Opus-MT en-zh (Apache-2.0, small, BLEU 31.4 Tatoeba) is very fast but weaker; Qwen3 and Qwen2.5 are the known strong small Chinese LLMs; TranslateGemma 4B is the best-documented upgrade.

Cost reality: a 4B LLM is autoregressive and heavier than CT2 NLLB-1.3B. On Apple silicon with Q4 via llama.cpp or MLX, a short line is on the order of a second or two; I did not find a published number. Test speed and quality with the actual sermon lines before switching. Practical plan: keep NLLB as the fast first pass and A/B Typhoon Translate 1.5 4B for Thai and TranslateGemma 4B for Chinese on 50 real lines with a Thai/Chinese reader.

## Q3. Showing translated subtitles sooner without flicker
- Re-translation: translate the growing source every time it changes; always correct, but unstable. Arivazhagan et al. cut instability up to 100x with biased decoding. https://arxiv.org/pdf/1912.03393v2.pdf
- Mask-k: show everything except the last k tokens, which may still change. Fixed k is the simple version; dynamic masking (Yao and Haddow) sets k by how much the output changes under probe extensions, giving a better delay/flicker curve. https://arxiv.org/pdf/2006.00249
- Revision-controllable decoding: reuse the previous output as a forced prefix so it cannot be rewritten; reported to remove flicker with a small quality loss. https://arxiv.org/html/2310.04399v1
- Wait-k / AlignAtt policies: emit only when enough source has arrived; NLLW and SimulStreaming implement this.
- Display tricks (our own judgement, no source): translate at clause boundaries (commas, "and", "but") rather than full sentences; show the draft Thai in a lighter colour and the confirmed Thai in full colour; never delete confirmed words, only append; fade in rather than replace; hold a minimum 1.5 to 2 s per subtitle block; make the English line the "live" one and Thai the "settled" one. Sermons have a steady pace and many repeated phrases (Scripture), so a cache of verse text in Thai (e.g. the Thai Bible) can bypass MT for quoted verses.
- LLM translators cope better with unfinished sentences (LiveCaptions-Translator's claim), which supports using a small LLM with mask-k.

## Recommendation for next Sunday
Do not rebuild. Highest-value, lowest-risk moves: (1) swap recognizer to Parakeet v3 (parakeet-mlx) or Moonshine Medium and keep LocalAgreement; test on Filipino-accent audio first; (2) translate at clause boundaries with a locked prefix (NLLW idea), keep NLLB; (3) A/B Typhoon Translate 1.5 4B for Thai off-line before trusting it live. Check licenses: Typhoon (Apache vs Gemma text), TranslateGemma (Gemma terms), GPL-2.0 for obs-localvocal, CC-BY-NC for Seamless.
