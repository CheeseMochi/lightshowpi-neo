"""
Pattern lab: build, compare, rate and push light patterns from the Mac.

    make lab                                  # the GUI (browser), in lightshowpi-neo-lab
    conda activate lightshowpi-neo-lab
    python -m research.lab --help             # the CLI

See research/README.md. The lab composes on the Mac (librosa, Demucs) and
talks to the show Pi only through its API (upload, audition, set).
"""

import os
import sys

RESEARCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_DIR = os.path.dirname(RESEARCH_DIR)
for _path in (RESEARCH_DIR, os.path.join(REPO_DIR, "py")):
    if _path not in sys.path:
        sys.path.insert(0, _path)
