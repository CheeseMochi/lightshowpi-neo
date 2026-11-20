"""Tests for the offline composer in research/ (writes the song.csv the Pi plays).

The numpy-only parts (timing, CSV, relay safety, strategies, metrics, preview)
run everywhere; librosa-based analysis is skipped where librosa isn't installed
(CI, the Pis).
"""

import json
import os
import re
import sys
import wave

import numpy as np
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "research"))

from lspcompose import csvio, metrics, preview, safety, strategies, timing  # noqa: E402

pytestmark = pytest.mark.unit
SR = 44100


# ---------------------------------------------------------------------------
# Row timing matches synchronized_lights.setup_audio
# ---------------------------------------------------------------------------

class TestTiming:

    @pytest.mark.parametrize("sr, n", [(44100, 1102), (48000, 1200), (22050, 551)])
    def test_frames_per_row_like_the_pi(self, sr, n):
        assert timing.frames_per_row(sr, 25) == n == int(sr * (25 / 1000))

    def test_row_times_use_real_period_not_25ms(self):
        t = timing.row_times(7201, SR)
        assert t[7200] == pytest.approx(7200 * 1102 / 44100)  # ~179.92 s, not 180.0
        assert 180.0 - t[7200] > 0.07

    def test_row_count_covers_last_partial_chunk(self):
        assert timing.row_count(1102 * 10, SR) == 10
        assert timing.row_count(1102 * 10 + 1, SR) == 11


# ---------------------------------------------------------------------------
# Relay safety
# ---------------------------------------------------------------------------

class TestRelaySafety:

    def test_short_pulse_is_extended_not_delayed(self):
        on = np.zeros(40, bool)
        on[10:12] = True
        out = safety.relay_safety(on, min_on=8, min_off=6)
        assert safety.runs(out) == [(10, 18)]

    def test_short_gap_trims_previous_pulse_to_keep_the_next_edge(self):
        on = np.zeros(60, bool)
        on[0:20] = True
        on[22:40] = True           # 2-row gap
        out = safety.relay_safety(on, min_on=8, min_off=6)
        assert safety.runs(out) == [(0, 16), (22, 40)]

    def test_short_gap_merges_when_trimming_would_make_a_short_pulse(self):
        on = np.zeros(40, bool)
        on[0:9] = True
        on[11:20] = True
        out = safety.relay_safety(on, min_on=8, min_off=6)
        assert safety.runs(out) == [(0, 20)]

    @pytest.mark.parametrize("seed", range(25))
    def test_guarantees_on_random_input(self, seed):
        rng = np.random.default_rng(seed)
        on = rng.random(500) < rng.uniform(0.1, 0.9)
        out = safety.relay_safety(on, min_on=8, min_off=6)
        assert safety.check(on, out, 8, 6) == []


# ---------------------------------------------------------------------------
# CSV format
# ---------------------------------------------------------------------------

class TestCsv:

    def test_headerless_integer_rows_the_pi_can_parse(self, tmp_path):
        path = str(tmp_path / "song.csv")
        csvio.write_csv(np.array([[0, 1, 0.5], [1, 0, 0.2]]), path)
        lines = open(path).read().splitlines()
        assert lines == ["0,255,128", "255,0,51"]
        for line in lines:  # exactly how synchronized_lights.py parses a row
            assert len(list(map(float, line.strip().split(",")))) == 3

    def test_read_rejects_wrong_column_count(self, tmp_path):
        path = str(tmp_path / "song.csv")
        csvio.write_csv(np.ones((3, 8)), path)
        with pytest.raises(ValueError, match="gpio_len"):
            csvio.read_csv(path, channels=7)

    def test_rejects_empty(self, tmp_path):
        with pytest.raises(ValueError):
            csvio.write_csv(np.zeros((0, 7)), str(tmp_path / "x.csv"))


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

def beat_grid(bpm, seconds):
    return np.arange(0.5, seconds, 60.0 / bpm)


def chase(bpm=120, seconds=30, energy=None, layout=None, **opts):
    rows = timing.row_count(int(seconds * SR), SR)
    et = np.linspace(0, seconds, 300)
    edb = energy if energy is not None else np.full(300, -10.0)
    layout = layout or strategies.Layout(7, dead=[1])
    beats = beat_grid(bpm, seconds)
    return strategies.beat_chase(beats, et, edb, rows, SR, layout,
                                 opts=strategies.ChaseOptions(**opts)), beats, et, edb


class TestBeatChase:

    def test_edges_on_beats_and_relay_safe(self):
        levels, beats, et, edb = chase()
        m = metrics.summarize(levels, SR, beats, et, edb)
        assert m["on_beat_fraction"] == 1.0
        assert m["short_on_violations"] == 0 and m["short_off_violations"] == 0
        assert set(np.unique(levels)) <= {0.0, 1.0}

    def test_dead_channel_stays_dark(self):
        levels, *_ = chase()
        assert levels[:, 1].max() == 0

    def test_quiet_passage_is_dark(self):
        energy = np.full(300, -10.0)
        energy[150:] = -80.0           # second half near silence
        levels, *_ = chase(energy=energy)
        half = levels.shape[0] // 2
        assert levels[:half].sum() > 0
        assert levels[half + 80:].sum() == 0   # allow the last loud step to finish

    def test_fast_tempo_steps_every_other_beat(self):
        beats = beat_grid(180, 10)             # 0.333 s beats < min_step 0.4
        assert len(strategies.step_beats(beats, 0.4)) == len(beats[::2])

    def test_louder_lights_more_channels(self):
        energy = np.linspace(-40, -5, 300)
        levels, *_ = chase(energy=energy, seconds=60)
        quarter = levels.shape[0] // 4
        assert levels[-quarter:].sum() > 2 * levels[:quarter].sum()

    def test_downbeat_accent_is_opt_in(self):
        no_accent, *_ = chase(max_lit=3)
        accent, *_ = chase(max_lit=3, beats_per_bar=4, accent_level=0.0)
        active = [0, 2, 3, 4, 5, 6]
        assert (accent[:, active].sum(axis=1) == 6).any()
        assert not (no_accent[:, active].sum(axis=1) == 6).any()

    def test_gaps_in_the_beat_grid_dont_hold_lights(self):
        """librosa drops beats in fade-outs and breaks; no 10 s hold at the end."""
        seconds = 30
        rows = timing.row_count(int(seconds * SR), SR)
        beats = beat_grid(120, 15)                     # no beats in the last 15 s
        levels = strategies.beat_chase(beats, np.linspace(0, seconds, 300), np.full(300, -10.0),
                                       rows, SR, strategies.Layout(7, dead=[1]))
        period = timing.row_period(SR)
        longest = max((e - s) * period for c in range(7) for s, e in safety.runs(levels[:, c] > 0.5))
        assert longest <= 1.5 * 0.5 + 0.05

    def test_bounce_positions(self):
        assert strategies.chase_positions(8, 4, True).tolist() == [0, 1, 2, 3, 2, 1, 0, 1]
        assert strategies.chase_positions(5, 4, False).tolist() == [0, 1, 2, 3, 0]


class TestAlternate:

    def test_odd_even_swap_every_half_second(self):
        layout = strategies.Layout(4)
        rows = timing.row_count(SR * 2, SR)
        levels = strategies.alternate(rows, SR, layout)
        assert levels[0].tolist() == [1, 0, 1, 0]
        assert levels[timing.seconds_to_rows(0.55, SR)].tolist() == [0, 1, 0, 1]


# ---------------------------------------------------------------------------
# Preview page
# ---------------------------------------------------------------------------

def test_preview_embeds_valid_data(tmp_path):
    levels, beats, *_ = chase(seconds=5)
    path = str(tmp_path / "preview.html")
    preview.write_preview(path, levels, SR, "song with 'quote'.mp3", "Song", "sub", beats,
                          {"on_beat_fraction": 1.0, "mean_channel_correlation": float("nan")},
                          dead=[1])
    page = open(path).read()
    data = json.loads(re.search(r"const D = (.*);\n", page).group(1))
    assert len(data["channels"]) == 7 and data["channels"][1]["dead"] is True
    assert data["metrics"]["mean_channel_correlation"] == "n/a"
    assert 'src="song%20with%20%27quote%27.mp3"' in page


# ---------------------------------------------------------------------------
# End to end on a real file (no librosa needed for the alternate pattern)
# ---------------------------------------------------------------------------

def _write_wav(path, seconds=3, sr=SR):
    t = np.arange(int(seconds * sr)) / sr
    pcm = (0.3 * np.sin(2 * np.pi * 440 * t) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.repeat(pcm, 2).tobytes())


def test_compose_alternate_end_to_end(tmp_path):
    pytest.importorskip("audioread")
    import compose
    song = str(tmp_path / "tone.wav")
    _write_wav(song)
    out = str(tmp_path / "out")
    assert compose.main([song, "--channels", "7", "--dead", "2", "--strategy", "alternate",
                         "--out-dir", out]) == 0
    csv_path = os.path.join(out, "tone-alternate", "tone.csv")
    levels = csvio.read_csv(csv_path, channels=7)
    assert levels.shape == (timing.row_count(3 * SR, SR), 7)
    assert levels[:, 1].max() == 0
    assert os.path.exists(os.path.join(out, "tone-alternate", "tone.wav"))
    assert os.path.exists(os.path.join(out, "tone-alternate", "preview.html"))


def test_fft_baseline_runs_the_pis_fft(tmp_path):
    pytest.importorskip("audioread")
    from lspcompose import baseline
    song = str(tmp_path / "tone.wav")
    _write_wav(song)
    levels, sr = baseline.fft_levels(song, 7, 2048, [1, 2, 2, 3, 4, 5, 4])
    assert sr == SR and levels.shape[1] == 7
    on = baseline.on_csv_grid(levels, sr, 2048, timing.row_count(3 * SR, SR), dead=[1])
    assert on[:, 1].max() == 0


def test_librosa_finds_the_tempo_of_a_click_track():
    pytest.importorskip("librosa")
    from lspcompose import audio as audio_mod
    sr, seconds, bpm = 22050, 20, 120
    y = np.zeros(sr * seconds, dtype=np.float32)
    for b in np.arange(0, seconds, 60 / bpm):
        i = int(b * sr)
        y[i:i + 200] = np.hanning(200)
    feats = audio_mod.analyze(audio_mod.Audio(y, sr, y.size, "test"))
    assert feats.tempo == pytest.approx(bpm, rel=0.05)
    assert np.median(np.diff(feats.beat_times)) == pytest.approx(0.5, abs=0.03)


# ---------------------------------------------------------------------------
# More strategies (numpy only)
# ---------------------------------------------------------------------------

class TestMoreStrategies:

    def layout(self):
        return strategies.Layout(7, dead=[1])

    @pytest.mark.parametrize("pattern", sorted(strategies.PATTERNS))
    def test_every_pattern_is_relay_safe_and_on_beat(self, pattern):
        levels, beats, et, edb = chase(pattern=pattern)
        m = metrics.summarize(levels, SR, beats, et, edb)
        assert m["short_on_violations"] == m["short_off_violations"] == 0
        assert m["on_beat_fraction"] == 1.0
        assert levels[:, 1].max() == 0

    def test_vu_is_a_bar_from_the_first_channel(self):
        levels, *_ = chase(pattern="vu")
        active = [0, 2, 3, 4, 5, 6]
        for row in levels[levels.sum(axis=1) > 0][:200]:
            lit = [c for c in active if row[c]]
            assert lit == active[:len(lit)]

    def test_hysteresis_ignores_small_wobbles(self):
        x = np.array([[0.55], [0.45], [0.65], [0.55], [0.45], [0.35], [0.5]])
        assert strategies.hysteresis(x)[:, 0].tolist() == [0, 0, 1, 1, 1, 0, 0]

    def test_fft_safe_removes_flicker(self):
        rows = timing.row_count(SR * 10, SR)
        flicker = (np.arange(rows)[:, None] % 2).astype(float) * np.ones((1, 3))  # 40 Hz
        safe = strategies.fft_safe(flicker, SR)
        for c in range(3):
            assert safety.check(flicker[:, c] > 0.5, safe[:, c] > 0.5,
                                timing.seconds_to_rows(0.2, SR), timing.seconds_to_rows(0.15, SR)) == []

    def test_band_flux_groups_low_to_high(self):
        mel = np.zeros((64, 10))
        mel[60:, 5] = 10.0                     # a hit in the top band only
        flux = strategies.band_flux(mel, 4)
        assert flux.shape == (4, 10) and flux[3, 5] > 0 and flux[:3].sum() == 0

    def test_onset_bands_fires_the_right_channel_on_hits(self):
        seconds = 20
        rows = timing.row_count(SR * seconds, SR)
        hop = 512 / SR
        mel_t = np.arange(int(seconds / hop)) * hop
        mel = np.random.default_rng(0).normal(-60, 0.5, (64, mel_t.size))
        beats = beat_grid(120, seconds)
        for b in beats:                        # kick-like hits in the lowest band on each beat
            mel[:10, int(b / hop)] += 30
        levels = strategies.onset_bands(mel_t, mel, beats, np.linspace(0, seconds, 100),
                                        np.full(100, -10.0), rows, SR, self.layout())
        assert levels[:, 0].sum() > 0          # lowest active channel fires
        assert levels[:, 1].max() == 0         # dead
        m = metrics.summarize(levels, SR, beats, np.linspace(0, seconds, 100), np.full(100, -10.0))
        assert m["on_beat_fraction"] > 0.9

    def test_sections_repeat_the_same_look(self):
        seconds = 60
        rows = timing.row_count(SR * seconds, SR)
        beats = beat_grid(120, seconds)
        et = np.linspace(0, seconds, 600)
        edb = np.where((et // 15) % 2 == 0, -30.0, -8.0)   # quiet, loud, quiet, loud
        seg_t = np.array([0.0, 15.0, 30.0, 45.0])
        seg_l = np.array([0, 1, 0, 1])                       # A B A B
        levels = strategies.sections(seg_t, seg_l, beats, et, edb, rows, SR, self.layout())
        t = timing.row_times(rows, SR)
        a1 = levels[(t >= 2) & (t < 13)]
        a2 = levels[(t >= 32) & (t < 43)]
        assert np.array_equal(a1.mean(axis=0) > 0, a2.mean(axis=0) > 0)
        assert levels[(t >= 15) & (t < 28)].mean() > levels[(t >= 2) & (t < 13)].mean()


# ---------------------------------------------------------------------------
# Test bed (runs on the Pi; tested here with fakes)
# ---------------------------------------------------------------------------

import testbed  # noqa: E402

MANIFEST = {"channels": 7, "timing_ms": 25, "brightness_range": 255, "clip_start_s": 14.0,
            "clip_seconds": 30.0, "tempo_bpm": 120,
            "variants": [{"name": "fft", "title": "Today", "description": "d"},
                         {"name": "beat_chase", "title": "Chase", "description": "d"}]}


@pytest.fixture
def out_dir(tmp_path):
    d = tmp_path / "out" / "song"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(MANIFEST))
    (tmp_path / "out" / ".cache").mkdir()
    return str(tmp_path / "out")


class TestTestbed:

    def test_find_bundles(self, out_dir):
        assert [b[0] for b in testbed.find_bundles(out_dir)] == ["song"]

    def test_layout_problems(self):
        assert testbed.layout_problems(MANIFEST, (7, 25, 255)) == []
        problems = testbed.layout_problems(MANIFEST, (8, 50, 255))
        assert any("--channels 8" in p for p in problems) and len(problems) == 2

    def test_testbed_config_turns_off_pre_and_postshow(self, tmp_path):
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "overrides.cfg").write_text(
            "[hardware]\ngpio_pins = 0,1,2\n[lightshow]\nlight_delay = 0.04\n"
            "postshow_script = $SYNCHRONIZED_LIGHTS_HOME/py/examples/random_pattern_ci.py\n")
        name = testbed.write_testbed_config(str(tmp_path))
        import configparser
        cfg = configparser.RawConfigParser()
        cfg.read(tmp_path / "config" / name)
        assert cfg.get("lightshow", "postshow_script") == ""
        assert cfg.get("lightshow", "preshow_configuration") == ""
        assert cfg.get("lightshow", "light_delay") == "0.04"
        assert cfg.get("hardware", "gpio_pins") == "0,1,2"
        assert cfg.get("lightshow", "always_off_channels") == "-1"
        testbed.write_testbed_config(str(tmp_path), keep_overrides=True)
        cfg = configparser.RawConfigParser()
        cfg.read(tmp_path / "config" / name)
        assert not cfg.has_option("lightshow", "always_off_channels")

    def test_early_exit_with_code_0_is_a_failure(self, tmp_path, capsys):
        class Proc:
            def wait(self, timeout=None):
                return 0

        times = iter([0.0, 0.4])
        result = testbed.play("a.wav", "A", runner=lambda cmd, **kw: Proc(),
                              log_path=str(tmp_path / "t.log"), expected_s=30,
                              clock=lambda: next(times))
        assert result == "failed"
        assert "ended after 0.4 s" in capsys.readouterr().out

    def test_menu_refuses_a_bundle_that_doesnt_fit(self, out_dir, capsys):
        played = []
        testbed.menu(out_dir, testbed.find_bundles(out_dir), (8, 25, 255),
                     input_fn=lambda prompt: "q", play_fn=lambda *a: played.append(a))
        assert played == [] and "--channels 8" in capsys.readouterr().out

    def test_show_command(self):
        cmd = testbed.show_command("/x/clip.wav", python="/py", repo="/r", sudo=True)
        assert cmd == ["sudo", "-E", "/py", "/r/py/synchronized_lights.py", "--file=/x/clip.wav",
                       "--config=testbed.cfg"]

    def test_play_success_and_failure(self, tmp_path):
        class Proc:
            def __init__(self, code, **_):
                self.code = code

            def wait(self, timeout=None):
                return self.code

        log = str(tmp_path / "logs" / "t.log")
        assert testbed.play("a.wav", "A", runner=lambda cmd, **kw: Proc(0), log_path=log) == "done"
        assert testbed.play("a.wav", "A", runner=lambda cmd, **kw: Proc(2), log_path=log) == "failed"

    def test_play_ctrl_c_stops(self, tmp_path):
        class Proc:
            calls = 0

            def wait(self, timeout=None):
                Proc.calls += 1
                if Proc.calls == 1:
                    raise KeyboardInterrupt
                return 0

        assert testbed.play("a.wav", "A", runner=lambda cmd, **kw: Proc(),
                            log_path=str(tmp_path / "t.log")) == "stopped"

    def test_sequence_order_paths_and_stop(self, out_dir, monkeypatch):
        monkeypatch.setattr(testbed.time, "sleep", lambda s: None)
        played = []
        result = testbed.play_sequence(out_dir, "song", MANIFEST, "clip", ["fft", "beat_chase"],
                                       repeat=2, play_fn=lambda p, t, e: played.append(p) or "done")
        assert result == "done" and len(played) == 4
        assert played[1].endswith(os.path.join("song", "clip", "beat_chase", "clip.wav"))
        played.clear()
        result = testbed.play_sequence(out_dir, "song", MANIFEST, "full", ["fft", "beat_chase"],
                                       play_fn=lambda p, t, e: played.append(p) or "stopped")
        assert result == "stopped" and len(played) == 1

    def test_menu_script(self, out_dir, monkeypatch):
        monkeypatch.setattr(testbed.time, "sleep", lambda s: None)
        answers = iter(["2", "m", "1", "a", "i 2", "zzz", "q"])
        played = []
        testbed.menu(out_dir, testbed.find_bundles(out_dir), (7, 25, 255),
                     input_fn=lambda prompt: next(answers),
                     play_fn=lambda p, t, e: played.append(p) or "done")
        assert [os.path.relpath(p, out_dir) for p in played] == [
            os.path.join("song", "clip", "beat_chase", "clip.wav"),
            os.path.join("song", "full", "fft", "full.wav"),
            os.path.join("song", "full", "fft", "full.wav"),
            os.path.join("song", "full", "beat_chase", "full.wav")]


# ---------------------------------------------------------------------------
# Bundle end to end (librosa)
# ---------------------------------------------------------------------------

def test_bundle_end_to_end(tmp_path):
    pytest.importorskip("librosa")
    import bundle
    sr, seconds, bpm = SR, 40, 120
    t = np.arange(sr * seconds) / sr
    y = 0.05 * np.sin(2 * np.pi * 220 * t)
    for b in np.arange(0, seconds, 60 / bpm):
        i = int(b * sr)
        y[i:i + 600] += 0.6 * np.hanning(600)
    pcm = (np.clip(y, -1, 1) * 32767).astype("<i2")
    song = str(tmp_path / "click.wav")
    with wave.open(song, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.repeat(pcm, 2).tobytes())
    out = str(tmp_path / "out")
    assert bundle.main([song, "--channels", "7", "--dead", "2", "--clip-seconds", "10",
                        "--no-stems", "--out-dir", out]) == 0
    man = json.load(open(os.path.join(out, "click", "manifest.json")))
    from lspcompose.variants import VARIANTS
    assert [v["name"] for v in man["variants"]] == [v.name for v in VARIANTS if not v.needs_stems]
    full_rows = timing.row_count(sr * seconds, sr)
    for v in man["variants"]:
        full = csvio.read_csv(os.path.join(out, "click", "full", v["name"], "full.csv"), channels=7)
        clip = csvio.read_csv(os.path.join(out, "click", "clip", v["name"], "clip.csv"), channels=7)
        assert full.shape[0] == full_rows
        k = int(round(man["clip_start_s"] / timing.row_period(sr)))
        assert np.array_equal(clip, full[k:k + clip.shape[0]])      # clip rows == full rows
        assert os.path.islink(os.path.join(out, "click", "clip", v["name"], "clip.wav"))
    with wave.open(os.path.join(out, "click", "audio", "clip.wav")) as w:
        frames = w.getnframes()
    assert frames == clip.shape[0] * timing.frames_per_row(sr)
    assert testbed.find_bundles(out)[0][0] == "click"


# ---------------------------------------------------------------------------
# Drop cut and dynamic onset_bands
# ---------------------------------------------------------------------------

class TestDrops:

    def test_drop_mask_marks_sustained_drops_from_their_start(self):
        hop = 0.01
        env = np.full(1000, -10.0)
        env[500:] = -30.0                      # sudden 20 dB drop at 5.00 s
        env[200:205] = -30.0                   # a 50 ms dip: not sustained
        mask = strategies.drop_mask(env, hop, drop_db=10, sustain_s=0.15, window_s=3.0)
        assert mask[500] and mask[650]         # dark from the very first frame of the drop
        assert not mask[499] and not mask[202]

    def test_drop_mask_ends_when_the_quiet_part_becomes_the_new_normal(self):
        hop = 0.01
        env = np.full(1000, -10.0)
        env[300:] = -30.0
        mask = strategies.drop_mask(env, hop, 10, 0.15, window_s=2.0)
        assert mask[300:499].all() and not mask[600:].any()

    def test_cut_never_leaves_short_pulses(self):
        levels = np.zeros((100, 1))
        levels[10:40, 0] = 1                   # 30-row pulse
        dark = np.zeros(100, bool)
        dark[13:] = True                       # cut after 3 rows
        out = strategies.cut(levels, dark, min_on_rows=8)
        assert out.sum() == 0                  # the 3-row stub is removed, not kept
        dark[:] = False
        dark[30:] = True
        out = strategies.cut(levels, dark, min_on_rows=8)
        assert safety.runs(out[:, 0] > 0.5) == [(10, 30)]

    def test_cut_inside_a_long_pulse_keeps_min_off(self):
        levels = np.zeros((100, 1))
        levels[10:60, 0] = 1                   # one long pulse
        dark = np.zeros(100, bool)
        dark[30:33] = True                     # a 3-row dark span inside it
        out = strategies.cut(levels, dark, min_on_rows=8, min_off_rows=6)
        assert safety.runs(out[:, 0] > 0.5) == [(10, 30)]   # the rest stays dark, no 3-row gap
        assert safety.check(levels[:, 0] > 0.5, out[:, 0] > 0.5, 8, 6) == []

    @pytest.mark.parametrize("seed", range(20))
    def test_cut_keeps_relay_limits_on_random_input(self, seed):
        rng = np.random.default_rng(seed)
        on = safety.relay_safety(rng.random(400) < 0.5, 8, 6)
        dark = rng.random(400) < 0.05
        out = strategies.cut(on[:, None].astype(float), dark, 8, 6)[:, 0] > 0.5
        for i, (start, end) in enumerate(safety.runs(out)):
            assert end - start >= 8 or end == 400
            if i:
                assert start - safety.runs(out)[i - 1][1] >= 6

    def _song(self, drop_at=None, seconds=30):
        rows = timing.row_count(SR * seconds, SR)
        hop = 512 / SR
        mel_t = np.arange(int(seconds / hop)) * hop
        rng = np.random.default_rng(1)
        mel = rng.normal(-40, 0.5, (64, mel_t.size))
        beats = beat_grid(120, seconds)
        for b in beats:
            for band in range(0, 64, 11):      # hits in every band on each beat
                mel[band:band + 8, int(b / hop)] += 25
        if drop_at is not None:
            mel[:, mel_t >= drop_at] -= 25     # sustained 25 dB drop
        et = np.linspace(0, seconds, 300)
        edb = np.where(et >= (drop_at or 1e9), -35.0, -10.0)
        return mel_t, mel, beats, et, edb, rows

    def test_dynamic_onset_goes_dark_the_moment_the_music_drops(self):
        mel_t, mel, beats, et, edb, rows = self._song(drop_at=15.0)
        opts = strategies.OnsetOptions(dynamic=True, drop_db=10.0)
        levels = strategies.onset_bands(mel_t, mel, beats, et, edb, rows, SR,
                                        strategies.Layout(7), opts=opts)
        t = timing.row_times(rows, SR)
        assert levels[(t < 14.5)].sum() > 0
        assert levels[(t >= 14.55) & (t < 14.75)].sum() > 0   # not early: the last beat before plays
        assert levels[(t >= 15.05) & (t < 17.0)].sum() == 0   # dark within ~50 ms, stays dark

    def test_without_drop_cut_lights_linger_after_the_drop(self):
        mel_t, mel, beats, et, edb, rows = self._song(drop_at=15.0)
        levels = strategies.onset_bands(mel_t, mel, beats, et, edb, rows, SR, strategies.Layout(7))
        t = timing.row_times(rows, SR)
        assert levels[(t >= 15.0) & (t < 15.4)].sum() > 0     # the last pulse rings on

    def test_dynamic_density_follows_loudness(self):
        mel_t, mel, beats, et, _, rows = self._song()
        edb = np.where(et < 15, -30.0, -5.0)                  # quiet half, loud half
        opts = strategies.OnsetOptions(dynamic=True)
        levels = strategies.onset_bands(mel_t, mel, beats, et, edb, rows, SR,
                                        strategies.Layout(7), opts=opts)
        t = timing.row_times(rows, SR)
        assert levels[t >= 16].sum() > 2 * levels[(t > 1) & (t < 14)].sum()

    def test_every_onset_variant_is_relay_safe(self):
        mel_t, mel, beats, et, edb, rows = self._song(drop_at=20.0)
        for kw in ({}, {"dynamic": True, "drop_db": 10.0}, {"hold_beats": 1.0, "drop_db": 6.0},
                   {"threshold": 2.0, "floor": 0.08, "dynamic": True, "drop_db": 6.0}):
            levels = strategies.onset_bands(mel_t, mel, beats, et, edb, rows, SR,
                                            strategies.Layout(7), opts=strategies.OnsetOptions(**kw))
            m = metrics.summarize(levels, SR, beats, et, edb)
            assert m["short_on_violations"] == m["short_off_violations"] == 0, kw


# ---------------------------------------------------------------------------
# Stems (synthetic stem features; no Demucs needed)
# ---------------------------------------------------------------------------

def _stem_features(seconds=20, silent=("piano",)):
    from lspcompose import stems
    hop = 512 / SR
    times = np.arange(int(seconds / hop)) * hop
    freqs = np.geomspace(30, 15000, stems.N_MELS)
    rng = np.random.default_rng(2)
    mel = {}
    beats = beat_grid(120, seconds)
    for name in ("drums", "bass", "other", "vocals", "guitar", "piano"):
        m = rng.normal(-70, 0.5, (stems.N_MELS, times.size))
        if name not in silent:
            if name == "drums":
                for b in beats:                                 # kick on beats, hats off-beat
                    m[freqs < 150, int(b / hop)] += 40
                    m[freqs > 2000, int((b + 0.25) / hop)] += 40
            else:
                for b in beats[::2]:                            # a note every 2 beats, held 0.6 s
                    m[(freqs > 100) & (freqs < 2000), int(b / hop):int((b + 0.6) / hop)] += 40
        mel[name] = m
    return stems.StemFeatures(times, freqs, mel), beats, times


class TestStems:

    def test_absent_instruments_are_skipped_and_spares_split(self):
        from lspcompose import stems
        feats, *_ = _stem_features()
        present = stems.present_voices(feats)
        assert "piano" not in present and "kick" in present and "snare_hats" in present
        assign = stems.assign_voices(feats, strategies.Layout(9))
        assert len(assign) == 9
        assert any("@" in v for v in assign.values())          # spare lights split a voice
        labels = [stems.voice_spec(v)[4] for v in assign.values()]
        assert "kick drum" in labels and any(x.endswith(" low") for x in labels)

    def test_each_light_follows_its_instrument(self):
        from lspcompose import stems
        feats, beats, _ = _stem_features()
        rows = timing.row_count(SR * 20, SR)
        layout = strategies.Layout(7)
        levels, assign = stems.stems_show(feats, np.zeros((64, 2)), np.array([0.0]), beats, rows,
                                          SR, layout, drop_db=None)
        t = timing.row_times(rows, SR)
        kick = [c for c, v in assign.items() if v == "kick"][0]
        on_edges = [t[s] for s, _ in safety.runs(levels[:, kick] > 0.5)]
        assert len(on_edges) > 20
        assert all(np.min(np.abs(beats - e)) < 0.05 for e in on_edges)   # kick light on the kicks
        tone = [c for c, v in assign.items() if stems.voice_spec(v)[3] == "tone"][0]
        assert levels[:, tone].sum() > 0
        m = metrics.summarize(levels, SR, beats, np.array([0, 20.0]), np.array([-10.0, -10.0]))
        assert m["short_on_violations"] == m["short_off_violations"] == 0

    def test_voice_spec_for_split_names(self):
        from lspcompose import stems
        assert stems.voice_spec("guitar@0-420") == ("guitar", 0.0, 420.0, "tone", "guitar low")
        assert stems.voice_spec("kick")[4] == "kick drum"


# ---------------------------------------------------------------------------
# Scale sweeps and restart flashes (onset_runs)
# ---------------------------------------------------------------------------

class TestRuns:

    def test_finds_fast_rising_and_falling_scales(self):
        t = np.arange(8) * 0.12
        up = strategies.find_runs(t, 60 + np.array([0, 2, 4, 5, 7, 9, 11, 12]))
        down = strategies.find_runs(t, 72 - np.array([0, 2, 4, 5, 7, 9, 11, 12]))
        assert len(up) == 1 and up[0].direction == 1 and len(up[0].times) == 8
        assert len(down) == 1 and down[0].direction == -1

    def test_repeating_figure_and_slow_or_small_moves_are_not_runs(self):
        t = np.arange(16) * 0.15
        carol = np.tile([70, 69, 70, 67], 4)               # Carol of the Bells ostinato
        assert strategies.find_runs(t, carol) == []
        assert strategies.find_runs(np.arange(8) * 0.6, 60 + np.arange(8) * 2) == []   # too slow
        assert strategies.find_runs(np.arange(8) * 0.1, [60, 61, 61, 62, 62, 61, 60, 61]) == []
        assert strategies.find_runs(np.arange(6) * 0.1, [60, 61, 62, 63, 64, 65]) == []  # 5 semitones
        # a held note then a scale is still a scale
        assert len(strategies.find_runs(np.arange(8) * 0.1, [60, 60, 60, 62, 64, 65, 67, 69])) == 1

    def test_octave_jumps_are_folded(self):
        t = np.arange(7) * 0.12
        pitch = np.array([60, 62, 64 + 12, 65, 67, 69 - 12, 71])   # tracker octave errors
        runs = strategies.find_runs(t, pitch)
        assert len(runs) == 1 and runs[0].pitch[-1] - runs[0].pitch[0] == 11

    def test_sweep_fills_end_to_end_one_switch_per_light(self):
        t_notes = 1.0 + np.arange(7) * 0.1
        run = strategies.find_runs(t_notes, 60 + np.arange(7) * 2)[0]
        rows = timing.row_count(SR * 3, SR)
        times = timing.row_times(rows, SR)
        out = np.zeros((rows, 7))
        order = [6, 5, 4, 3, 2, 1, 0]                      # display runs right to left
        strategies.sweep(run, order, times, out, hold=0.25)
        first_on = [times[np.argmax(out[:, c] > 0)] for c in order]
        assert first_on == sorted(first_on)               # lights come on in display order
        for c in range(7):
            assert len(safety.runs(out[:, c] > 0.5)) == 1  # each relay switches once
        assert out[times > run.end + 0.3].sum() == 0

    def test_onset_runs_flashes_when_the_music_returns(self):
        from types import SimpleNamespace
        mel_t, mel, beats, et, edb, rows = TestDrops()._song(drop_at=15.0)
        mel[:, (mel_t >= 18.0)] += 25                       # music crashes back in at 18 s
        feats = SimpleNamespace(mel_times=mel_t, mel_db=mel, beat_times=beats,
                                energy_times=et, energy_db=edb, onset_times=beats,
                                note_times=np.array([]), note_pitch=np.array([]))
        levels, n = strategies.onset_runs(feats, rows, SR, strategies.Layout(7))
        t = timing.row_times(rows, SR)
        assert n == 0
        assert levels[(t >= 15.05) & (t < 17.9)].sum() == 0          # stop: dark
        back = levels[(t >= 18.0) & (t < 18.25)]
        assert (back.sum(axis=1) == 7).any()                          # start: everything flashes
        m = metrics.summarize(levels, SR, beats, et, edb)
        assert m["short_on_violations"] == m["short_off_violations"] == 0
