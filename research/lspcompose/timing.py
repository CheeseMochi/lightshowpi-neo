"""
CSV row timing, copied from how the Pi plays a sequence.

synchronized_lights.py (setup_audio) reads one CSV row per audio chunk of
``int(sample_rate * (timing / 1000))`` frames, where ``sample_rate`` is the
file's native rate (audioread) and ``timing`` is ``[custom_sequences] timing``
in ms (default 25). At 44.1 kHz that is 1102 frames = 24.989 ms, not 25 ms, so
row ``i`` starts at ``i * N / sr`` seconds. Composers must use these helpers
rather than assuming exact 25 ms rows (it drifts ~80 ms over 3 minutes).
"""

import math

import numpy as np


def frames_per_row(sample_rate: int, timing_ms: int = 25) -> int:
    """Audio frames per CSV row, exactly as the Pi computes it."""
    return int(sample_rate * (timing_ms / 1000))


def row_count(total_frames: int, sample_rate: int, timing_ms: int = 25) -> int:
    """Rows needed to cover the song (the Pi reads one per chunk, the last one partial)."""
    n = frames_per_row(sample_rate, timing_ms)
    return max(1, math.ceil(total_frames / n))


def row_times(rows: int, sample_rate: int, timing_ms: int = 25) -> np.ndarray:
    """Start time in seconds of each CSV row."""
    n = frames_per_row(sample_rate, timing_ms)
    return np.arange(rows) * (n / sample_rate)


def row_period(sample_rate: int, timing_ms: int = 25) -> float:
    """Seconds per CSV row (24.989 ms at 44.1 kHz and 25 ms timing)."""
    return frames_per_row(sample_rate, timing_ms) / sample_rate


def seconds_to_rows(seconds: float, sample_rate: int, timing_ms: int = 25) -> int:
    """A duration in whole rows, rounded up, at least 1."""
    return max(1, math.ceil(seconds / row_period(sample_rate, timing_ms) - 1e-9))
