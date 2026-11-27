"""Tests for per-song sequence selection (py/sequence_select.py)."""

import random

import pytest

import sequence_select as ss

pytestmark = pytest.mark.unit

CHANNELS = 3


class State:
    """In-memory stand-in for cm.get_state / cm.update_state."""

    def __init__(self, **values):
        self.values = dict(values)

    def get(self, key, default=''):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


@pytest.fixture
def song(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    path = music / "Carol Bells.mp3"
    path.write_bytes(b"")
    return path


@pytest.fixture
def seqdir(tmp_path):
    d = tmp_path / "sequences"
    d.mkdir()
    return d


def add_variant(seqdir, name, rows=("0,255,0", "255,0,255"), stem="Carol Bells"):
    folder = seqdir / stem
    folder.mkdir(exist_ok=True)
    path = folder / f"{name}.csv"
    path.write_text("\n".join(rows) + "\n")
    return path


def add_legacy(song, rows=("1,2,3",)):
    path = song.with_suffix(".csv")
    path.write_text("\n".join(rows) + "\n")
    return path


def choose(setting, song, seqdir, state=None, rng=random):
    state = state or State()
    return ss.choose(setting, str(song), str(seqdir), CHANNELS, state.get, state.set, rng)


class TestParseSpec:

    @pytest.mark.parametrize("text,mode,choices", [
        ("fft", "single", ("fft",)),
        ("FFT", "single", ("fft",)),
        ("csv", "single", ("csv",)),
        ("onset_bands", "single", ("onset_bands",)),
        ("rotate:a,b, fft", "rotate", ("a", "b", "fft")),
        ("Random: a , b", "random", ("a", "b")),
    ])
    def test_valid(self, text, mode, choices):
        assert ss.parse_spec(text) == ss.Spec(mode, choices)

    @pytest.mark.parametrize("text", [
        "", "  ", "rotate:", "shuffle:a,b", "a,b", "../etc/passwd", "a/b",
        "rotate:a,../b", "rotate", "random", "x" * 65,
    ])
    def test_invalid(self, text):
        with pytest.raises(ValueError):
            ss.parse_spec(text)


class TestReadSetting:

    def write_cfg(self, tmp_path, body):
        cfg = tmp_path / ".song.mp3.cfg"
        cfg.write_text(body)
        return str(cfg)

    def test_missing_file(self, tmp_path):
        assert ss.read_sequence_setting(str(tmp_path / "nope.cfg")) is None

    def test_no_key(self, tmp_path):
        cfg = self.write_cfg(tmp_path, "[custom_lightshow]\nattenuate_pct = 10\n[fft]\nchunk_size = 2048\n")
        assert ss.read_sequence_setting(cfg) is None

    @pytest.mark.parametrize("line", ["sequence =", "sequence"])
    def test_blank_key_is_legacy(self, tmp_path, line):
        cfg = self.write_cfg(tmp_path, f"[custom_lightshow]\n{line}\n")
        assert ss.read_sequence_setting(cfg) is None

    def test_value(self, tmp_path):
        cfg = self.write_cfg(tmp_path, "[custom_lightshow]\nsequence = rotate:a,b\n")
        assert ss.read_sequence_setting(cfg) == "rotate:a,b"

    def test_unreadable_file_is_legacy(self, tmp_path, caplog):
        cfg = self.write_cfg(tmp_path, "not an ini file\n")
        assert ss.read_sequence_setting(cfg) is None
        assert "Could not read sequence setting" in caplog.text


class TestCheckCsv:

    def test_ok(self, tmp_path):
        p = tmp_path / "a.csv"
        p.write_text("0,1,2\n3,4,5")
        assert ss.check_csv(str(p), 3) is None

    @pytest.mark.parametrize("body,reason", [
        ("", "empty"),
        ("0,1\n", "columns"),
        ("0,1,2\n0,1\n", "row 2"),
        ("0,1,2\n\n", "row 2"),
        ("0,x,2\n", "not numeric"),
    ])
    def test_bad(self, tmp_path, body, reason):
        p = tmp_path / "a.csv"
        p.write_text(body)
        assert reason in ss.check_csv(str(p), 3)

    def test_first_row_only(self, tmp_path):
        p = tmp_path / "a.csv"
        p.write_text("0,1,2\n0,1\n")
        assert ss.check_csv(str(p), 3, full=False) is None

    def test_missing(self, tmp_path):
        assert ss.check_csv(str(tmp_path / "nope.csv"), 3) == "not found"


class TestChoose:

    def test_fft_ignores_legacy_csv(self, song, seqdir):
        add_legacy(song)
        assert choose("fft", song, seqdir) == ss.Selection("auto", None, "fft")

    def test_csv_uses_legacy(self, song, seqdir):
        legacy = add_legacy(song)
        assert choose("csv", song, seqdir) == ss.Selection("csv", str(legacy), "csv")

    def test_csv_without_file_is_fft(self, song, seqdir):
        assert choose("csv", song, seqdir) == ss.Selection("auto", None, "fft")

    def test_variant(self, song, seqdir):
        path = add_variant(seqdir, "onset_bands")
        assert choose("onset_bands", song, seqdir) == ss.Selection("csv", str(path), "onset_bands")

    def test_missing_variant_falls_back_to_legacy(self, song, seqdir, caplog):
        legacy = add_legacy(song)
        assert choose("nope", song, seqdir) == ss.Selection("csv", str(legacy), "csv")
        assert "unusable" in caplog.text

    def test_missing_variant_falls_back_to_fft(self, song, seqdir):
        assert choose("nope", song, seqdir) == ss.Selection("auto", None, "fft")

    def test_mismatched_variant_falls_back(self, song, seqdir, caplog):
        add_variant(seqdir, "wide", rows=("1,2,3,4",))
        assert choose("wide", song, seqdir) == ss.Selection("auto", None, "fft")
        assert "expected 3" in caplog.text

    def test_mismatched_legacy_falls_back_to_fft_instead_of_exiting(self, song, seqdir, caplog):
        add_legacy(song, rows=("1,2",))
        assert choose("nope", song, seqdir) == ss.Selection("auto", None, "fft")
        assert "Legacy sequence" in caplog.text

    def test_malformed_setting_falls_back(self, song, seqdir, caplog):
        legacy = add_legacy(song)
        assert choose("rotate:", song, seqdir) == ss.Selection("csv", str(legacy), "csv")
        assert "Ignoring sequence setting" in caplog.text

    def test_rotate_cycles_and_persists(self, song, seqdir):
        a = add_variant(seqdir, "a")
        b = add_variant(seqdir, "b")
        state = State()
        picks = [choose("rotate:a,b,fft", song, seqdir, state) for _ in range(4)]
        assert [p.name for p in picks] == ["a", "b", "fft", "a"]
        assert picks[0].filename == str(a) and picks[1].filename == str(b)
        assert state.values == {"seq_rot_carol_bells": "1"}

    def test_rotate_resumes_from_state(self, song, seqdir):
        add_variant(seqdir, "a")
        add_variant(seqdir, "b")
        state = State(seq_rot_carol_bells="1")
        assert choose("rotate:a,b", song, seqdir, state).name == "b"
        assert state.values["seq_rot_carol_bells"] == "0"

    def test_rotate_index_out_of_range_or_garbage(self, song, seqdir):
        add_variant(seqdir, "a")
        add_variant(seqdir, "b")
        assert choose("rotate:a,b", song, seqdir, State(seq_rot_carol_bells="7")).name == "b"
        assert choose("rotate:a,b", song, seqdir, State(seq_rot_carol_bells="x")).name == "a"

    def test_rotate_bad_entry_falls_back_but_still_advances(self, song, seqdir):
        add_variant(seqdir, "a")
        state = State()
        assert choose("rotate:missing,a", song, seqdir, state).name == "fft"
        assert choose("rotate:missing,a", song, seqdir, state).name == "a"

    def test_rotate_state_write_failure_still_plays(self, song, seqdir):
        add_variant(seqdir, "a")
        add_variant(seqdir, "b")

        def boom(key, value):
            raise OSError("read-only")

        sel = ss.choose("rotate:a,b", str(song), str(seqdir), CHANNELS,
                        lambda k, d='': d, boom)
        assert sel.name == "a"

    def test_random(self, song, seqdir):
        add_variant(seqdir, "a")
        add_variant(seqdir, "b")
        rng = random.Random(1)
        names = {choose("random:a,b", song, seqdir, rng=rng).name for _ in range(30)}
        assert names == {"a", "b"}

    def test_random_does_not_touch_state(self, song, seqdir):
        add_variant(seqdir, "a")
        state = State()
        choose("random:a", song, seqdir, state)
        assert state.values == {}


class TestHelpers:

    def test_list_variants(self, song, seqdir):
        add_variant(seqdir, "b")
        add_variant(seqdir, "a")
        (seqdir / "Carol Bells" / "notes.txt").write_text("x")
        (seqdir / "Carol Bells" / "fft.csv").write_text("1,2,3\n")  # reserved name
        assert ss.list_variants(str(seqdir), str(song)) == ["a", "b"]

    def test_list_variants_missing_dir(self, song, tmp_path):
        assert ss.list_variants(str(tmp_path / "nope"), str(song)) == []

    def test_paths(self, song, seqdir):
        assert ss.song_stem(str(song)) == "Carol Bells"
        assert ss.legacy_csv_path(str(song)).endswith("music/Carol Bells.csv")
        assert ss.variant_path(str(seqdir), str(song), "a") == str(seqdir / "Carol Bells" / "a.csv")
        assert ss.rotation_key(str(song)) == "seq_rot_carol_bells"

    @pytest.mark.parametrize("override,expected", [
        (None, "/home/x/music/sequences"),
        ("", "/home/x/music/sequences"),
        ("VALUELESS", "/home/x/music/sequences"),
        ("$SYNCHRONIZED_LIGHTS_HOME/seq", "/home/x/seq"),
        ("/srv/seq", "/srv/seq"),
    ])
    def test_config_sequences_dir(self, override, expected):
        import configparser
        import os
        import configuration_manager

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cfg = configuration_manager.Configuration.__new__(configuration_manager.Configuration)
        cfg.home_dir = "/home/x"
        cfg.config = configparser.RawConfigParser(allow_no_value=True)
        cfg.config.read(os.path.join(repo, "config", "defaults.cfg"))
        if override == "VALUELESS":  # a bare "sequences_dir" line
            cfg.config.set("lightshow", "sequences_dir", None)
        elif override is not None:
            cfg.config.set("lightshow", "sequences_dir", override)
        cfg.set_lightshow()
        assert cfg.lightshow.sequences_dir == expected

    @pytest.mark.parametrize("override,expected", [
        (None, True), ("False", False), ("no", False), ("True", True),
    ])
    def test_config_allow_sequence_audition(self, override, expected):
        import configparser
        import os
        import configuration_manager

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cfg = configuration_manager.Configuration.__new__(configuration_manager.Configuration)
        cfg.home_dir = "/home/x"
        cfg.config = configparser.RawConfigParser(allow_no_value=True)
        cfg.config.read(os.path.join(repo, "config", "defaults.cfg"))
        if override is not None:
            cfg.config.set("lightshow", "allow_sequence_audition", override)
        cfg.set_lightshow()
        assert cfg.lightshow.allow_sequence_audition is expected

    def test_config_allow_sequence_audition_missing_key_defaults_on(self):
        import configparser
        import os
        import configuration_manager

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cfg = configuration_manager.Configuration.__new__(configuration_manager.Configuration)
        cfg.home_dir = "/home/x"
        cfg.config = configparser.RawConfigParser(allow_no_value=True)
        cfg.config.read(os.path.join(repo, "config", "defaults.cfg"))
        cfg.config.remove_option("lightshow", "allow_sequence_audition")
        cfg.set_lightshow()
        assert cfg.lightshow.allow_sequence_audition is True


class TestTakeOnce:

    def test_match_returns_variant_and_clears(self, song):
        state = State(sequence_once="Carol Bells|onset_bands")
        assert ss.take_once(str(song), state.get, state.set) == "onset_bands"
        assert state.values["sequence_once"] == ""

    def test_once_value_round_trips(self, song):
        state = State(sequence_once=ss.once_value(str(song), "stems"))
        assert ss.take_once(str(song), state.get, state.set) == "stems"

    def test_stem_containing_separator(self, tmp_path):
        song = tmp_path / "a|b.mp3"
        state = State(sequence_once=ss.once_value(str(song), "v1"))
        assert ss.take_once(str(song), state.get, state.set) == "v1"

    def test_other_song_returns_none_and_clears(self, song):
        state = State(sequence_once="Other|onset_bands")
        assert ss.take_once(str(song), state.get, state.set) is None
        assert state.values["sequence_once"] == ""

    @pytest.mark.parametrize("value", ["Carol Bells|fft", "Carol Bells|../x", "Carol Bells|",
                                       "no separator"])
    def test_bad_value_returns_none_and_clears(self, song, value):
        state = State(sequence_once=value)
        assert ss.take_once(str(song), state.get, state.set) is None
        assert state.values["sequence_once"] == ""

    def test_absent_returns_none_without_writing(self, song):
        state = State()
        writes = []
        assert ss.take_once(str(song), state.get, lambda k, v: writes.append(k)) is None
        assert writes == []

    def test_state_errors_never_raise(self, song):
        def boom(*args):
            raise OSError("locked")
        assert ss.take_once(str(song), boom, boom) is None
        state = State(sequence_once="Carol Bells|a")
        assert ss.take_once(str(song), state.get, boom) == "a"
