"""Builds the browser version of Riah Church Translate (riahstudio.com/churchtranslate/live/) as a
folder of small static files.

    python tools/build_browser.py "../Riah Studio/app/public/churchtranslate/live"

Everything heavy runs in the visitor's browser: the speech and translation models download from
Hugging Face's free CDN into the browser's own cache, and transformers.js comes from jsDelivr. The
site only serves this folder (about 0.6 MB), so hosting it costs nothing extra.

The church screen is the app's own (riah_translate/web/screen.*, copied unchanged) with one small
script added, screen-feed.js, which feeds it from the operator page over a BroadcastChannel.

The page needs these response headers on /churchtranslate/live/ (see HEADERS below):
cross-origin isolation (so the models can use more than one processor core) and its own CSP.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "riah_translate", "web")
SRC = os.path.join(ROOT, "browser")
APP_NAME = "Church Translate"

# Sent with every file under /churchtranslate/live/ (the operator page, the church screen and the
# workers). COEP require-corp works in every browser (credentialless is not in Safari); Hugging
# Face and jsDelivr both send Cross-Origin-Resource-Policy: cross-origin.
HEADERS = {
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Embedder-Policy": "require-corp",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Content-Security-Policy": "; ".join([
        "default-src 'self'",
        "script-src 'self' https://cdn.jsdelivr.net 'wasm-unsafe-eval' blob:",
        "worker-src 'self' blob:",
        "connect-src 'self' https://cdn.jsdelivr.net https://huggingface.co https://*.huggingface.co https://*.hf.co",
        "style-src 'self'",
        "font-src 'self'",
        "img-src 'self' data: blob:",
        "media-src 'self' blob:",
        "frame-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'none'",
        "frame-ancestors 'self'",
    ]),
    "Permissions-Policy": "microphone=(self), camera=(), geolocation=()",
    "Referrer-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
}

# The parts of bible_words.json the translator uses (names, books, known misspellings).
BIBLE_KEEP = ("about", "sources", "names", "books", "miss")


def find_kit():
    """The live Riah UI kit (Riah Studio/brand/design-system) when this folder sits in the studio
    workspace, however deep (worktrees); else the app's own copy."""
    d = ROOT
    for _ in range(8):
        p = os.path.join(d, "Riah Studio", "brand", "design-system", "riah-interface.css")
        if os.path.isfile(p):
            return p
        d = os.path.dirname(d)
    return os.path.join(WEB, "vendor", "riah-interface.css")


def version():
    try:
        h = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                           check=True).stdout.strip()
    except Exception:
        h = "local"
    return f"{_dt.date.today().isoformat()} · {h}"


def write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def copy_text(src, dst):
    """Copies a text file with LF line endings."""
    with open(src, encoding="utf-8") as f:
        write(dst, f.read().replace("\r\n", "\n"))


def screen(out):
    scr = os.path.join(out, "screen")
    if os.path.isdir(scr):
        shutil.rmtree(scr)
    os.makedirs(os.path.join(scr, "vendor"))
    for f in ("screen.css", "screen.js", "icon.svg"):
        copy_text(os.path.join(WEB, f), os.path.join(scr, f))
    copy_text(os.path.join(SRC, "screen-feed.js"), os.path.join(scr, "screen-feed.js"))
    shutil.copytree(os.path.join(WEB, "type"), os.path.join(scr, "type"))
    shutil.copytree(os.path.join(WEB, "vendor", "fonts"), os.path.join(scr, "vendor", "fonts"))
    shutil.copyfile(find_kit(), os.path.join(scr, "vendor", "riah-interface.css"))   # byte for byte: the style check compares

    html = open(os.path.join(WEB, "screen.html"), encoding="utf-8").read().replace("\r\n", "\n")
    assert f"Riah {APP_NAME}" in html, "the app's name changed in screen.html: update build_browser.py"
    assert "style=" not in html, "screen.html has a style attribute: the page's CSP would block it"
    html = html.replace('<html lang="en">', '<html lang="en" data-feed="demo">', 1)
    html = html.replace("</title>", '</title>\n<meta name="robots" content="noindex, nofollow">', 1)
    html = html.replace('<script src="screen.js"></script>',
                        '<script src="screen.js"></script>\n<script src="screen-feed.js"></script>', 1)
    assert 'data-feed="demo"' in html and "screen-feed.js" in html, "screen.html changed: update build_browser.py"
    write(os.path.join(scr, "screen.html"), html)


def main(out):
    out = os.path.abspath(out)
    os.makedirs(out, exist_ok=True)
    for f in sorted(os.listdir(SRC)):
        if f == "screen-feed.js" or not f.endswith((".js", ".html", ".css")):
            continue
        copy_text(os.path.join(SRC, f), os.path.join(out, f))
    copy_text(os.path.join(WEB, "control.css"), os.path.join(out, "control.css"))

    page = open(os.path.join(out, "index.html"), encoding="utf-8").read()
    assert '<meta name="riah-version" content="dev">' in page
    page = page.replace('<meta name="riah-version" content="dev">', f'<meta name="riah-version" content="{version()}">', 1)
    for f in ("index.html", "licenses.html"):
        assert "style=" not in open(os.path.join(SRC, f), encoding="utf-8").read(), f"{f} has a style attribute"
        assert "<script>" not in open(os.path.join(SRC, f), encoding="utf-8").read(), f"{f} has an inline script"
    write(os.path.join(out, "index.html"), page)

    screen(out)

    with open(os.path.join(ROOT, "riah_translate", "bible_words.json"), encoding="utf-8") as f:
        doc = json.load(f)
    small = {k: doc[k] for k in BIBLE_KEEP if k in doc}
    with open(os.path.join(out, "bible_words.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(small, f, ensure_ascii=False, separators=(",", ":"))

    total = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(out) for f in fs)
    print(f"built {out} ({total / 1e6:.2f} MB)")
    print("serve /churchtranslate/live/* with these headers:")
    for k, v in HEADERS.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    if sys.argv[1:] == ["--headers"]:      # for tools/browser_clip_test.mjs's local server
        print(json.dumps(HEADERS))
    elif len(sys.argv) != 2:
        sys.exit(__doc__)
    else:
        main(sys.argv[1])
