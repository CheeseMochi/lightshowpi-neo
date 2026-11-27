"""
Relay safety: minimum on and off times for mechanical relays.

Unlike a simple "ignore changes until the dwell has passed" filter, this never
moves an ON edge later, because ON edges are placed on beats and a late edge
looks out of sync. Violations are fixed by:

1. extending ON pulses that are too short (to ``min_on``), then
2. for OFF gaps that are too short: ending the previous pulse earlier when it
   stays at least ``min_on`` long, otherwise merging the two pulses.

Guarantees, for each channel (``check`` verifies them):
- every ON run except one cut off by the end of the song is >= min_on rows
- every OFF gap between two ON runs is >= min_off rows
- every ON edge in the output is also an ON edge in the input
"""

from typing import List, Tuple

import numpy as np


def runs(on: np.ndarray) -> List[Tuple[int, int]]:
    """ON runs of a 1-D bool array as [start, end) row pairs."""
    on = np.asarray(on, dtype=bool)
    if on.size == 0:
        return []
    padded = np.concatenate([[False], on, [False]])
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return list(zip(edges[0::2].tolist(), edges[1::2].tolist()))


def relay_safety(on: np.ndarray, min_on: int, min_off: int) -> np.ndarray:
    """Apply min on/off times (in rows) to one channel; returns a new bool array."""
    on = np.asarray(on, dtype=bool)
    total = on.size
    pulses = [[s, max(e, min(s + min_on, total))] for s, e in runs(on)]

    merged: List[List[int]] = []
    for start, end in pulses:
        if not merged:
            merged.append([start, end])
            continue
        prev = merged[-1]
        if start <= prev[1]:  # overlaps after extension: merge
            prev[1] = max(prev[1], end)
            continue
        if start - prev[1] >= min_off:
            merged.append([start, end])
            continue
        # Gap too short: end the previous pulse earlier if it stays long enough
        new_prev_end = start - min_off
        if new_prev_end - prev[0] >= min_on:
            prev[1] = new_prev_end
            merged.append([start, end])
        else:
            prev[1] = max(prev[1], end)

    out = np.zeros(total, dtype=bool)
    for start, end in merged:
        out[start:end] = True
    return out


def apply(levels: np.ndarray, min_on: int, min_off: int) -> np.ndarray:
    """relay_safety on every column of a [rows, channels] 0/1 array."""
    out = np.zeros_like(levels, dtype=float)
    for c in range(levels.shape[1]):
        out[:, c] = relay_safety(levels[:, c] > 0.5, min_on, min_off)
    return out


def check(original: np.ndarray, safe: np.ndarray, min_on: int, min_off: int) -> List[str]:
    """Violations of the guarantees above for one channel (empty list = OK)."""
    problems = []
    total = safe.size
    safe_runs = runs(safe)
    for i, (start, end) in enumerate(safe_runs):
        if end - start < min_on and end != total:
            problems.append(f"ON run {start}-{end} shorter than {min_on}")
        if i and start - safe_runs[i - 1][1] < min_off:
            problems.append(f"OFF gap before {start} shorter than {min_off}")
    original_starts = {s for s, _ in runs(original)}
    for start, _ in safe_runs:
        if start not in original_starts:
            problems.append(f"ON edge at {start} not in the input")
    return problems
