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
    pattern: str = "chase"             # chase | vu | ab | all | sparse (see PATTERNS)
    seed: int = 0                      # sparse pattern's random choice


PATTERNS = {
    "chase": "a loudness-sized group of adjacent channels moves one place per beat",
    "vu": "a bar graph from channel 1 up: more channels lit when louder",
    "ab": "odd and even channels alternate on each beat",
    "all": "every channel pulses on each beat",
    "sparse": "one random channel per beat",
}


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
    """beat_steps() for the chosen pattern, then the relay-safety pass."""
    opts = opts or ChaseOptions()
    raw = beat_steps(beat_times, energy_times, energy_db, rows, sample_rate, layout,
                     timing_ms, opts)
    return relay_safe(raw, sample_rate, timing_ms, opts.min_on, opts.min_off)


def relay_safe(levels, sample_rate, timing_ms, min_on_s, min_off_s):
    """safety.apply with limits given in seconds."""
    return safety.apply(levels, seconds_to_rows(min_on_s, sample_rate, timing_ms),
                        seconds_to_rows(min_off_s, sample_rate, timing_ms))


def beat_steps(beat_times, energy_times, energy_db, rows: int, sample_rate: int,
               layout: Layout, timing_ms: int = 25,
               opts: Optional[ChaseOptions] = None) -> np.ndarray:
    """
    Beat-gated patterns before the safety pass (see PATTERNS). Chase: on each
    step a group of k adjacent channels lights, k growing with loudness, moving
    one place per step. Quiet passages are dark.

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

    # Cap each step at 1.5 median steps: librosa drops weak beats (intros,
    # fade-outs, breaks), and the last step would otherwise hold to the end
    median_step = float(np.median(np.diff(steps))) if steps.size > 1 else 0.5
    heads = chase_positions(steps.size, group, opts.bounce)
    rng = np.random.default_rng(opts.seed)
    beat_index = np.searchsorted(np.asarray(beat_times, dtype=float), steps)
    ends = np.append(steps[1:], times[-1] + (times[1] - times[0] if rows > 1 else 0.025))

    for i, (start, end) in enumerate(zip(steps, ends)):
        if db_at[i] < loud_db - opts.quiet_db:
            continue  # quiet = dark
        step_len = min(end - start, 1.5 * median_step)
        on_len = max(opts.min_on, step_len * (1 - opts.gap_fraction))
        r0 = int(np.searchsorted(times, start - 1e-9))
        r1 = min(rows, r0 + seconds_to_rows(on_len, sample_rate, timing_ms))
        accent = (opts.beats_per_bar > 0 and beat_index[i] % opts.beats_per_bar == 0
                  and level_at[i] >= opts.accent_level)
        k = 1 + int(round(level_at[i] * (max_lit - 1)))
        if accent or opts.pattern == "all":
            lit = active
        elif opts.pattern == "vu":
            lit = active[:k]
        elif opts.pattern == "ab":
            lit = active[i % 2::2]
        elif opts.pattern == "sparse":
            lit = [active[int(rng.integers(group))]]
        else:
            lit = [active[(heads[i] + j) % group] for j in range(k)]
        out[r0:r1, lit] = 1.0
    return out


def alternate(rows: int, sample_rate: int, layout: Layout, period: float = 0.5,
              timing_ms: int = 25) -> np.ndarray:
    """Step-0 test pattern: odd/even active channels swap every ``period`` seconds."""
    times = row_times(rows, sample_rate, timing_ms)
    phase = (np.floor(times / period).astype(int) % 2)[:, None]
    out = np.zeros((rows, layout.channels))
    for n, c in enumerate(layout.active):
        out[:, c] = (phase[:, 0] == n % 2)
    return out


# ---------------------------------------------------------------------------
# onset_bands: each channel fires on hits in its own frequency band
# ---------------------------------------------------------------------------

@dataclass
class OnsetOptions:
    min_on: float = 0.20
    min_off: float = 0.15
    threshold: float = 3.0             # peaks must beat the local median by this many MADs
    floor: float = 0.15                # ...and reach this fraction of the song's strongest hits
    window: float = 0.5                # adaptive-threshold window (s)
    snap: float = 0.05                 # move a hit onto a beat within this many seconds
    max_per_beat: Optional[int] = None  # most channels starting in one beat (default half)
    quiet_db: float = 35.0


def _moving_median(x: np.ndarray, width: int) -> np.ndarray:
    width = max(1, width | 1)
    pad = width // 2
    padded = np.pad(x, pad, mode="edge")
    return np.median(np.lib.stride_tricks.sliding_window_view(padded, width), axis=1)


def band_flux(mel_db: np.ndarray, groups: int) -> np.ndarray:
    """Positive spectral flux per band: [groups, frames], low bands first."""
    mel_db = np.asarray(mel_db, dtype=float)
    edges = np.linspace(0, mel_db.shape[0], groups + 1).astype(int)
    diff = np.maximum(0.0, np.diff(mel_db, axis=1, prepend=mel_db[:, :1]))
    return np.array([diff[a:b].sum(axis=0) for a, b in zip(edges[:-1], edges[1:])])


def onset_bands(mel_times, mel_db, beat_times, energy_times, energy_db, rows: int,
                sample_rate: int, layout: Layout, timing_ms: int = 25,
                opts: Optional[OnsetOptions] = None) -> np.ndarray:
    """
    Event-based band triggering: the FFT show's "one band per channel" idea,
    but channels fire on hits (spectral flux peaks) instead of sustained level.
    Hits near a beat snap onto it; each pulse holds about half a beat; at most
    half the channels may start in any one beat (the strongest win).
    """
    opts = opts or OnsetOptions()
    times = row_times(rows, sample_rate, timing_ms)
    out = np.zeros((rows, layout.channels))
    active = layout.active
    mel_times = np.asarray(mel_times, dtype=float)
    if not active or rows == 0 or mel_times.size < 3:
        return out
    beats = np.asarray(beat_times, dtype=float)
    beat_period = float(np.median(np.diff(beats))) if beats.size > 1 else 0.5
    hold = float(np.clip(0.5 * beat_period, opts.min_on, max(opts.min_on, beat_period)))
    hop = float(np.median(np.diff(mel_times)))
    width = int(round(opts.window / hop))
    min_gap = max(1, int(round((hold + opts.min_off) / hop)))
    max_per_beat = opts.max_per_beat or max(1, (len(active) + 1) // 2)

    energy_db = np.asarray(energy_db, dtype=float)
    loud_db = float(np.percentile(energy_db, 95)) if energy_db.size else 0.0

    hits = []   # (time, strength, channel)
    flux = band_flux(mel_db, len(active))
    # strongest band's 99th percentile: robust to hits being a tiny share of frames
    floor = opts.floor * float(np.percentile(flux, 99, axis=1).max()) if flux.size else 0.0
    for band, channel in enumerate(active):
        f = flux[band]
        med = _moving_median(f, width)
        mad = _moving_median(np.abs(f - med), width) + 1e-6
        score = (f - med) / mad
        last = -min_gap
        for t in np.flatnonzero((score > opts.threshold) & (f >= floor)
                                & (score >= np.roll(score, 1)) & (score >= np.roll(score, -1))):
            if t - last < min_gap:
                continue
            last = t
            hits.append((mel_times[t], float(score[t]), channel))

    if beats.size:
        snapped = []
        for t, strength, channel in hits:
            j = int(np.argmin(np.abs(beats - t)))
            snapped.append((beats[j] if abs(beats[j] - t) <= opts.snap else t, strength, channel))
        hits = snapped
        # density cap per beat interval: keep the strongest
        slot = np.searchsorted(beats, [h[0] + 1e-6 for h in hits])
        keep = []
        for s_idx in np.unique(slot):
            group = sorted((h for h, sl in zip(hits, slot) if sl == s_idx), key=lambda h: -h[1])
            keep.extend(group[:max_per_beat])
        hits = keep

    hold_rows = seconds_to_rows(hold, sample_rate, timing_ms)
    for t, _, channel in hits:
        if energy_db.size and np.interp(t, energy_times, energy_db) < loud_db - opts.quiet_db:
            continue
        r0 = int(np.searchsorted(times, t - 1e-9))
        out[r0:r0 + hold_rows, channel] = 1.0
    return relay_safe(out, sample_rate, timing_ms, opts.min_on, opts.min_off)


# ---------------------------------------------------------------------------
# sections: a different look per section, the same look when a section repeats
# ---------------------------------------------------------------------------

SECTION_STYLES = ["sparse", "ab", "chase", "all"]   # quiet ... loud


def sections(seg_times, seg_labels, beat_times, energy_times, energy_db, rows: int,
             sample_rate: int, layout: Layout, timing_ms: int = 25,
             opts: Optional[ChaseOptions] = None) -> np.ndarray:
    """
    Section-aware styles. Each section's loudness rank picks a beat pattern
    (sparse -> A/B -> chase -> all-pulse); a repeated section (same label)
    reuses the first occurrence's pattern. Going into a louder section, the
    last half beat before it is dark and its first beat flashes everything.
    """
    opts = opts or ChaseOptions()
    times = row_times(rows, sample_rate, timing_ms)
    out = np.zeros((rows, layout.channels))
    if not layout.active or rows == 0:
        return out
    seg_times = np.asarray(seg_times, dtype=float)
    seg_labels = np.asarray(seg_labels, dtype=int)
    bounds = np.append(seg_times, times[-1] + 1)
    energy_db = np.asarray(energy_db, dtype=float)

    seg_energy = np.array([
        energy_db[(energy_times >= a) & (energy_times < b)].mean()
        if ((energy_times >= a) & (energy_times < b)).any() else energy_db.min(initial=-80)
        for a, b in zip(bounds[:-1], bounds[1:])])
    ranks = loudness_rank(seg_energy) if seg_energy.size > 1 else np.ones(1)

    style_for_label = {}
    renders = {}
    styles = []
    for label, rank in zip(seg_labels, ranks):
        style = style_for_label.setdefault(
            int(label), SECTION_STYLES[min(len(SECTION_STYLES) - 1, int(rank * len(SECTION_STYLES)))])
        styles.append(style)
        if style not in renders:
            sub = ChaseOptions(**{**opts.__dict__, "pattern": style})
            renders[style] = beat_steps(beat_times, energy_times, energy_db, rows, sample_rate,
                                        layout, timing_ms, sub)

    beats = np.asarray(beat_times, dtype=float)
    beat_period = float(np.median(np.diff(beats))) if beats.size > 1 else 0.5
    for i, (a, b) in enumerate(zip(bounds[:-1], bounds[1:])):
        mask = (times >= a) & (times < b)
        out[mask] = renders[styles[i]][mask]
        louder = i > 0 and seg_energy[i] > seg_energy[i - 1] + 1.0
        if louder:
            out[(times >= a - beat_period / 2) & (times < a)] = 0.0
            first = (times >= a) & (times < a + beat_period * 0.75)
            out[np.ix_(first, layout.active)] = 1.0
    return relay_safe(out, sample_rate, timing_ms, opts.min_on, opts.min_off)


# ---------------------------------------------------------------------------
# fft_safe: today's FFT show with hysteresis and relay limits
# ---------------------------------------------------------------------------

def hysteresis(levels: np.ndarray, on_above: float = 0.6, off_below: float = 0.4) -> np.ndarray:
    """0/1 per column: turn on above ``on_above``, off below ``off_below``."""
    levels = np.asarray(levels, dtype=float)
    out = np.zeros_like(levels)
    state = levels[0] > on_above if levels.size else None
    for r in range(levels.shape[0]):
        state = np.where(levels[r] > on_above, True, np.where(levels[r] < off_below, False, state))
        out[r] = state
    return out


def fft_safe(fft_levels_on_grid: np.ndarray, sample_rate: int, timing_ms: int = 25,
             min_on: float = 0.20, min_off: float = 0.15) -> np.ndarray:
    """The current FFT look, minus the flicker: hysteresis, then relay limits."""
    return relay_safe(hysteresis(fft_levels_on_grid), sample_rate, timing_ms, min_on, min_off)
