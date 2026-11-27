"""How does the built-in translator handle church words? Prints Thai and Chinese for a list of
sermon sentences, so a person can check the Bible names and terms. Run it on a Linux test machine:

    ~/venvs/riah-translate/bin/python tests/mt_probe.py [--fixes]

With --fixes it uses the app's church word list (riah_translate/church_words.py), to check the fixes.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SENTENCES = """God loves you.
Jesus Christ is Lord.
The Holy Spirit lives in us.
Let us pray.
Open your Bible to John chapter 3, verse 16.
Nicodemus came to Jesus at night.
Welcome to our church this morning.
Our pastor will preach the gospel.
We are saved by grace through faith.
Salvation is a gift from God.
All have sinned and fall short of the glory of God.
Amen.
Christ died on the cross for our sins.
Let's worship the Lord together.
The disciples followed Jesus.
Paul was an apostle.
Heaven is our home.
Turn with me to Romans chapter 8.
In the book of Genesis, God created the heavens and the earth.
The Psalms teach us to praise God.
Proverbs says the fear of the Lord is the beginning of wisdom.
Isaiah, Ephesians, Philippians, Hebrews and Revelation.
Matthew, Mark, Luke and Acts.
Brothers and sisters, God bless you.
Peter denied Jesus three times.
The church is the body of Christ.
Repent and be baptized.
The kingdom of God is near.
Hallelujah!
In the name of the Father, the Son, and the Holy Spirit.
Moses led the Israelites out of Egypt.
David killed Goliath.
Abraham had great faith.
Pray for one another.
The Lord is my shepherd.
We give thanks to God.
You must be born again.
His grace is enough for you.
The Word became flesh.
Jesus is the Lamb of God.""".splitlines()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixes", action="store_true")
    ap.add_argument("--to", nargs="+", default=["th", "zh"])
    a = ap.parse_args()
    from riah_translate.mt import NllbTranslator
    glossary = None
    if a.fixes:
        from riah_translate.church_words import church_glossary
        glossary = church_glossary()
    mt = NllbTranslator(threads=4)
    for s in SENTENCES:
        print(s)
        for t in a.to:
            print(f"  {t}: {mt.translate(s, 'en', t, glossary=glossary)}")
    # Does CTranslate2's score include forced prefix tokens? (needed to compare a forced and a free result)
    src = "We are saved by grace through faith."
    pieces = ["eng_Latn"] + mt.tok.encode(src, add_special_tokens=False).tokens + ["</s>"]
    free = mt.translator.translate_batch([pieces], target_prefix=[["tha_Thai"]], beam_size=4, return_scores=True)[0]
    hyp = free.hypotheses[0]
    full = mt.translator.translate_batch([pieces], target_prefix=[hyp], beam_size=4, return_scores=True)[0]
    part = mt.translator.translate_batch([pieces], target_prefix=[hyp[:3]], beam_size=4, return_scores=True)[0]
    print("\nprefix scores: free", round(free.scores[0], 3), "| forced full", round(full.scores[0], 3),
          "| forced 3", round(part.scores[0], 3), "| same?", full.hypotheses[0] == hyp)


if __name__ == "__main__":
    main()
