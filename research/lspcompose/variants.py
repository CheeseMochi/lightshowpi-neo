"""
The show variants a bundle contains: one entry per thing to compare on the lights.

Each builder gets a Context and returns [rows, channels] levels in 0..1 for the
whole song; bundle.py cuts the clip out of that, so clips keep musical context.
"""

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

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
    stems: Optional[object] = None   # stems.StemFeatures, when Demucs ran
    labels: Dict[str, List[str]] = field(default_factory=dict)  # variant -> label per channel


@dataclass
class Variant:
    name: str
    title: str
    description: str
    build: Callable[[Context], np.ndarray]
    needs_stems: bool = False


def _chase(ctx: Context, **kw) -> np.ndarray:
    f = ctx.feats
    opts = ChaseOptions(min_on=ctx.min_on, min_off=ctx.min_off, **kw)
    return strategies.beat_chase(f.beat_times, f.energy_times, f.energy_db, ctx.rows,
                                 ctx.sample_rate, ctx.layout, ctx.timing_ms, opts)


def _onset(ctx: Context, **kw) -> np.ndarray:
    f = ctx.feats
    return strategies.onset_bands(
        f.mel_times, f.mel_db, f.beat_times, f.energy_times, f.energy_db, ctx.rows,
        ctx.sample_rate, ctx.layout, ctx.timing_ms,
        OnsetOptions(min_on=ctx.min_on, min_off=ctx.min_off, **kw))


def _stems(ctx: Context) -> np.ndarray:
    from . import stems as stems_mod
    levels, assignment = stems_mod.stems_show(
        ctx.stems, ctx.feats.mel_db, ctx.feats.mel_times, ctx.feats.beat_times, ctx.rows,
        ctx.sample_rate, ctx.layout, ctx.timing_ms, ctx.min_on, ctx.min_off)
    ctx.labels["stems"] = [stems_mod.voice_spec(assignment[c])[4] if c in assignment else "(unused)"
                           for c in range(ctx.layout.channels)]
    return levels


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
    Variant("onset_bands", "Hits per band",
            "Like today's one-band-per-channel idea, but channels fire on hits (drum, note "
            "attacks) in their band instead of loudness; hits snap to beats. (Your pick.)",
            lambda c: _onset(c)),
    Variant("onset_dynamic", "Hits per band, dynamic",
            "Hits per band, but how many channels may fire per beat follows loudness (sparse "
            "when quiet, full in big moments), and everything cuts to dark the instant the "
            "music drops 10 dB and stays down.",
            lambda c: _onset(c, dynamic=True, drop_db=10.0)),
    Variant("onset_smooth", "Hits per band, dynamic + long",
            "The dynamic version with each hit held a full beat: calmer, less blinking.",
            lambda c: _onset(c, dynamic=True, drop_db=10.0, hold_beats=1.0)),
    Variant("onset_sensitive", "Hits per band, dynamic + sensitive",
            "The dynamic version that also catches softer hits (for gentle songs) and cuts "
            "on smaller drops (6 dB).",
            lambda c: _onset(c, dynamic=True, drop_db=6.0, threshold=2.0, floor=0.08)),
    Variant("stems", "Instrument per light",
            "Demucs splits the song into instruments; each light follows one: kick drum, "
            "snare/hats, bass, low voice, high voice, guitar, piano, other (whichever are "
            "present). Drums blink on hits; voices light per note while they sound.",
            _stems, needs_stems=True),
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
