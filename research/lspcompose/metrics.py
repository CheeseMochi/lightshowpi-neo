"""
Objective proxies for a light sequence (docs/research/ALGORITHMS.md section 7b).

They say whether a sequence is relay-safe, on the beat and not "everything
equally busy"; they can't say whether it looks good.
"""

from typing import Dict

import numpy as np

from .safety import runs
from .timing import row_times


def summarize(levels: np.ndarray, sample_rate: int, beat_times: np.ndarray,
              energy_times: np.ndarray, energy_db: np.ndarray, timing_ms: int = 25,
              min_on_s: float = 0.2, min_off_s: float = 0.15,
              tolerance_s: float = 0.05, quiet_db: float = 35.0) -> Dict[str, float]:
    """Metrics for a [rows, channels] 0..1 sequence (relays: ON when > 0.5)."""
    on = np.asarray(levels) > 0.5
    rows, channels = on.shape
    times = row_times(rows, sample_rate, timing_ms)
    period = times[1] - times[0] if rows > 1 else timing_ms / 1000
    minutes = max(rows * period / 60, 1e-9)
    beat_times = np.asarray(beat_times, dtype=float)

    on_edges, toggles, short_on, short_off = [], [], 0, 0
    for c in range(channels):
        col_runs = runs(on[:, c])
        on_edges.extend(times[s] for s, _ in col_runs)
        toggles.append(sum(1 for s, _ in col_runs if s > 0) + sum(1 for _, e in col_runs if e < rows))
        short_on += sum(1 for s, e in col_runs if (e - s) * period < min_on_s - 1e-9 and e < rows)
        short_off += sum(1 for (_, e0), (s1, _) in zip(col_runs, col_runs[1:])
                         if (s1 - e0) * period < min_off_s - 1e-9)

    if on_edges and beat_times.size:
        idx = np.clip(np.searchsorted(beat_times, on_edges), 1, beat_times.size - 1)
        nearest = np.minimum(np.abs(beat_times[idx] - on_edges),
                             np.abs(beat_times[idx - 1] - on_edges))
        on_beat = float(np.mean(nearest <= tolerance_s))
    else:
        on_beat = float("nan")

    duty = on.mean(axis=0)
    loud = np.percentile(energy_db, 95) if len(energy_db) else 0.0
    quiet_rows = np.interp(times, energy_times, energy_db) < loud - quiet_db if len(energy_db) \
        else np.zeros(rows, dtype=bool)
    silence_lit = float(on[quiet_rows].mean()) if quiet_rows.any() else 0.0
    varying = on[:, on.std(axis=0) > 0].astype(float)  # dead/always-on columns excluded
    if varying.shape[1] > 1:
        corr = np.corrcoef(varying.T)
        mean_corr = float(corr[np.triu_indices(varying.shape[1], 1)].mean())
    else:
        mean_corr = float("nan")

    return {
        "on_beat_fraction": on_beat,
        "toggles_per_channel_min_median": float(np.median(toggles) / minutes),
        "toggles_per_channel_min_max": float(np.max(toggles) / minutes),
        "short_on_violations": float(short_on),
        "short_off_violations": float(short_off),
        "duty_mean": float(duty.mean()),
        "duty_spread": float(duty.std()),
        "silence_lit_fraction": silence_lit,
        "mean_channel_correlation": mean_corr,
    }
