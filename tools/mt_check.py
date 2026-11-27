"""Translates the test sermon's lines with NLLB, both as written and with the first letter flipped,
and prints the model's own confidence for each, to see whether the score can catch a bad line.
    python tools/mt_check.py th
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from riah_translate.langs import lang  # noqa: E402
from riah_translate.mt import NllbTranslator, tidy  # noqa: E402

tgt = sys.argv[1] if len(sys.argv) > 1 else "th"
mt = NllbTranslator(threads=4)
s, t = lang("en")["nllb"], lang(tgt)["nllb"]
text = open("tests/clips/sermon-en.txt", encoding="utf-8").read()
import re  # noqa: E402
lines = [x.strip() for x in re.split(r"(?<=[.?!])\s+", text) if x.strip()]
for line in lines:
    flip = line[0].lower() + line[1:]
    batch = [[s] + mt.tok.encode(v, add_special_tokens=False).tokens + ["</s>"] for v in (line, flip)]
    res = mt.translator.translate_batch(batch, target_prefix=[[t]] * 2, beam_size=4, max_decoding_length=256,
                                        repetition_penalty=1.1, no_repeat_ngram_size=4, return_scores=True)
    print(line)
    for tag, r in zip(("as is", "lower"), res):
        ids = [i for i in (mt.tok.token_to_id(p) for p in r.hypotheses[0] if p != t) if i is not None]
        print(f"  {tag}: {r.scores[0]:+.3f}  {tidy(mt.tok.decode(ids, skip_special_tokens=True), tgt)}")
