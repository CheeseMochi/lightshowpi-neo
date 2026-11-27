"""
Today's FFT show for the same song, for comparing metrics.

Runs the Pi's own py/fft.py on the same decoded audio with the production
settings (chunk_size, channel mapping, SD_low/SD_high), normalises with the
cached mean/std (what a second play uses) and thresholds at 0.5 like a relay.
The result is resampled onto the CSV row grid so metrics compare like for like.
"""

import os
import sys
from typing import Optional, Sequence

import numpy as np

from .timing import row_times

_PY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "py")


def _pcm16(path: str):
    """Interleaved int16 PCM, sample rate and channel count (audioread, else soundfile)."""
    try:
        import audioread
        with audioread.audio_open(path) as f:
            return np.frombuffer(b"".join(f.read_data()), dtype="<i2"), f.samplerate, f.channels
    except Exception:
        import soundfile
        data, sr = soundfile.read(path, dtype="int16", always_2d=True)
        return data.reshape(-1), sr, data.shape[1]


def fft_levels(path: str, channels: int, chunk_size: int = 2048,
               mapping: Optional[Sequence[int]] = None, min_frequency: float = 20,
               max_frequency: float = 15000, sd_low: float = 0.5, sd_high: float = 0.75):
    """Per-chunk brightness (0..1) as the FFT show computes it, plus the sample rate."""
    if _PY_DIR not in sys.path:
        sys.path.insert(0, _PY_DIR)
    import fft

    pcm, sr, nch = _pcm16(path)
    calc = fft.FFT(chunk_size, sr, channels, min_frequency, max_frequency,
                   list(mapping) if mapping else 0, 0, nch, False)
    step = chunk_size * nch
    matrix = np.array([calc.calculate_levels(pcm[i:i + step].tobytes())
                       for i in range(0, len(pcm), step)])
    mean = np.array([np.mean(c[c > 0]) if (c > 0).any() else 0 for c in matrix.T])
    std = np.array([np.std(c[c > 0]) if (c > 0).any() else 1 for c in matrix.T])
    b = (matrix - mean + std * sd_low) / (std * (sd_low + sd_high))
    return np.nan_to_num(np.clip(b, 0, 1)), sr


def on_csv_grid(levels: np.ndarray, sample_rate: int, chunk_size: int, rows: int,
                timing_ms: int = 25, dead: Sequence[int] = ()) -> np.ndarray:
    """Relay view (0/1) of chunk levels, sampled at each CSV row; dead columns off."""
    chunk_idx = np.minimum((row_times(rows, sample_rate, timing_ms) * sample_rate // chunk_size)
                           .astype(int), len(levels) - 1)
    out = (levels[chunk_idx] > 0.5).astype(float)
    for c in dead:
        out[:, c] = 0.0
    return out


def levels_on_csv_grid(levels: np.ndarray, sample_rate: int, chunk_size: int, rows: int,
                       timing_ms: int = 25, dead: Sequence[int] = ()) -> np.ndarray:
    """Continuous FFT brightness (0..1) sampled at each CSV row; dead columns off."""
    chunk_idx = np.minimum((row_times(rows, sample_rate, timing_ms) * sample_rate // chunk_size)
                           .astype(int), len(levels) - 1)
    out = np.array(levels[chunk_idx], dtype=float)
    for c in dead:
        out[:, c] = 0.0
    return out
