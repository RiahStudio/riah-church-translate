# PyInstaller recipe for the Mac and Windows apps. Built on GitHub Actions (mac-build.yml and
# windows-build.yml):
#   pyinstaller --noconfirm packaging/riah-translate.spec
# Models are not bundled; the app downloads them on first launch (~2 GB, once).
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent
APP = "Riah Church Translate"
sys.path.insert(0, str(ROOT))

datas = [(str(ROOT / "riah_translate" / "web"), "riah_translate/web"),
         (str(ROOT / "riah_translate" / "bible_words.json"), "riah_translate"),
         (str(ROOT / "riah_translate" / "bible"), "riah_translate/bible")]   # verse texts (~5 MB)
binaries = []
hidden = collect_submodules("riah_translate") + collect_submodules("huggingface_hub")
try:
    hidden += collect_submodules("pypdf")      # PDF sermon notes (imported only when one is added)
except Exception:
    pass

# Packages that carry their own native libraries or data files (models' helpers, PortAudio, VAD).
# pythonnet and clr_loader are Windows only: pywebview talks to Edge WebView2 through them.
for pkg in ("faster_whisper", "ctranslate2", "onnxruntime", "av", "tokenizers",
            "sounddevice", "_sounddevice_data", "webview", "pythonnet", "clr_loader"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:
        continue
    datas += d
    binaries += b
    hidden += h

a = Analysis([str(ROOT / "packaging" / "launcher.py")], pathex=[str(ROOT)], binaries=binaries,
             datas=datas, hiddenimports=hidden, excludes=["tkinter", "matplotlib", "torch"],
             noarchive=False)
pyz = PYZ(a.pure)
ico = ROOT / "packaging" / "icon.ico"   # Riah Icons pack, riah-ext-translate.svg
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=APP, console=False,
          argv_emulation=False, target_arch=None, codesign_identity=None,
          icon=str(ico) if sys.platform == "win32" and ico.exists() else None)
coll = COLLECT(exe, a.binaries, a.datas, name=APP)

if sys.platform == "darwin":
    from riah_translate import __version__

    icon = ROOT / "packaging" / "icon.icns"
    app = BUNDLE(
        coll,
        name=f"{APP}.app",
        icon=str(icon) if icon.exists() else None,
        bundle_identifier="com.riahstudio.translate",
        version=__version__,
        info_plist={
            "CFBundleDisplayName": APP,
            "CFBundleShortVersionString": __version__,
            "NSMicrophoneUsageDescription":
                "Riah Church Translate listens to the sermon to show live subtitles. Nothing leaves this Mac.",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "LSApplicationCategoryType": "public.app-category.productivity",
        },
    )
