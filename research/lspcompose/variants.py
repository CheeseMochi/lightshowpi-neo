"""
The show variants a bundle contains: one entry per thing to compare on the lights.

Each builder gets a Context and returns [rows, channels] levels in 0..1 for the
whole song; bundle.py cuts the clip out of that, so clips keep musical context.
"""

from dataclasses import dataclass
from typing import Callable, List

import numpy as np

from . import strategies
from .strategies import ChaseOptions, Layout, OnsetOptions


@dataclass
class Context:
    feats: object            # audio.Features
    rows: int
    sample_rate: int
    layout: Layout
    timing_ms: int
    min_on: float
    min_off: float
    fft_levels: np.ndarray   # today's FFT brightness on the CSV grid (0..1), dead columns off


@dataclass
class Variant:
    name: str
    title: str
    description: str
    build: Callable[[Context], np.ndarray]


def _chase(ctx: Context, **kw) -> np.ndarray:
    f = ctx.feats
    opts = ChaseOptions(min_on=ctx.min_on, min_off=ctx.min_off, **kw)
    return strategies.beat_chase(f.beat_times, f.energy_times, f.energy_db, ctx.rows,
                                 ctx.sample_rate, ctx.layout, ctx.timing_ms, opts)


def _half_beats(ctx: Context):
    return ctx.feats.beat_times[::2]


VARIANTS: List[Variant] = [
    Variant("fft", "Today's show (FFT)",
            "What plays now: one frequency band per channel, ON above a 0.5 threshold "
            "(simulated with the Pi's own fft.py and your chunk_size/mapping).",
            lambda c: c.fft_levels),
    Variant("fft_safe", "Today's show, relay-safe",
            "The same FFT levels with hysteresis (on > 0.6, off < 0.4) and minimum "
            "on/off times. Same look, far less flicker.",
            lambda c: strategies.fft_safe(c.fft_levels, c.sample_rate, c.timing_ms,
                                          c.min_on, c.min_off)),
    Variant("beat_chase", "Beat chase",
            "A group of adjacent channels steps along on every beat, bouncing at the ends; "
            "louder = bigger group, quiet = dark.",
            lambda c: _chase(c)),
    Variant("beat_chase_calm", "Beat chase, calm",
            "The chase on every other beat with at most 3 channels lit: slower, "
            "easier on relays.",
            lambda c: strategies.beat_chase(
                _half_beats(c), c.feats.energy_times, c.feats.energy_db, c.rows, c.sample_rate,
                c.layout, c.timing_ms,
                ChaseOptions(min_on=c.min_on, min_off=c.min_off, max_lit=3, min_step=0.6))),
    Variant("vu_meter", "VU meter on the beat",
            "A bar graph from channel 1 up, refreshed on each beat: more channels when louder.",
            lambda c: _chase(c, pattern="vu")),
    Variant("beat_ab", "A/B on the beat",
            "Odd and even channels swap on every beat.",
            lambda c: _chase(c, pattern="ab")),
    Variant("onset_bands", "Hits per band",
            "Like today's one-band-per-channel idea, but channels fire on hits (drum, note "
            "attacks) in their band instead of loudness; hits snap to beats.",
            lambda c: strategies.onset_bands(
                c.feats.mel_times, c.feats.mel_db, c.feats.beat_times, c.feats.energy_times,
                c.feats.energy_db, c.rows, c.sample_rate, c.layout, c.timing_ms,
                OnsetOptions(min_on=c.min_on, min_off=c.min_off))),
    Variant("sections", "Sections",
            "A different look per song section (sparse, A/B, chase, all-pulse by loudness), "
            "the same look when a section repeats; a blackout and flash into louder sections.",
            lambda c: strategies.sections(
                c.feats.seg_times, c.feats.seg_labels, c.feats.beat_times, c.feats.energy_times,
                c.feats.energy_db, c.rows, c.sample_rate, c.layout, c.timing_ms,
                ChaseOptions(min_on=c.min_on, min_off=c.min_off))),
    Variant("test_alternate", "Test pattern",
            "Odd/even swap every 0.5 s regardless of the music: checks the CSV path and "
            "audio/light sync.",
            lambda c: strategies.alternate(c.rows, c.sample_rate, c.layout,
                                           timing_ms=c.timing_ms)),
]

BY_NAME = {v.name: v for v in VARIANTS}
