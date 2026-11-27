"""
Write and read the headerless song.csv format the Pi plays.

Rules (docs/research/ALGORITHMS.md section 1.2): no header, no blank or comment
lines, exactly ``gpio_len`` comma-separated numbers per row, one row per
audio chunk, values 0..brightness_range (relays are ON when > range / 2).
"""

from typing import Optional

import numpy as np


def write_csv(levels: np.ndarray, path: str, brightness_range: int = 255) -> None:
    """Write a [rows, channels] array of levels in 0..1 as integer CSV rows."""
    levels = np.asarray(levels, dtype=float)
    if levels.ndim != 2 or levels.shape[0] == 0 or levels.shape[1] == 0:
        raise ValueError(f"levels must be a non-empty [rows, channels] array, got {levels.shape}")
    values = np.rint(np.clip(levels, 0.0, 1.0) * brightness_range).astype(int)
    with open(path, "w", newline="\n") as f:
        for row in values:
            f.write(",".join(map(str, row)) + "\n")


def read_csv(path: str, brightness_range: int = 255, channels: Optional[int] = None) -> np.ndarray:
    """Read a CSV the way the Pi does; returns levels in 0..1. Raises on bad rows."""
    rows = []
    with open(path) as f:
        first = f.readline()
        width = len(first.rstrip().split(","))
        if channels is not None and width != channels:
            raise ValueError(f"{path}: {width} columns, the Pi expects {channels} (gpio_len)")
        f.seek(0)
        for number, line in enumerate(f, 1):
            values = list(map(float, line.strip().split(",")))  # ValueError like the Pi
            if len(values) != width:
                raise ValueError(f"{path}:{number}: {len(values)} columns, expected {width}")
            rows.append(values)
    return np.clip(np.asarray(rows, dtype=float) / brightness_range, 0.0, 1.0)
