"""
The show variants a bundle contains: patterns (a base strategy plus options)
and mixes (a pattern per light source or channel group).

Patterns are data: research/patterns.toml holds the built-in ones and
research/patterns.local.toml (git-ignored) Steve's own tweaks and additions,
so iterating on a look means editing options, not code. Each builder gets a
Context and returns [rows, channels] levels in 0..1 for the whole song;
bundle.py cuts the clip out of that, so clips keep musical context.
"""

import copy
import dataclasses
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from . import strategies
from .strategies import ChaseOptions, Layout, OnsetOptions

try:                                    # Python 3.11+
    import tomllib
except ImportError:                     # pragma: no cover - 3.10 and older
    tomllib = None

RESEARCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILTIN_FILE = os.path.join(RESEARCH_DIR, "patterns.toml")
LOCAL_FILE = os.path.join(RESEARCH_DIR, "patterns.local.toml")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")   # = sequence_select.VARIANT_RE


class PatternError(ValueError):
    """A pattern or mix definition that can't be used (message says why)."""


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
    order: Optional[List[int]] = None   # 0-based channels from one end of the display to the other
    extra: Dict[str, dict] = field(default_factory=dict)        # variant -> extra metrics


@dataclass
class Variant:
    name: str
    title: str
    description: str
    build: Callable[[Context], np.ndarray]
    needs_stems: bool = False
    spec: dict = field(default_factory=dict)   # what built it, for manifest.json


# ---------------------------------------------------------------------------
# Base strategies: options they accept and how they render
# ---------------------------------------------------------------------------

RELAY_KEYS = ("min_on", "min_off")


def _fields(cls, *drop):
    return {f.name: f for f in dataclasses.fields(cls) if f.name not in drop}


def _with_relays(ctx: Context, opts: dict) -> Tuple[float, float]:
    return float(opts.get("min_on", ctx.min_on)), float(opts.get("min_off", ctx.min_off))


def _onset_options(ctx: Context, opts: dict, **force) -> OnsetOptions:
    on, off = _with_relays(ctx, opts)
    kw = {k: v for k, v in opts.items() if k not in RELAY_KEYS}
    kw.update(force)
    return OnsetOptions(min_on=on, min_off=off, **kw)


def _chase_options(ctx: Context, opts: dict) -> ChaseOptions:
    on, off = _with_relays(ctx, opts)
    kw = {k: v for k, v in opts.items() if k not in RELAY_KEYS + ("every_beats",)}
    return ChaseOptions(min_on=on, min_off=off, **kw)


def _b_fft(ctx, opts, name):
    return ctx.fft_levels


def _b_fft_safe(ctx, opts, name):
    on, off = _with_relays(ctx, opts)
    return strategies.fft_safe(ctx.fft_levels, ctx.sample_rate, ctx.timing_ms, on, off,
                               on_above=float(opts.get("on_above", 0.6)),
                               off_below=float(opts.get("off_below", 0.4)))


def _b_onset(ctx, opts, name):
    f = ctx.feats
    return strategies.onset_bands(f.mel_times, f.mel_db, f.beat_times, f.energy_times,
                                  f.energy_db, ctx.rows, ctx.sample_rate, ctx.layout,
                                  ctx.timing_ms, _onset_options(ctx, opts))


def _b_runs(ctx, opts, name):
    drop_db = float(opts.get("drop_db") or 10.0)
    levels, n = strategies.onset_runs(
        ctx.feats, ctx.rows, ctx.sample_rate, ctx.layout, ctx.timing_ms,
        _onset_options(ctx, opts, dynamic=True, drop_db=drop_db), order=ctx.order,
        drop_db=drop_db)
    ctx.extra[name] = {"scale_runs": float(n)}
    return levels


def _b_stems(ctx, opts, name):
    from . import stems as stems_mod
    on, off = _with_relays(ctx, opts)
    levels, assignment = stems_mod.stems_show(
        ctx.stems, ctx.feats.mel_db, ctx.feats.mel_times, ctx.feats.beat_times, ctx.rows,
        ctx.sample_rate, ctx.layout, ctx.timing_ms, on, off,
        drop_db=opts.get("drop_db", 10.0))
    ctx.labels[name] = [stems_mod.voice_spec(assignment[c])[4] if c in assignment else "(unused)"
                        for c in range(ctx.layout.channels)]
    return levels


def _b_chase(ctx, opts, name):
    f = ctx.feats
    every = int(opts.get("every_beats", 1))
    if every < 1:
        raise PatternError(f"{name}: every_beats must be 1 or more")
    return strategies.beat_chase(f.beat_times[::every], f.energy_times, f.energy_db, ctx.rows,
                                 ctx.sample_rate, ctx.layout, ctx.timing_ms,
                                 _chase_options(ctx, opts))


def _b_sections(ctx, opts, name):
    f = ctx.feats
    return strategies.sections(f.seg_times, f.seg_labels, f.beat_times, f.energy_times,
                               f.energy_db, ctx.rows, ctx.sample_rate, ctx.layout,
                               ctx.timing_ms, _chase_options(ctx, opts))


def _b_alternate(ctx, opts, name):
    return strategies.alternate(ctx.rows, ctx.sample_rate, ctx.layout,
                                period=float(opts.get("period", 0.5)), timing_ms=ctx.timing_ms)


_RELAY_FIELDS = {k: float for k in RELAY_KEYS}
_ONSET = {k: f.type for k, f in _fields(OnsetOptions, *RELAY_KEYS).items()}
_CHASE = {k: f.type for k, f in _fields(ChaseOptions, *RELAY_KEYS).items()}

# base -> (renderer, {option: type}, defaults object for the GUI, needs stems)
BASES: Dict[str, tuple] = {
    "fft": (_b_fft, {}, None, False),
    "fft_safe": (_b_fft_safe, {"on_above": float, "off_below": float, **_RELAY_FIELDS},
                 {"on_above": 0.6, "off_below": 0.4}, False),
    "onset_bands": (_b_onset, {**_ONSET, **_RELAY_FIELDS}, OnsetOptions(), False),
    "onset_runs": (_b_runs, {**_ONSET, **_RELAY_FIELDS}, OnsetOptions(dynamic=True, drop_db=10.0),
                   False),
    "stems": (_b_stems, {"drop_db": float, **_RELAY_FIELDS}, {"drop_db": 10.0}, True),
    "beat_chase": (_b_chase, {**_CHASE, "every_beats": int, **_RELAY_FIELDS},
                   ChaseOptions(), False),
    "sections": (_b_sections, {**_CHASE, **_RELAY_FIELDS}, ChaseOptions(), False),
    "alternate": (_b_alternate, {"period": float}, {"period": 0.5}, False),
}
META_KEYS = ("title", "description", "base")


def option_types(base: str) -> Dict[str, object]:
    """Option name -> type annotation (as declared) for a base strategy."""
    return dict(BASES[base][1])


def option_defaults(base: str) -> Dict[str, object]:
    """Option name -> default value for a base strategy (for the GUI's forms)."""
    defaults = BASES[base][2]
    if defaults is None:
        return {}
    if isinstance(defaults, dict):
        return dict(defaults)
    return {k: getattr(defaults, k) for k in option_types(base) if hasattr(defaults, k)}


def _check_value(pattern: str, key: str, value, declared):
    text = str(declared)
    if isinstance(value, bool):
        ok = "bool" in text
    elif isinstance(value, (int, float)):
        ok = any(t in text for t in ("float", "int"))
        if "int" in text and "float" not in text and isinstance(value, float) and not value.is_integer():
            ok = False
    elif isinstance(value, str):
        ok = "str" in text
    else:
        ok = False
    if not ok:
        raise PatternError(f"{pattern}: option {key} = {value!r} has the wrong type ({text})")


def validate_pattern(name: str, spec: dict) -> dict:
    """Check one [patterns.<name>] table; returns a clean copy."""
    if not NAME_RE.match(name):
        raise PatternError(f"pattern name {name!r}: use letters, digits, '-' and '_' (max 64)")
    if "base" not in spec:
        raise PatternError(f"{name}: needs base = one of {', '.join(sorted(BASES))}")
    base = spec["base"]
    if base not in BASES:
        raise PatternError(f"{name}: unknown base {base!r} (one of {', '.join(sorted(BASES))})")
    types = option_types(base)
    for key, value in spec.items():
        if key in META_KEYS:
            continue
        if key not in types:
            raise PatternError(f"{name}: {base} has no option {key!r} "
                               f"(options: {', '.join(sorted(types)) or 'none'})")
        _check_value(name, key, value, types[key])
    return dict(spec)


def parse_channels(text: str, width: int) -> List[int]:
    """'1-7' / '1,3,5-6' (1-based) -> 0-based list, each < width."""
    out = []
    for part in str(text).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = (int(x) for x in part.split("-", 1))
            out.extend(range(lo, hi + 1))
        else:
            out.append(int(part))
    bad = [c for c in out if c < 1 or c > width]
    if bad:
        raise PatternError(f"channels {text!r}: {bad} outside 1..{width}")
    return [c - 1 for c in dict.fromkeys(out)]


def validate_mix(name: str, spec: dict, patterns: Dict[str, dict]) -> dict:
    """Check one [mixes.<name>] table against the known patterns."""
    if not NAME_RE.match(name):
        raise PatternError(f"mix name {name!r}: use letters, digits, '-' and '_' (max 64)")
    groups = spec.get("groups")
    if not isinstance(groups, list) or not groups:
        raise PatternError(f"{name}: needs groups = [ {{channels = \"1-7\", pattern = \"...\"}} ]")
    for g in groups:
        if not isinstance(g, dict) or "channels" not in g or "pattern" not in g:
            raise PatternError(f"{name}: every group needs channels and pattern")
        if g["pattern"] not in patterns:
            raise PatternError(f"{name}: unknown pattern {g['pattern']!r}")
        if "relays" in g and g["relays"] not in strategies.RELAY_PRESETS:
            raise PatternError(f"{name}: relays must be one of {sorted(strategies.RELAY_PRESETS)}")
        extra = set(g) - {"channels", "pattern", "relays", "label"}
        if extra:
            raise PatternError(f"{name}: unknown group keys {sorted(extra)}")
    return dict(spec)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

@dataclass
class Library:
    patterns: Dict[str, dict]       # name -> spec (order kept)
    mixes: Dict[str, dict]
    sources: Dict[str, str]         # name -> "builtin" | "local"


def _read_toml(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    if tomllib is None:                         # pragma: no cover
        raise PatternError("reading patterns needs Python 3.11+ (tomllib)")
    with open(path, "rb") as f:
        try:
            return tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise PatternError(f"{path}: {e}") from e


def load_library(builtin: str = BUILTIN_FILE, local: Optional[str] = LOCAL_FILE) -> Library:
    """Built-in patterns, then local ones (same name replaces), then mixes."""
    patterns, mixes, sources = {}, {}, {}
    for path, source in ((builtin, "builtin"), (local, "local")):
        if not path:
            continue
        data = _read_toml(path)
        unknown = set(data) - {"patterns", "mixes"}
        if unknown:
            raise PatternError(f"{path}: unknown tables {sorted(unknown)} (use patterns / mixes)")
        for name, spec in data.get("patterns", {}).items():
            patterns[name] = validate_pattern(name, spec)
            sources[name] = source
        for name, spec in data.get("mixes", {}).items():
            mixes[name] = spec
            sources[name] = source
    clash = set(patterns) & set(mixes)
    if clash:
        raise PatternError(f"names used for both a pattern and a mix: {sorted(clash)}")
    for name, spec in mixes.items():
        validate_mix(name, spec, patterns)
    return Library(patterns, mixes, sources)


# ---------------------------------------------------------------------------
# Variants from patterns and mixes
# ---------------------------------------------------------------------------

def pattern_variant(name: str, spec: dict) -> Variant:
    renderer, _types, _defaults, needs_stems = BASES[spec["base"]]
    options = {k: v for k, v in spec.items() if k not in META_KEYS}

    def build(ctx: Context) -> np.ndarray:
        return renderer(ctx, options, name)

    return Variant(name, spec.get("title", name), spec.get("description", ""), build,
                   needs_stems, {"base": spec["base"], "options": options})


def _group_context(ctx: Context, channels: List[int], relays: Optional[str]) -> Context:
    sub = copy.copy(ctx)
    keep = set(channels) - set(ctx.layout.dead)
    sub.layout = Layout(ctx.layout.channels, [c for c in range(ctx.layout.channels) if c not in keep])
    if relays:
        sub.min_on, sub.min_off = strategies.RELAY_PRESETS[relays]
    if ctx.order:
        sub.order = [c for c in ctx.order if c in keep] or None
    sub.labels, sub.extra = {}, {}
    return sub


def mix_variant(name: str, spec: dict, patterns: Dict[str, dict]) -> Variant:
    groups = spec["groups"]
    members = [pattern_variant(g["pattern"], patterns[g["pattern"]]) for g in groups]

    def build(ctx: Context) -> np.ndarray:
        width = ctx.layout.channels
        out = np.zeros((ctx.rows, width))
        labels = ["(unused)"] * width
        for g, member in zip(groups, members):
            cols = parse_channels(g["channels"], width)
            sub = _group_context(ctx, cols, g.get("relays"))
            levels = member.build(sub)
            out[:, cols] = levels[:, cols]
            names = sub.labels.get(member.name)
            for c in cols:
                labels[c] = (g.get("label") or member.title) + (f": {names[c]}" if names else "")
        ctx.labels[name] = labels
        return out

    title = spec.get("title") or " + ".join(
        patterns[g["pattern"]].get("title", g["pattern"]) for g in groups)
    description = spec.get("description") or "; ".join(
        f"channels {g['channels']}: {patterns[g['pattern']].get('title', g['pattern'])}"
        + (f" ({g['relays']} relays)" if g.get("relays") else "") for g in groups)
    return Variant(name, title, description, build, any(m.needs_stems for m in members),
                   {"mix": [dict(g) for g in groups]})


def library_variants(library: Library) -> List[Variant]:
    """Every pattern, then every mix, as bundle variants."""
    return ([pattern_variant(n, s) for n, s in library.patterns.items()]
            + [mix_variant(n, s, library.patterns) for n, s in library.mixes.items()])


def builtin_variants() -> List[Variant]:
    """The built-in patterns only (no local file): stable for tests and docs."""
    return library_variants(load_library(BUILTIN_FILE, None))


VARIANTS: List[Variant] = builtin_variants()
BY_NAME = {v.name: v for v in VARIANTS}
