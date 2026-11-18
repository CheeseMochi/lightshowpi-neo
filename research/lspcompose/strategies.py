"""
Composition strategies: features in, [rows, channels] levels (0..1) out.

Inputs are plain numpy (beat times, an energy curve), so these run and test
without librosa. Every relay strategy ends with safety.apply().
"""

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from . import safety
from .timing import row_times, seconds_to_rows


@dataclass
class Layout:
    """The columns of the CSV and which of them a strategy may light."""
    channels: int                      # = gpio_len on the Pi (CSV column count)
    dead: List[int] = field(default_factory=list)  # 0-based columns left dark (e.g. always_off)

    @property
    def active(self) -> List[int]:
        return [c for c in range(self.channels) if c not in set(self.dead)]


@dataclass
class ChaseOptions:
    """beat_chase knobs (seconds unless noted)."""
    min_on: float = 0.20               # mechanical relays: shortest ON pulse
    min_off: float = 0.15              # shortest OFF gap
    min_step: float = 0.40             # shortest step (>= min_on + min_off); faster beats skip
    gap_fraction: float = 0.25         # OFF part of each step, as a fraction of the step
    quiet_db: float = 35.0             # dark when this far below the song's loud level
    max_lit: Optional[int] = None      # most channels lit at once (default: all active)
    beats_per_bar: int = 0             # >0: full flash on loud downbeats (assumes beat 0 is one)
    accent_level: float = 0.85         # loudness rank needed for a downbeat flash
    bounce: bool = True                # reverse direction at the ends instead of wrapping


def loudness_rank(energy_db: np.ndarray) -> np.ndarray:
    """Song-global rank (0..1) of each value: 0.9 = louder than 90% of the song."""
    energy_db = np.asarray(energy_db, dtype=float)
    if energy_db.size == 0:
        return energy_db
    order = np.argsort(np.argsort(energy_db, kind="stable"), kind="stable")
    return order / max(1, energy_db.size - 1)


def step_beats(beat_times: np.ndarray, min_step: float) -> np.ndarray:
    """Every beat, or every 2nd/3rd... when beats come faster than ``min_step``."""
    beat_times = np.asarray(beat_times, dtype=float)
    if beat_times.size < 2:
        return beat_times
    period = float(np.median(np.diff(beat_times)))
    every = max(1, int(np.ceil(min_step / period - 1e-9))) if period > 0 else 1
    return beat_times[::every]


def chase_positions(steps: int, group: int, bounce: bool) -> np.ndarray:
    """Head position for each step: 0,1,..,G-1,G-2,..,1,0,1.. (bounce) or wrapping."""
    if group <= 1:
        return np.zeros(steps, dtype=int)
    if not bounce:
        return np.arange(steps) % group
    cycle = list(range(group)) + list(range(group - 2, 0, -1))
    return np.array([cycle[i % len(cycle)] for i in range(steps)], dtype=int)


def beat_chase(beat_times, energy_times, energy_db, rows: int, sample_rate: int,
               layout: Layout, timing_ms: int = 25,
               opts: Optional[ChaseOptions] = None) -> np.ndarray:
    """
    Beat-gated chase: on each step a group of k adjacent channels lights, k
    growing with loudness, moving one place per step. Quiet passages are dark.

    Args:
        beat_times: beat onsets in seconds (song time, as the Pi plays it)
        energy_times, energy_db: a loudness curve (dB) sampled at energy_times
        rows: CSV rows (timing.row_count)
        sample_rate: the file's native rate (row timing)
        layout: column count and dead columns

    Returns:
        [rows, layout.channels] array of 0.0 / 1.0
    """
    opts = opts or ChaseOptions()
    times = row_times(rows, sample_rate, timing_ms)
    out = np.zeros((rows, layout.channels))
    active = layout.active
    if not active or rows == 0:
        return out
    group = len(active)
    max_lit = min(opts.max_lit or group, group)

    steps = step_beats(beat_times, opts.min_step)
    steps = steps[(steps >= 0) & (steps < times[-1] + 1e-9)]
    if steps.size == 0:
        return out

    energy_db = np.asarray(energy_db, dtype=float)
    loud_db = float(np.percentile(energy_db, 95)) if energy_db.size else 0.0
    level_at = np.interp(steps, energy_times, loudness_rank(energy_db)) if energy_db.size else \
        np.ones_like(steps)
    db_at = np.interp(steps, energy_times, energy_db) if energy_db.size else \
        np.full_like(steps, loud_db)

    heads = chase_positions(steps.size, group, opts.bounce)
    beat_index = np.searchsorted(np.asarray(beat_times, dtype=float), steps)
    ends = np.append(steps[1:], times[-1] + (times[1] - times[0] if rows > 1 else 0.025))

    for i, (start, end) in enumerate(zip(steps, ends)):
        if db_at[i] < loud_db - opts.quiet_db:
            continue  # quiet = dark
        step_len = end - start
        on_len = max(opts.min_on, step_len * (1 - opts.gap_fraction))
        r0 = int(np.searchsorted(times, start - 1e-9))
        r1 = min(rows, r0 + seconds_to_rows(on_len, sample_rate, timing_ms))
        accent = (opts.beats_per_bar > 0 and beat_index[i] % opts.beats_per_bar == 0
                  and level_at[i] >= opts.accent_level)
        if accent:
            lit = active
        else:
            k = 1 + int(round(level_at[i] * (max_lit - 1)))
            lit = [active[(heads[i] + j) % group] for j in range(k)]
        out[r0:r1, lit] = 1.0

    min_on = seconds_to_rows(opts.min_on, sample_rate, timing_ms)
    min_off = seconds_to_rows(opts.min_off, sample_rate, timing_ms)
    return safety.apply(out, min_on, min_off)


def alternate(rows: int, sample_rate: int, layout: Layout, period: float = 0.5,
              timing_ms: int = 25) -> np.ndarray:
    """Step-0 test pattern: odd/even active channels swap every ``period`` seconds."""
    times = row_times(rows, sample_rate, timing_ms)
    phase = (np.floor(times / period).astype(int) % 2)[:, None]
    out = np.zeros((rows, layout.channels))
    for n, c in enumerate(layout.active):
        out[:, c] = (phase[:, 0] == n % 2)
    return out
