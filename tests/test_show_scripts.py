"""bin/start_music_and_lights and bin/stop_music_and_lights, run for real with
fake `python` and `sudo` on PATH.

Regression: Neo's start script ran synchronized_lights.py once, which plays a
single song, so a cron-started show stopped after one song; and the stop
script matched processes by $SYNCHRONIZED_LIGHTS_HOME in their command line,
which a relative `py/synchronized_lights.py` under the conda python lacks.
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(sys.platform.startswith("win") or not shutil.which("pgrep"),
                       reason="needs bash and pgrep"),
]

FAKE_PYTHON = """#!/bin/bash
# Fake python: a "song" logs one line and lasts 0.5 s
case "$*" in
  *synchronized_lights.py*) echo "run $*" >> "$SYNCHRONIZED_LIGHTS_HOME/logs/runs.txt"; sleep 0.5 ;;
  *-c*) echo False ;;
  *) exit 0 ;;
esac
"""

FAKE_SUDO = """#!/bin/bash
[ "$1" = "-E" ] && shift
exec "$@"
"""


@pytest.fixture
def home(tmp_path):
    home = tmp_path / "lsphome"
    (home / "bin").mkdir(parents=True)
    (home / "py").mkdir()
    (home / "logs").mkdir()
    for script in ("start_music_and_lights", "stop_music_and_lights", "env_error_msg"):
        shutil.copy(REPO / "bin" / script, home / "bin" / script)
    (home / "py" / "synchronized_lights.py").write_text("# placeholder\n")
    fake = tmp_path / "fakebin"
    fake.mkdir()
    for name, body in (("python", FAKE_PYTHON), ("sudo", FAKE_SUDO)):
        (fake / name).write_text(body)
        (fake / name).chmod(0o755)
    env = dict(os.environ, SYNCHRONIZED_LIGHTS_HOME=str(home), LSP_MIN_RUN_SECONDS="0",
               PATH=f"{fake}:{os.environ['PATH']}")
    yield home, env
    subprocess.run(["bash", str(home / "bin" / "stop_music_and_lights")], env=env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)


def runs(home):
    f = home / "logs" / "runs.txt"
    return f.read_text().splitlines() if f.exists() else []


def wait_for(cond, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def test_start_loops_through_songs_until_stopped(home):
    home, env = home
    subprocess.run(["bash", str(home / "bin" / "start_music_and_lights")], env=env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=True)
    assert (home / "logs" / "show_loop.pid").exists()
    # More than one song: the loop starts the next run after each one ends
    assert wait_for(lambda: len(runs(home)) >= 3), runs(home)
    assert all(str(home / "py" / "synchronized_lights.py") in r for r in runs(home))

    subprocess.run(["bash", str(home / "bin" / "stop_music_and_lights")], env=env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=True)
    assert not (home / "logs" / "show_loop.pid").exists()
    count = len(runs(home))
    time.sleep(1.5)
    assert len(runs(home)) == count, "loop kept starting songs after stop"


def test_file_argument_plays_once(home):
    home, env = home
    subprocess.run(["bash", str(home / "bin" / "start_music_and_lights"), "--file=/tmp/x.mp3"],
                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=True)
    assert wait_for(lambda: len(runs(home)) >= 1)
    time.sleep(1.5)
    assert len(runs(home)) == 1
    assert not (home / "logs" / "show_loop.pid").exists()


def test_restart_replaces_the_previous_loop(home):
    home, env = home
    start = ["bash", str(home / "bin" / "start_music_and_lights")]
    subprocess.run(start, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=True)
    first = (home / "logs" / "show_loop.pid").read_text().strip()
    subprocess.run(start, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=True)
    second = (home / "logs" / "show_loop.pid").read_text().strip()
    assert first != second
    assert subprocess.run(["kill", "-0", first], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0, \
        "old playlist loop still running"
