"""Dev preview: serves the operator page and church screen, replaying a recorded test run on a loop.
No models or microphone needed.  python "Riah Translate/tools/dev_replay.py" [events.jsonl] [port]
"""
import os
import sys

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(root)
sys.path.insert(0, root)
from riah_translate.app import main  # noqa: E402

events = sys.argv[1] if len(sys.argv) > 1 else "tests/results/en-th-clean.jsonl"
port = sys.argv[2] if len(sys.argv) > 2 else "5266"
main(["--replay", events, "--loop", "--ui", "none", "--port", port])
