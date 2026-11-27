"""
Composition strategies: features in, [rows, channels] levels (0..1) out.

Inputs are plain numpy (beat times, an energy curve), so these run and test
without librosa. Every relay strategy ends with safety.apply().
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

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
    hold_beats: float = 0.5            # pulse length, in beats (clamped to min_on .. 1 beat)
    dynamic: bool = False              # channels per beat follow loudness (1 when quiet .. all)
    drop_db: Optional[float] = None    # cut to dark when loudness falls this far below its recent peak
    drop_sustain: float = 0.15         # ...and stays down this long (s)
    drop_window: float = 3.0           # "recent peak" = loudest point in this many seconds (s)
    scheduler: str = "smart"           # "smart": shorten/overflow/strongest-wins; "first": first hit wins
    overflow: int = 1                  # smart: try neighbour lights up to this many places away


RELAY_PRESETS = {
    # (min_on, min_off) seconds
    "mechanical": (0.20, 0.15),   # contacts wear: few, well-spaced switches
    "ssr": (0.05, 0.05),          # solid-state relays: as fast as the 25 ms rows allow, with a floor
}


@dataclass
class Hit:
    time: float
    strength: float
    channel: int
    end: Optional[float] = None   # preferred end (tone voices); default start + hold


def schedule_hits(hits: List[Hit], rows: int, times: np.ndarray, channels: int, hold_s: float,
                  min_on_s: float, min_off_s: float, sample_rate: int, timing_ms: int = 25,
                  neighbours: Optional[Dict[int, List[int]]] = None) -> np.ndarray:
    """
    Place hits as relay pulses without losing close-together (often syncopated) hits.

    Hits are taken in time order. A hit whose light is still busy:
      1. shortens that light's current pulse (it ends ``min_off`` before the new
         hit) when the current pulse can stay at least ``min_on`` long;
      2. otherwise overflows to the first free light in ``neighbours[channel]``
         (close hits spread across neighbouring lights);
      3. otherwise replaces the current pulse if it is stronger (strongest wins),
         else is dropped.
    Every pulse is >= min_on and gaps are >= min_off, so no safety pass is needed.
    """
    out = np.zeros((rows, channels))
    if not hits or rows == 0:
        return out
    min_on = seconds_to_rows(min_on_s, sample_rate, timing_ms)
    min_off = seconds_to_rows(min_off_s, sample_rate, timing_ms)
    hold = max(min_on, seconds_to_rows(hold_s, sample_rate, timing_ms))
    pulses: Dict[int, List[list]] = {}   # channel -> [[start_row, end_row, strength], ...]

    def free_at(ch, r):
        ps = pulses.get(ch)
        return not ps or r >= ps[-1][1] + min_off

    def place(ch, r, hit):
        if hit.end is not None:
            end = int(np.searchsorted(times, hit.end - 1e-9))
            end = max(r + min_on, end)
        else:
            end = r + hold
        pulses.setdefault(ch, []).append([r, min(rows, end), hit.strength])

    for hit in sorted(hits, key=lambda h: (h.time, -h.strength)):
        r = max(0, int(np.searchsorted(times, hit.time + 1e-9, side="right")) - 1)  # row containing it
        if r >= rows:
            continue
        c = hit.channel
        if free_at(c, r):
            place(c, r, hit)
            continue
        last = pulses[c][-1]
        if r > last[0] and r - min_off - last[0] >= min_on:      # 1. shorten
            last[1] = r - min_off
            place(c, r, hit)
            continue
        free = [n for n in (neighbours or {}).get(c, []) if free_at(n, r)]
        if free:                                                  # 2. overflow
            place(free[0], r, hit)
            continue
        if hit.strength > last[2]:                                # 3. strongest wins
            ps = pulses[c]
            before = ps[-2] if len(ps) > 1 else None
            if before is None or r >= before[1] + min_off:
                ps.pop()
                place(c, r, hit)

    for ch, ps in pulses.items():
        for start, end, _ in ps:
            out[start:end, ch] = 1.0
    return out


def neighbour_map(active: List[int], reach: int) -> Dict[int, List[int]]:
    """For each light, the lights up to ``reach`` places away in display order, nearest first."""
    out = {}
    for i, c in enumerate(active):
        near = []
        for d in range(1, reach + 1):
            near += [active[j] for j in (i - d, i + d) if 0 <= j < len(active)]
        out[c] = near
    return out


def fast_energy_db(mel_db: np.ndarray, mel_times: np.ndarray, smooth_s: float = 0.05) -> np.ndarray:
    """Loudness in dB per spectrogram frame, ~50 ms smoothing (the 1 s curve is too slow)."""
    power = np.sum(10 ** (np.asarray(mel_db, dtype=float) / 10), axis=0)
    hop = float(np.median(np.diff(mel_times))) if len(mel_times) > 1 else 0.0116
    width = max(1, int(round(smooth_s / hop)))
    power = np.convolve(power, np.ones(width) / width, mode="same")
    return 10 * np.log10(np.maximum(power, 1e-12))


def drop_mask(env_db: np.ndarray, frame_s: float, drop_db: float, sustain_s: float,
              window_s: float) -> np.ndarray:
    """
    True where the music has dropped: ``env_db`` is more than ``drop_db`` below
    its maximum over the previous ``window_s`` for at least ``sustain_s``. The
    whole run is marked from its first frame, so lights go dark the moment the
    drop starts (we know the future offline).
    """
    env_db = np.asarray(env_db, dtype=float)
    n = env_db.size
    if n == 0:
        return np.zeros(0, dtype=bool)
    w = max(1, int(round(window_s / frame_s)))
    padded = np.concatenate([np.full(w - 1, env_db[0]), env_db])
    recent_peak = np.lib.stride_tricks.sliding_window_view(padded, w).max(axis=1)
    below = env_db < recent_peak - drop_db
    out = np.zeros(n, dtype=bool)
    need = max(1, int(round(sustain_s / frame_s)))
    for start, end in safety.runs(below):
        if end - start >= need:
            out[start:end] = True
    return out


def dark_frames(mel_db: np.ndarray, mel_times: np.ndarray, drop_db: float,
                sustain_s: float = 0.15, window_s: float = 3.0, quiet_db: float = 35.0,
                momentary_s: float = 0.3) -> np.ndarray:
    """
    Frames where the lights should be dark: near-silence, or a sustained drop.

    Drops are judged on a "momentary loudness" curve (centred window of at
    least one beat), not the 50 ms envelope: between drum hits the fast
    envelope dips 10+ dB on every beat, which would read as a drop. The centred
    window means a real drop is marked from about when it starts (up to half a
    window early, never late).
    """
    momentary = fast_energy_db(mel_db, mel_times, smooth_s=momentary_s)
    hop = float(np.median(np.diff(mel_times)))
    quiet = momentary < np.percentile(momentary, 95) - quiet_db
    drops = drop_mask(momentary, hop, drop_db, sustain_s, window_s)
    # The centred average crosses the threshold up to ~half a window after the
    # music actually drops; move each drop's start back to the steepest fall of
    # the 50 ms envelope inside that window (the real cliff).
    fast = fast_energy_db(mel_db, mel_times)
    lag = max(1, int(round(0.05 / hop)))
    fall = np.full(fast.size, 0.0)
    fall[lag:] = fast[lag:] - fast[:-lag]           # dB change over the last 50 ms
    back = int(round(momentary_s / hop))
    for start, end in safety.runs(drops):
        lo = max(0, start - back)
        if start > lo:
            cliff = lo + int(np.argmin(fall[lo:start + 1])) - lag // 2
            drops[max(0, cliff):start] = True
    return quiet | drops


def cut(levels: np.ndarray, dark: np.ndarray, min_on_rows: int,
        min_off_rows: int = 0) -> np.ndarray:
    """
    Force rows in ``dark`` off after the relay-safety pass, then repair the
    relay limits by removing ON time only (never adding it):

    - an ON fragment shorter than ``min_on_rows`` is removed;
    - an ON run that follows an OFF gap shorter than ``min_off_rows`` (a short
      dark span inside a long pulse) is removed, so the light stays dark.
    """
    out = np.array(levels, dtype=float)
    out[np.asarray(dark, dtype=bool)] = 0.0
    total = out.shape[0]
    for c in range(out.shape[1]):
        prev_end = None
        for start, end in safety.runs(out[:, c] > 0.5):
            short_on = end - start < min_on_rows and end != total
            short_gap = prev_end is not None and start - prev_end < min_off_rows
            if short_on or short_gap:
                out[start:end, c] = 0.0
            else:
                prev_end = end
    return out


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
    hold = float(np.clip(opts.hold_beats * beat_period, opts.min_on, max(opts.min_on, beat_period)))
    hop = float(np.median(np.diff(mel_times)))
    width = int(round(opts.window / hop))
    smart = opts.scheduler == "smart"
    # "first" (the original): one hit per band per hold + gap. "smart": detect
    # hits as close as the relays allow and let the scheduler resolve clashes.
    gap_s = (opts.min_on + opts.min_off) if smart else (hold + opts.min_off)
    min_gap = max(1, int(round(max(gap_s, 0.06) / hop)))
    max_per_beat = opts.max_per_beat or max(1, (len(active) + 1) // 2)

    energy_db = np.asarray(energy_db, dtype=float)
    loud_db = float(np.percentile(energy_db, 95)) if energy_db.size else 0.0
    rank = loudness_rank(energy_db) if energy_db.size else None

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
            if smart:
                # cap distinct lights per beat, keeping every hit of the chosen lights
                # (a light may fire twice in a beat: that's the syncopation)
                strongest = {}
                for h in group:
                    strongest.setdefault(h[2], h[1])
                ranked = sorted(strongest, key=lambda ch: -strongest[ch])
            cap = max_per_beat
            if opts.dynamic and rank is not None:
                # Half where this moment ranks in the song, half how close it is to the
                # song's loud level in dB: a uniformly loud (compressed) song stays full
                # instead of having contrast invented by the ranking.
                t0 = group[0][0]
                db_level = np.clip((np.interp(t0, energy_times, energy_db) - (loud_db - 12.0))
                                   / 12.0, 0.0, 1.0)
                level = 0.5 * float(np.interp(t0, energy_times, rank)) + 0.5 * float(db_level)
                cap = 1 + int(round(level * (len(active) - 1)))
            if smart:
                chosen = set(ranked[:cap])
                keep.extend(h for h in group if h[2] in chosen)
            else:
                keep.extend(group[:cap])
        hits = keep

    loud_enough = [h for h in hits if not (energy_db.size and np.interp(
        h[0], energy_times, energy_db) < loud_db - opts.quiet_db)]
    if smart:
        out = schedule_hits([Hit(t, st, ch) for t, st, ch in loud_enough], rows, times,
                            layout.channels, hold, opts.min_on, opts.min_off, sample_rate,
                            timing_ms, neighbour_map(active, opts.overflow))
    else:
        hold_rows = seconds_to_rows(hold, sample_rate, timing_ms)
        for t, _, channel in loud_enough:
            r0 = int(np.searchsorted(times, t - 1e-9))
            out[r0:r0 + hold_rows, channel] = 1.0
    out = relay_safe(out, sample_rate, timing_ms, opts.min_on, opts.min_off)
    if opts.drop_db is None:
        return out
    # Track drops immediately: near-silence and sudden sustained drops go dark
    dark = dark_frames(mel_db, mel_times, opts.drop_db, opts.drop_sustain, opts.drop_window,
                       opts.quiet_db, momentary_s=max(0.3, beat_period))
    dark_rows = np.interp(times, mel_times, dark.astype(float)) > 0.5
    return cut(out, dark_rows, seconds_to_rows(opts.min_on, sample_rate, timing_ms),
               seconds_to_rows(opts.min_off, sample_rate, timing_ms))


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
             min_on: float = 0.20, min_off: float = 0.15, on_above: float = 0.6,
             off_below: float = 0.4) -> np.ndarray:
    """The current FFT look, minus the flicker: hysteresis, then relay limits."""
    return relay_safe(hysteresis(fft_levels_on_grid, on_above, off_below), sample_rate,
                      timing_ms, min_on, min_off)


# ---------------------------------------------------------------------------
# onset_runs: dynamic hits per band + scale sweeps + dramatic starts and stops
# ---------------------------------------------------------------------------

@dataclass
class Run:
    start: float          # seconds (first note)
    end: float            # seconds (last note)
    times: np.ndarray     # note onsets
    pitch: np.ndarray     # unfolded pitch (MIDI), monotonic-ish
    direction: int        # +1 up, -1 down


def find_runs(note_times, note_pitch, max_gap: float = 0.35, min_moves: int = 4,
              min_span: float = 7.0, max_repeats: int = 1, max_median_gap: float = 0.25
              ) -> List["Run"]:
    """
    Fast scale/arpeggio runs: chains of notes at most ``max_gap`` apart whose
    pitch keeps moving one way for ``min_moves``+ pitch changes (5+ distinct
    notes) and ``min_span``+ semitones, with no more than ``max_repeats``
    repeated notes in a row and a median note gap of at most ``max_median_gap``
    (scales are fast). Steps bigger than a tritone are folded by octaves, since
    the pitch tracker often jumps an octave on the same note.
    """
    t = np.asarray(note_times, dtype=float)
    p = np.asarray(note_pitch, dtype=float)
    keep = np.isfinite(p)
    t, p = t[keep], p[keep]
    runs_out: List[Run] = []
    i = 0
    while i < t.size - 1:
        j, direction, unfolded, moves, repeats = i, 0, [p[i]], 0, 0
        while j + 1 < t.size and t[j + 1] - t[j] <= max_gap:
            step = p[j + 1] - p[j]
            step = ((step + 6) % 12) - 6 if abs(step) > 6 else step
            sign = int(np.sign(round(step)))
            if sign and direction and sign != direction:
                break
            repeats = repeats + 1 if not sign else 0
            if repeats > max_repeats:
                break
            moves += bool(sign)
            direction = direction or sign
            unfolded.append(unfolded[-1] + step)
            j += 1
        fast = j > i and float(np.median(np.diff(t[i:j + 1]))) <= max_median_gap
        if direction and moves >= min_moves and fast and abs(unfolded[-1] - unfolded[0]) >= min_span:
            runs_out.append(Run(float(t[i]), float(t[j]), t[i:j + 1].copy(),
                                np.asarray(unfolded), direction))
            i = j + 1
        else:
            i += 1
    return runs_out


def sweep(run: Run, order: List[int], times: np.ndarray, out: np.ndarray, hold: float) -> None:
    """
    Fill ``order`` (one end of the display to the other) following the run's
    pitch: going up, lights come on from order[0] towards order[-1]; going down,
    from order[-1] back. A light stays on once lit until the run ends + ``hold``,
    so each relay switches once per run however fast the notes are.
    """
    g = len(order)
    lo, hi = float(np.min(run.pitch)), float(np.max(run.pitch))
    window = (times >= run.start - 1e-9) & (times < run.end + hold)
    out[window] = 0.0
    reached = 0
    for k, (t0, pitch) in enumerate(zip(run.times, run.pitch)):
        pos = int(round((pitch - lo) / max(hi - lo, 1e-9) * (g - 1)))
        reached = max(reached, pos if run.direction > 0 else g - 1 - pos)
        lit = order[:reached + 1] if run.direction > 0 else order[g - 1 - reached:]
        on = (times >= t0 - 1e-9) & (times < run.end + hold)
        out[np.ix_(on, lit)] = 1.0


def onset_runs(feats, rows: int, sample_rate: int, layout: Layout, timing_ms: int = 25,
               opts: Optional[OnsetOptions] = None, order: Optional[List[int]] = None,
               drop_db: float = 10.0):
    """
    Dynamic hits per band, plus:
    - scale sweeps: fast rising/falling runs fill the display end to end with the notes
    - starts as dramatic as the stops: after every drop cut, the first hit as the
      music comes back flashes every light for half a beat (at least min_on)
    Returns (levels, number of runs found).
    """
    opts = opts or OnsetOptions(dynamic=True, drop_db=drop_db)
    times = row_times(rows, sample_rate, timing_ms)
    out = onset_bands(feats.mel_times, feats.mel_db, feats.beat_times, feats.energy_times,
                      feats.energy_db, rows, sample_rate, layout, timing_ms, opts)
    order = [c for c in (order or layout.active) if c in set(layout.active)]
    beats = np.asarray(feats.beat_times, dtype=float)
    beat_period = float(np.median(np.diff(beats))) if beats.size > 1 else 0.5
    hold = max(opts.min_on, 0.5 * beat_period)

    runs_found = find_runs(feats.note_times, feats.note_pitch) if feats.note_times is not None else []
    for run in runs_found:
        sweep(run, order, times, out, hold)

    dark = dark_frames(feats.mel_db, feats.mel_times, drop_db, opts.drop_sustain, opts.drop_window,
                       opts.quiet_db, momentary_s=max(0.3, beat_period))
    onsets = np.asarray(feats.onset_times, dtype=float)
    flash = max(opts.min_on, 0.5 * beat_period)
    for start, end in safety.runs(dark):
        if end >= dark.size:
            continue
        back = feats.mel_times[end]
        after = onsets[(onsets >= back - 0.1) & (onsets <= back + 0.4)]
        if after.size:
            t0 = float(after[0])
            out[np.ix_((times >= t0 - 1e-9) & (times < t0 + flash), layout.active)] = 1.0

    out = relay_safe(out, sample_rate, timing_ms, opts.min_on, opts.min_off)
    dark_rows = np.interp(times, feats.mel_times, dark.astype(float)) > 0.5
    out = cut(out, dark_rows, seconds_to_rows(opts.min_on, sample_rate, timing_ms),
              seconds_to_rows(opts.min_off, sample_rate, timing_ms))
    return out, len(runs_found)
