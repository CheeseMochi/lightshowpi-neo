"""LightshowManager playlist loop: synchronized_lights.py plays one song per
run, so the API must start the next run until a stop condition.

Runs a real stand-in script (each "song" logs a line and lasts 0.3 s).
"""
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from api.models.schemas import LightshowState
from api.services.lightshow_manager import LightshowManager

pytestmark = pytest.mark.unit

SONG = """import sys, time, os
with open(os.environ["RUNS_LOG"], "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\\n")
time.sleep(float(os.environ.get("SONG_SECONDS", "0.3")))
sys.exit(int(os.environ.get("SONG_EXIT", "0")))
"""


class FakeConfig:
    def __init__(self, playlist):
        self.lightshow = SimpleNamespace(playlist_path=str(playlist))

    def reload_state(self):
        pass

    def get_state(self, key, default=""):
        return default


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    script = tmp_path / "fake_show.py"
    script.write_text(SONG)
    playlist = tmp_path / "xmas.playlist"
    playlist.write_text("Song\t/x.m4a\n")
    runs_log = tmp_path / "runs.txt"
    monkeypatch.setenv("SYNCHRONIZED_LIGHTS_HOME", str(tmp_path))
    monkeypatch.setenv("RUNS_LOG", str(runs_log))
    m = LightshowManager(FakeConfig(playlist))
    m.synchronized_lights_script = str(script)
    m.startup_check_seconds = 0.05
    m.min_run_seconds = 0.2
    m.fast_failure_pause = 0.05
    m.runs = lambda: runs_log.read_text().splitlines() if runs_log.exists() else []
    yield m
    m.stop()


def wait_for(cond, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_playlist_plays_song_after_song_until_stopped(mgr):
    assert mgr.start() is True
    assert wait_for(lambda: len(mgr.runs()) >= 3), mgr.runs()
    assert all("--playlist" in r for r in mgr.runs())
    assert mgr.is_running()
    assert mgr.get_status()["state"] == LightshowState.PLAYING.value

    assert mgr.stop() is True
    count = len(mgr.runs())
    time.sleep(1.0)
    assert len(mgr.runs()) == count, "a song started after stop"
    assert not mgr.is_running()
    assert mgr.state == LightshowState.STOPPED


def test_running_between_songs(mgr, monkeypatch):
    monkeypatch.setenv("SONG_SECONDS", "0.25")
    mgr.start()
    # Sample often: is_running must never flicker false while the loop is alive
    end = time.time() + 1.5
    while time.time() < end:
        assert mgr.is_running()
        time.sleep(0.01)


def test_single_song_plays_once(mgr, tmp_path):
    song = tmp_path / "one.m4a"
    song.write_text("")
    assert mgr.start(song=str(song)) is True
    assert wait_for(lambda: len(mgr.runs()) >= 1)
    time.sleep(1.0)
    assert len(mgr.runs()) == 1
    assert "--file" in mgr.runs()[0]


def test_ambient_mode_is_not_looped(mgr, monkeypatch):
    monkeypatch.setenv("SONG_SECONDS", "0.3")
    assert mgr.start(mode="ambient") is True
    time.sleep(1.0)
    assert len(mgr.runs()) == 1


def test_crash_loop_stops_with_error(mgr, monkeypatch):
    # Runs that end almost immediately (bad config, crash) must not spin forever
    monkeypatch.setenv("SONG_SECONDS", "0.1")
    mgr.startup_check_seconds = 0.0
    # Python start-up alone can take ~1 s on a Zero 2 W: count anything under 5 s
    mgr.min_run_seconds = 5.0
    assert mgr.start() is True
    assert wait_for(lambda: mgr.state == LightshowState.ERROR, timeout=10)
    assert "3 times in a row" in mgr.error_message
    count = len(mgr.runs())
    time.sleep(0.5)
    assert len(mgr.runs()) == count
    assert not mgr.is_running()


def test_test_mode_stops_the_loop(mgr):
    tm = Mock()
    tm.is_active.return_value = False
    mgr.set_test_mode_manager(tm)
    mgr.start()
    assert wait_for(lambda: len(mgr.runs()) >= 1)
    tm.is_active.return_value = True   # test mode entered (e.g. after a stop race)
    assert wait_for(lambda: not mgr.is_running(), timeout=5)
    count = len(mgr.runs())
    time.sleep(0.6)
    assert len(mgr.runs()) == count


def test_restart_keeps_looping(mgr):
    mgr.start()
    assert wait_for(lambda: len(mgr.runs()) >= 2)
    assert mgr.restart() is True
    before = len(mgr.runs())
    assert wait_for(lambda: len(mgr.runs()) >= before + 2)
