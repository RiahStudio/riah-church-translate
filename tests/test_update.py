"""The "a newer version is out" line (update.py): dated versions compare right, odd answers and a dead
network mean no line (never an error), and the button opens the download page without touching anything
else. No network, no browser (webbrowser.open is stubbed). Safe to run anywhere:

    python tests/test_update.py
"""
import sys
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from riah_translate import update  # noqa: E402


def test_dated_versions_compare_like_dates():
    assert update.parse("v2026.11.28") == (2026, 11, 28)
    assert update.parse("2026.11.28.2") == (2026, 11, 28, 2)
    assert update.parse("0.3.1") == (0, 3, 1)
    for junk in (None, "", "latest", "v", "1", "2026.11.28-beta", 5, ["2026.11.28"]):
        assert update.parse(junk) is None, junk
    assert update.newer("v2026.11.29", "2026.11.28")
    assert update.newer("2026.11.28.2", "2026.11.28"), "a second release the same day counts"
    assert update.newer("2026.12.1", "2026.11.28")
    assert update.newer("v2026.11.28", "0.3.1"), "a dated version is newer than the old 0.x ones"
    assert not update.newer("2026.11.28", "2026.11.28"), "the same version is not news"
    assert not update.newer("2026.11.27", "2026.11.28"), "an older release is not news"
    assert not update.newer("0.3.1", "2026.11.28")
    assert not update.newer("nonsense", "2026.11.28")
    assert not update.newer("2026.11.29", "nonsense")


def test_check_says_only_what_is_needed():
    out = update.check("2026.11.28", fetch=lambda: {"tag_name": "v2026.11.29"})
    assert out == {"version": "2026.11.29", "url": update.PAGE}, out
    assert update.check("2026.11.28", fetch=lambda: {"tag_name": "v2026.11.28"}) is None
    assert update.check("2026.11.28", fetch=lambda: {"tag_name": "v2026.11.27"}) is None


def test_drafts_and_pre_releases_are_not_offered():
    assert update.check("2026.11.28", fetch=lambda: {"tag_name": "v2026.12.1", "draft": True}) is None
    assert update.check("2026.11.28", fetch=lambda: {"tag_name": "v2026.12.1", "prerelease": True}) is None


def test_a_failure_of_any_kind_is_just_no_line():
    def boom():
        raise OSError("no network")

    def hang_up():
        raise TimeoutError("slow")
    assert update.check("2026.11.28", fetch=boom) is None
    assert update.check("2026.11.28", fetch=hang_up) is None
    for odd in (None, [], "text", 7, {}, {"tag_name": None}, {"tag_name": ["x"]}, {"tag_name": "v9"},
                {"name": "2026.12.1"}):
        assert update.check("2026.11.28", fetch=lambda odd=odd: odd) is None, odd


def test_the_real_fetch_gives_up_quietly_with_nothing_to_talk_to():
    old = update.LATEST_URL
    update.LATEST_URL = "http://127.0.0.1:9/none"       # nothing listens here
    update.TIMEOUT = 2
    try:
        t = time.monotonic()
        assert update.check("2026.11.28") is None
        assert time.monotonic() - t < 6
    finally:
        update.LATEST_URL = old


def test_the_app_checks_in_the_background_and_the_button_opens_the_page():
    from riah_translate.app import App

    class Stub:
        update_info = None
        check_for_update = App.check_for_update
        open_update = App.open_update

    update_check = update.check
    opened = []
    real_open = webbrowser.open
    webbrowser.open = lambda url, *a, **k: opened.append(url) or True
    update.check = lambda *a, **k: {"version": "2026.12.1", "url": update.PAGE}
    try:
        s = Stub()
        t = time.monotonic()
        s.check_for_update()
        assert time.monotonic() - t < 0.5, "the check never makes the app wait"
        end = time.monotonic() + 3
        while s.update_info is None and time.monotonic() < end:
            time.sleep(0.02)
        assert s.update_info == {"version": "2026.12.1", "url": update.PAGE}
        out = s.open_update()
        assert out["ok"] is True and opened == ["https://riahstudio.com/churchtranslate/"], opened
        webbrowser.open = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no browser"))
        try:
            s.open_update()
        except ValueError as e:
            assert "riahstudio.com/churchtranslate" in str(e), "a browser that will not open still gets the link"
        else:
            raise AssertionError("open_update must say it could not open the browser")
    finally:
        webbrowser.open = real_open
        update.check = update_check


if __name__ == "__main__":
    tests = [(k, v) for k, v in list(globals().items()) if k.startswith("test_")]
    for name, fn in tests:
        fn()
        print(f"ok {name}", flush=True)
    print(f"PASS ({len(tests)} checks)")
