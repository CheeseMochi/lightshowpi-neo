"""Wiring of per-song sequence selection into synchronized_lights.get_song().

The no-key path must behave exactly as before (including the exit on a
mismatched legacy CSV); with a ``sequence`` key the show never exits.
"""

import importlib
import os
import shutil
import sys
import types
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "test_tone.wav")


@pytest.fixture(scope="module")
def sl():
    """synchronized_lights, importable without ALSA (only setup_audio uses it).

    A stub ALSA module is removed again afterwards (with the show module that
    saw it) so other tests still see the real platform.
    """
    stubbed = False
    try:
        import alsaaudio  # noqa: F401
    except ImportError:
        sys.modules["alsaaudio"] = types.ModuleType("alsaaudio")
        stubbed = True
    had_module = "synchronized_lights" in sys.modules
    try:
        module = importlib.import_module("synchronized_lights")
    except Exception as error:  # pragma: no cover - depends on the platform
        module = None
        reason = str(error)
    try:
        if module is None:
            pytest.skip(f"synchronized_lights not importable: {reason}")
        yield module
    finally:
        if stubbed:
            sys.modules.pop("alsaaudio", None)
            if not had_module:
                sys.modules.pop("synchronized_lights", None)


class FakeCM:
    def __init__(self, home, sequences_dir, gpio_len=3):
        self.home_dir = str(home)
        self.hardware = SimpleNamespace(gpio_len=gpio_len)
        self.lightshow = SimpleNamespace(sequences_dir=str(sequences_dir), songname_command="",
                                         allow_sequence_audition=True)
        self.state = {}
        self.published = {}

    def get_state(self, key, default=""):
        return self.state.get(key, default)

    def update_state(self, key, value):
        self.state[key] = str(value)

    def update_states(self, values):
        self.published.update(values)
        self.state.update({k: str(v) for k, v in values.items()})


@pytest.fixture
def env(sl, tmp_path, monkeypatch):
    (tmp_path / "logs").mkdir()
    music = tmp_path / "music"
    music.mkdir()
    song = music / "tone.wav"
    shutil.copy(FIXTURE, song)
    seqdir = tmp_path / "sequences"
    cm = FakeCM(tmp_path, seqdir)
    monkeypatch.setattr(sl, "cm", cm)
    monkeypatch.setattr(sl, "args", SimpleNamespace(file=str(song), playlist=None))
    show = sl.Lightshow.__new__(sl.Lightshow)
    show.sequence_type = "auto"
    show.sequence_filename = None
    return SimpleNamespace(show=show, cm=cm, song=song, music=music, seqdir=seqdir)


def write_cfg(env, value):
    (env.music / ".tone.wav.cfg").write_text(f"[custom_lightshow]\nsequence = {value}\n")


def write_legacy(env, row="1,2,3"):
    (env.music / "tone.csv").write_text(row + "\n")


def write_variant(env, name, row="0,255,0"):
    folder = env.seqdir / "tone"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.csv").write_text(row + "\n")


def test_no_key_no_csv_is_fft(env):
    env.show.get_song()
    assert env.show.sequence_type == "auto"
    assert env.cm.published["song_sequence"] == "fft"


def test_no_key_legacy_csv(env):
    write_legacy(env)
    env.show.get_song()
    assert env.show.sequence_type == "csv"
    assert env.show.sequence_filename == str(env.music / "tone.csv")
    assert env.cm.published["song_sequence"] == "csv"


def test_no_key_mismatched_legacy_still_exits(env):
    write_legacy(env, "1,2")
    with pytest.raises(SystemExit):
        env.show.get_song()


def test_variant_key(env):
    write_legacy(env)
    write_variant(env, "onset_bands")
    write_cfg(env, "onset_bands")
    env.show.get_song()
    assert env.show.sequence_type == "csv"
    assert env.show.sequence_filename == str(env.seqdir / "tone" / "onset_bands.csv")
    assert env.cm.published["song_sequence"] == "onset_bands"


def test_fft_key_ignores_legacy(env):
    write_legacy(env)
    write_cfg(env, "fft")
    env.show.get_song()
    assert env.show.sequence_type == "auto"
    assert env.cm.published["song_sequence"] == "fft"


def test_rotate_key_advances_state(env):
    write_variant(env, "a")
    write_variant(env, "b")
    write_cfg(env, "rotate:a,b")
    env.show.get_song()
    assert env.cm.published["song_sequence"] == "a"
    env.show.get_song()
    assert env.cm.published["song_sequence"] == "b"
    assert env.cm.state["seq_rot_tone"] == "0"


def next_play(env):
    """The show runs one process per song: start the next play fresh."""
    env.show.sequence_type = "auto"
    env.show.sequence_filename = None
    env.show.get_song()


def test_one_shot_used_once_then_saved_setting(env):
    write_variant(env, "onset_bands")
    write_variant(env, "stems")
    write_cfg(env, "onset_bands")
    env.cm.state["sequence_once"] = "tone|stems"
    env.show.get_song()
    assert env.cm.published["song_sequence"] == "stems"
    assert env.show.sequence_filename == str(env.seqdir / "tone" / "stems.csv")
    assert env.cm.state["sequence_once"] == ""
    next_play(env)
    assert env.cm.published["song_sequence"] == "onset_bands"


def test_one_shot_without_saved_setting(env):
    write_variant(env, "stems")
    env.cm.state["sequence_once"] = "tone|stems"
    env.show.get_song()
    assert env.cm.published["song_sequence"] == "stems"
    next_play(env)
    assert env.show.sequence_type == "auto"
    assert env.cm.published["song_sequence"] == "fft"


def test_one_shot_does_not_advance_rotation(env):
    write_variant(env, "a")
    write_variant(env, "b")
    write_cfg(env, "rotate:a,b")
    env.cm.state["sequence_once"] = "tone|b"
    env.show.get_song()
    assert env.cm.published["song_sequence"] == "b"
    assert "seq_rot_tone" not in env.cm.state
    next_play(env)
    assert env.cm.published["song_sequence"] == "a"


def test_one_shot_ignored_but_cleared_when_flag_off(env):
    write_variant(env, "onset_bands")
    write_variant(env, "stems")
    write_cfg(env, "onset_bands")
    env.cm.lightshow.allow_sequence_audition = False
    env.cm.state["sequence_once"] = "tone|stems"
    env.show.get_song()
    assert env.cm.published["song_sequence"] == "onset_bands"
    assert env.cm.state["sequence_once"] == ""


def test_one_shot_for_other_song_is_cleared(env):
    write_variant(env, "stems")
    env.cm.state["sequence_once"] = "other|stems"
    env.show.get_song()
    assert env.cm.published["song_sequence"] == "fft"
    assert env.cm.state["sequence_once"] == ""


def test_one_shot_missing_variant_falls_back_and_never_exits(env):
    write_legacy(env, "1,2")  # mismatched legacy CSV would exit() without a setting
    env.cm.state["sequence_once"] = "tone|gone"
    env.show.get_song()
    assert env.show.sequence_type == "auto"
    assert env.cm.published["song_sequence"] == "fft"


def test_key_with_bad_variant_and_mismatched_legacy_does_not_exit(env):
    write_legacy(env, "1,2")
    write_variant(env, "wide", "1,2,3,4")
    write_cfg(env, "wide")
    env.show.get_song()
    assert env.show.sequence_type == "auto"
    assert env.cm.published["song_sequence"] == "fft"
