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
