"""Builds the public demo page (riahstudio.com/churchtranslate) from the app's own church screen and
a recorded run.

    python tools/build_demo.py tests/results/mac-en-thzh.jsonl "../Riah Studio/app/public/churchtranslate"
    python tools/build_demo.py tests/results/mac-en-thzh.jsonl "../Riah Studio/app/public/livetranslate" "Live Translate"

The same app goes by two names (Captain, 2026-11-23): Riah Church Translate (the app, and the pastor's
link) and Riah Live Translate. The third argument picks the name the page shows.

The page (demo/) plays the run into a copy of the real screen (riah_translate/web/screen.*), so what
the pastor sees is what the projector shows. Only the screen's files, its fonts and the recorded
lines are copied: no app, no models, no audio.
"""
from __future__ import annotations

import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "riah_translate", "web")
DEMO = os.path.join(ROOT, "demo")
# The live Riah UI kit, when this folder sits in the studio workspace; else the app's copy of it.
KIT = os.path.join(ROOT, "..", "Riah Studio", "brand", "design-system", "riah-interface.css")

KEEP = {
    "line": ("id", "seg", "src", "keepDraft"),
    "translation": ("id", "lang", "dst"),
    "draft": ("seg", "lang", "text"),
    "partial": ("text", "seg"),
}


def events(path):
    if path.endswith("run.json"):    # a demo's own run, already trimmed: rebuild the page around it
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        return doc["events"], dict(doc["about"])
    out, about = [], {}
    for raw in open(path, encoding="utf-8"):
        if not raw.strip():
            continue
        e = json.loads(raw)
        t = e.get("type")
        if t == "summary":
            about = {k: e[k] for k in ("model", "audioSec", "tgts", "src") if k in e}
        elif t in KEEP and "at" in e:
            out.append({"type": t, "at": round(e["at"], 2), **{k: e[k] for k in KEEP[t] if k in e}})
    out.sort(key=lambda e: e["at"])
    # Skip the quiet before the first word (model loading, the speaker settling): start a second in.
    lead = max(0.0, out[0]["at"] - 1.0) if out else 0.0
    for e in out:
        e["at"] = round(e["at"] - lead, 2)
    return out, about


def initials(name):
    """'Church Translate' -> 'RCT' (the corner label)."""
    return "R" + "".join(w[0] for w in name.split())


def bible_count():
    with open(os.path.join(ROOT, "riah_translate", "bible_words.json"), encoding="utf-8") as f:
        doc = json.load(f)
    n = len(doc.get("names", {})) + len(doc.get("books", []))
    return f"{n // 100 * 100:,}"


APP_NAME = "Church Translate"     # what riah_translate/web/screen.html calls it


def main(run_path, out, name=APP_NAME):
    evs, about = events(run_path)
    if not evs:
        sys.exit(f"no events in {run_path}")
    os.makedirs(out, exist_ok=True)
    scr = os.path.join(out, "screen")
    if os.path.isdir(scr):
        shutil.rmtree(scr)
    os.makedirs(os.path.join(scr, "vendor"))
    for f in ("screen.css", "screen.js", "icon.svg"):
        shutil.copy2(os.path.join(WEB, f), scr)
    shutil.copytree(os.path.join(WEB, "type"), os.path.join(scr, "type"))
    shutil.copytree(os.path.join(WEB, "vendor", "fonts"), os.path.join(scr, "vendor", "fonts"))
    kit = KIT if os.path.isfile(KIT) else os.path.join(WEB, "vendor", "riah-interface.css")
    shutil.copy2(kit, os.path.join(scr, "vendor", "riah-interface.css"))

    html = open(os.path.join(WEB, "screen.html"), encoding="utf-8").read()
    html = html.replace('<html lang="en">', '<html lang="en" data-feed="demo">', 1)
    html = html.replace('</title>', '</title>\n<meta name="robots" content="noindex, nofollow">', 1)
    assert 'data-feed="demo"' in html, "screen.html changed: update build_demo.py"
    assert f"Riah {APP_NAME}" in html, "the app's name changed: update APP_NAME"
    html = html.replace(f"Riah {APP_NAME}", f"Riah {name}").replace(f">{APP_NAME}<", f">{name}<")
    html = html.replace(initials(APP_NAME) + " · ", initials(name) + " · ")
    open(os.path.join(scr, "screen.html"), "w", encoding="utf-8", newline="\n").write(html)

    for f in ("demo.css", "demo.js"):
        shutil.copy2(os.path.join(DEMO, f), out)
    page = open(os.path.join(DEMO, "index.html"), encoding="utf-8").read()
    page = page.replace("{{bible_count}}", bible_count())
    page = page.replace("{{product}}", name).replace("{{initials}}", initials(name))
    assert "{{" not in page, "a placeholder in demo/index.html has no value"
    open(os.path.join(out, "index.html"), "w", encoding="utf-8", newline="\n").write(page)

    about.setdefault("from", os.path.basename(run_path))
    with open(os.path.join(out, "run.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"about": about, "events": evs}, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{len(evs)} events, {evs[-1]['at']:.0f} s, languages {about.get('tgts')} -> {out}")


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        sys.exit(__doc__)
    main(*sys.argv[1:])
