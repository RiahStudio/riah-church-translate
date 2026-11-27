"""Translation backends.

* NllbTranslator (default): Meta's NLLB-200 distilled 1.3B, in the int8 CTranslate2 build published
  by OpenNMT (the CTranslate2 authors). Offline, free, ~1.4 GB, one model for 200 languages.
  Licence: CC-BY-NC-4.0, so fine for a church service, not for selling.
* LlmTranslator (optional): any local Ollama model (e.g. qwen3:8b). Slower, but it reads the last
  few lines for context and follows the glossary and church vocabulary, so it usually reads better.
* EchoTranslator: shows the original in both columns. Used when nothing else is available, so a
  broken translator is visible on screen instead of a blank column.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request

from .bible_words import bible_fix, bible_words
from .langs import lang

NLLB_REPO = "OpenNMT/nllb-200-distilled-1.3B-ct2-int8"


_THAI = "฀-๿"


_CJK = "㐀-鿿豈-﫿"
_FULL = {",": "，", ".": "。", "!": "！", "?": "？", ":": "：", ";": "；"}


def tidy(text: str, tgt: str) -> str:
    """Script clean-ups for model output that would look wrong on screen."""
    text = re.sub(r"\s+", " ", text).strip()
    if tgt == "th":
        text = text.replace("ํา", "ำ")          # nikhahit + sara aa -> sara am (ำ)
        # Thai puts spaces between phrases, not words. The model sometimes splits every word
        # ("เปาโล เป็น อัครสาวก"); when the pieces are that short, join them up.
        runs = re.findall(f"[{_THAI}]+", text)
        if len(runs) >= 3 and sum(map(len, runs)) / len(runs) < 8:
            text = re.sub(f"(?<=[{_THAI}]) (?=[{_THAI}])", "", text)
    elif tgt == "lo":
        text = text.replace("ໍາ", "ຳ")
    elif tgt in ("zh", "zh-TW", "ja"):
        # Chinese punctuation is full width ("，" "。"); the model writes the English kind.
        text = re.sub(rf"(?<=[{_CJK}])\s*([,.!?:;])(?!\d)\s*", lambda m: _FULL[m.group(1)], text)
    return text


def exact_phrase(text: str, tgt: str, glossary) -> str | None:
    """A short church phrase said on its own ("Amen."), with the words churches use."""
    exact = (glossary or {}).get("exact")
    if not exact or len(text) > 60:
        return None
    from .church_words import phrase_key
    return (exact.get(phrase_key(text)) or {}).get(tgt)


def apply_fixes(text: str, fixes: list) -> str:
    """Glossary fixes: exact find -> replace on the translated text, e.g. a church word NLLB gets wrong."""
    for pair in fixes or []:
        if isinstance(pair, (list, tuple)) and len(pair) == 2 and pair[0]:
            text = text.replace(pair[0], pair[1])
    return text


_DRAFT_END = re.compile(r"[.。!！?？]+$")

# NLLB now and then translates only half of a two-part sentence ("So when we leave this building
# today, let us carry that love into Chiang Mai." came out as just "let us carry that love into
# Chiang Mai" in Chinese). On four Mac runs of the test sermon, whole lines were at least this long
# next to the English (characters), and half lines shorter: zh 0.20+ vs 0.12-0.17, th 0.64+ vs 0.30.
MIN_COVER = {"th": 0.5, "zh": 0.19, "zh-TW": 0.19}


def too_short(src_text: str, out: str, tgt: str) -> bool:
    return len(src_text) >= 30 and len(out) < MIN_COVER.get(tgt, 0) * len(src_text)


def halves(text: str):
    """Split at the comma (or ; :) nearest the middle, with at least 3 words each side; else None."""
    best = None
    for m in re.finditer(r"[,;:]\s+", text):
        left, right = text[: m.start() + 1], text[m.end():]
        if len(left.split()) >= 3 and len(right.split()) >= 3:
            off = abs(len(left) - len(right))
            if best is None or off < best[0]:
                best = (off, [left, right])
    return best[1] if best else None


def join_halves(a: str, b: str, tgt: str) -> str:
    if tgt.startswith("zh"):
        return re.sub(r"[。.，,]+$", "", a) + "，" + b
    return a.rstrip(" .") + " " + b


class EchoTranslator:
    name = "none"
    drafts = False

    def translate(self, text, src, tgt, context=None, glossary=None):
        return text

    def translate_multi(self, text, src, tgts, context=None, glossary=None, keep=None, draft=False):
        return {t: (self.translate(text, src, t, context=context, glossary=glossary), None) for t in tgts}


class NllbTranslator:
    """Also translates while the preacher is still mid-sentence ("drafts"). Each new draft is forced
    to start with the words already on screen (all but the last two pieces), so the projected text
    grows instead of jumping about. EN->TH keeps nearly the same word order, so this costs little."""
    name = "nllb"
    drafts = True
    KEEP_BACK = 2      # pieces at the growing end that a later draft may still change
    LOOSER = 0.12      # the final line keeps the draft's start unless that scores this much worse
    # Paths searched for a finished line (drafts search one). On 150 Bible verses (4 threads, a
    # Linux test machine) beam 4 read best (Thai chrF 44.9, beam 2 43.7, beam 1 43.6) for ~10% more time a line.
    FINAL_BEAM = 4
    SLOW_FINAL = 1.5   # seconds; finished lines slower than this, three in a row, halve the beam

    def __init__(self, model_dir: str | None = None, threads: int = 2, device: str = "cpu", on_status=None):
        import ctranslate2
        import tokenizers

        self.final_beam, self._slow = self.FINAL_BEAM, 0
        if model_dir is None:
            from .models import ensure_model
            model_dir = ensure_model(NLLB_REPO, "translation model", on_status)
        self.translator = ctranslate2.Translator(model_dir, device=device, compute_type="int8",
                                                 intra_threads=threads, inter_threads=1)
        self.tok = tokenizers.Tokenizer.from_file(os.path.join(model_dir, "tokenizer.json"))

    # NLLB now and then gives a fluent but wrong line ("Notice that the verse begins with love." came
    # out as "What is love?"). Its own score drops when that happens: on the test sermon the two bad
    # lines scored -0.84 and -0.90, good lines -0.33 to -0.72. Below DOUBT we translate again with
    # the first letter's case flipped, which changes the model's path, and keep the better score.
    DOUBT = -0.75

    def translate(self, text, src, tgt, context=None, glossary=None):
        return self.translate_multi(text, src, [tgt], glossary=glossary)[tgt][0]

    def _src_pieces(self, text, src):
        return [lang(src)["nllb"]] + self.tok.encode(text, add_special_tokens=False).tokens + ["</s>"]

    def _run(self, jobs, beam):
        """jobs: (source pieces, target code, forced start). One batch for every language at once."""
        res = self.translator.translate_batch(
            [j[0] for j in jobs], target_prefix=[[j[1]] + list(j[2] or []) for j in jobs],
            beam_size=beam, max_decoding_length=256, repetition_penalty=1.1, no_repeat_ngram_size=4,
            return_scores=True)
        return [(r.scores[0], [p for p in r.hypotheses[0] if p != j[1]]) for r, j in zip(res, jobs)]

    def _pace(self, secs):
        """An old or busy computer: when finished lines keep coming late, search fewer paths, so the
        screen keeps up with the preacher. (4 -> 2 -> 1; a fast Mac never gets here.)"""
        self._slow = self._slow + 1 if secs > self.SLOW_FINAL else 0
        if self._slow >= 3 and self.final_beam > 1:
            self.final_beam, self._slow = self.final_beam // 2, 0

    # A kept start can end the line early: on a Mac run "Nicodemus had questions, and Jesus did not
    # send him away." kept the draft of "Nicodemus had questions," and the Thai stopped there.
    SHORTER = 0.75     # a kept line this much shorter than the fresh one has lost words

    def _keep_wins(self, free, kept, fair):
        """free, kept: the two finished lines (pieces); fair: their scores, None when they're the same."""
        if len(kept) < self.SHORTER * len(free):
            return False
        return fair is None or fair[1] >= fair[0] - self.LOOSER

    def _text(self, pieces, tgt, glossary, draft, src_text=None):
        ids = [i for i in (self.tok.token_to_id(p) for p in pieces) if i is not None]
        out = tidy(self.tok.decode(ids, skip_special_tokens=True), tgt)
        if draft:
            out = _DRAFT_END.sub("", out)       # the sentence isn't over yet
        if src_text and (glossary or {}).get("bible", True):
            out = bible_fix(src_text, out, tgt)  # Bible names as the Thai and Chinese Bibles spell them
        return apply_fixes(out, (glossary or {}).get("fix", {}).get(tgt))

    def translate_multi(self, text, src, tgts, context=None, glossary=None, keep=None, draft=False):
        """Returns {target: (text, pieces)}. `keep` maps a target to the pieces already on screen;
        a draft always continues them, a final line continues them unless that reads clearly worse."""
        fixed = {t: exact_phrase(text, t, glossary) for t in tgts}
        out = {t: (v, None) for t, v in fixed.items() if v}
        tgts = [t for t in tgts if t not in out]
        if not tgts:
            return out
        src_p = self._src_pieces(text, src)
        keep = {t: p[: max(0, len(p) - self.KEEP_BACK)] for t, p in (keep or {}).items() if p}
        jobs, slots = [], []
        for t in tgts:
            code = lang(t)["nllb"]
            if draft:
                slots.append((t, "draft")); jobs.append((src_p, code, keep.get(t)))
                continue
            slots.append((t, "free")); jobs.append((src_p, code, None))
            if keep.get(t):
                slots.append((t, "kept")); jobs.append((src_p, code, keep[t]))
        got = {}
        t0 = time.perf_counter()
        ran = self._run(jobs, 1 if draft else self.final_beam)
        if not draft:
            self._pace(time.perf_counter() - t0)
        for (t, kind), r in zip(slots, ran):
            got.setdefault(t, {})[kind] = r
        # A forced start isn't counted in CTranslate2's score, so score both whole lines the same way.
        pairs = [t for t in tgts if not draft and "kept" in got[t] and got[t]["kept"][1] != got[t]["free"][1]]
        fair = {}
        if pairs:
            targets = []
            for t in pairs:
                code = lang(t)["nllb"]
                targets += [[code] + got[t]["free"][1], [code] + got[t]["kept"][1]]
            res = self.translator.score_batch([src_p] * len(targets), targets)
            means = [sum(r.log_probs) / max(1, len(r.log_probs)) for r in res]
            for i, t in enumerate(pairs):
                fair[t] = (means[2 * i], means[2 * i + 1])
        for t in tgts:
            g = got[t]
            if draft:
                score, pieces = g["draft"]
            else:
                score, pieces = g["free"]
                if "kept" in g and self._keep_wins(g["free"][1], g["kept"][1], fair.get(t)):
                    pieces = g["kept"][1]
                elif score < self.DOUBT and text[:1].isalpha():
                    alt = self._run([(self._src_pieces(text[0].swapcase() + text[1:], src), lang(t)["nllb"], None)],
                                    self.final_beam)[0]
                    if alt[0] > score:
                        score, pieces = alt
            out[t] = (self._text(pieces, t, glossary, draft, text), pieces)
            if not draft and too_short(text, out[t][0], t):
                out[t] = self._both_halves(text, src, t, glossary) or out[t]
        return out

    def _both_halves(self, text, src, tgt, glossary):
        """A finished line that came out too short: translate each half and join them."""
        parts = halves(text)
        if not parts:
            return None
        ran = self._run([(self._src_pieces(p, src), lang(tgt)["nllb"], None) for p in parts], self.final_beam)
        a, b = (self._text(r[1], tgt, glossary, False, p) for r, p in zip(ran, parts))
        return join_halves(a, b, tgt), ran[0][1] + ran[1][1]


class LlmTranslator:
    """Ollama's local chat API. Nothing leaves the computer unless the URL points somewhere else."""
    name = "llm"
    drafts = False   # too slow to translate mid-sentence

    def translate_multi(self, text, src, tgts, context=None, glossary=None, keep=None, draft=False):
        return {t: (exact_phrase(text, t, glossary) or self.translate(text, src, t, context=context, glossary=glossary),
                    None) for t in tgts}

    def __init__(self, model: str = "qwen3:8b", url: str = "http://127.0.0.1:11434", timeout: float = 20):
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("The LLM URL must use http:// or https://.")
        self.model, self.url, self.timeout = model, url.rstrip("/"), timeout

    def translate(self, text, src, tgt, context=None, glossary=None):
        g = glossary or {}
        S, T = lang(src)["name"], lang(tgt)["name"]
        rules = [
            f"You translate a live church sermon from {S} into {T}. The result is shown as subtitles on the church screen.",
            "Reply with the translation of the NEW line only. No quotes, notes, or explanations.",
            "Use the words churches use (Bible names and terms as in the standard Protestant Bible for that language).",
            "Keep it natural and short enough to read quickly. Keep names as they are.",
        ]
        if g.get("hints"):
            rules.append(f"Names and words that may come up: {g['hints']}")
        bw = bible_words() if g.get("bible", True) else None
        said = bw.hints(text, tgt) if bw else []
        if said:
            rules.append("Spell these the way the Bible does: " + ", ".join(f"{a} = {b}" for a, b in said))
        for a, b in g.get("fix", {}).get(tgt, []) or []:
            rules.append(f"Write '{b}', never '{a}'.")
        user = ""
        if context:
            user += "Earlier lines (context only, do not translate):\n" + "\n".join(context[-3:]) + "\n\n"
        user += "NEW line:\n" + text
        body = dict(model=self.model, stream=False, think=False, keep_alive="30m",
                    options=dict(temperature=0.2),
                    messages=[dict(role="system", content="\n".join(rules)), dict(role="user", content=user)])
        req = urllib.request.Request(self.url + "/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            out = json.loads(r.read().decode())["message"]["content"]
        out = re.sub(r"<think>.*?</think>", "", out, flags=re.S).strip().strip('"“”')
        out = tidy(out, tgt)
        if bw:
            out = bw.fix(text, out, tgt)
        return apply_fixes(out, g.get("fix", {}).get(tgt))


def make_translator(kind: str, on_status=None, llm_model=None, llm_url=None, threads=2):
    if kind == "none":
        return EchoTranslator()
    if kind == "llm":
        return LlmTranslator(model=llm_model or "qwen3:8b", url=llm_url or "http://127.0.0.1:11434")
    return NllbTranslator(threads=threads, on_status=on_status)
