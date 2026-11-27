"""
The Pi's channel layout, as the composer needs it.

``GET /api/layout`` on the Pi reports how many CSV columns a sequence needs
(``gpio_len``), which of them are physical GPIO, LED-EXTEND or virtual
channels, and which show channels each output (Nanoleaf, LED strips) uses.
The composer uses it to size CSVs, to run the FFT baseline on the base
channel count (virtual columns mirror, as on the Pi), and to offer one mix
group per light source. Without a Pi, ``PiLayout.simple(n)`` is n relays.
"""

import json
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class Group:
    label: str
    channels: List[int]          # 0-based show channels
    kind: str = "relays"         # relays | led | virtual | output


@dataclass
class PiLayout:
    gpio_len: int
    base_count: int                          # columns before any virtual channels
    groups: List[Group] = field(default_factory=list)
    virtual_source: List[int] = field(default_factory=list)   # FFT mirror source per virtual column

    @classmethod
    def simple(cls, channels: int) -> "PiLayout":
        return cls(channels, channels, [Group("Relays", list(range(channels)))])

    @property
    def virtual(self) -> List[int]:
        return list(range(self.base_count, self.gpio_len))

    def mirror_source(self, column: int) -> int:
        """Which base column an FFT-mode virtual column copies (as the Pi does)."""
        i = column - self.base_count
        if 0 <= i < len(self.virtual_source):
            return self.virtual_source[i]
        return i % max(1, self.base_count)

    def to_dict(self) -> dict:
        return {"gpio_len": self.gpio_len, "base_count": self.base_count,
                "virtual_source": self.virtual_source,
                "groups": [{"label": g.label, "channels": g.channels, "kind": g.kind}
                           for g in self.groups]}


def _span(value) -> Optional[Tuple[int, int]]:
    if not value:
        return None
    if isinstance(value, dict):
        return int(value.get("start", 0)), int(value.get("end", 0))
    return int(value[0]), int(value[1])


def from_api(data: dict) -> PiLayout:
    """
    Build a PiLayout from the Pi's /api/layout JSON (or a saved copy of it).

    Tolerant of optional keys: ``gpio_len`` is required; ``physical``,
    ``led_extend`` and ``virtual`` are [start, end) spans (or null);
    ``outputs`` is a list of {name, type, channels}; ``virtual_channel_source``
    optionally lists the FFT mirror source per virtual column.
    """
    if "gpio_len" not in data:
        raise ValueError("layout has no gpio_len")
    gpio_len = int(data["gpio_len"])
    physical = _span(data.get("physical")) or (0, gpio_len)
    led = _span(data.get("led_extend"))
    virtual = _span(data.get("virtual"))
    base = virtual[0] if virtual else gpio_len
    groups = []
    if physical[1] > physical[0]:
        groups.append(Group("GPIO relays", list(range(*physical)), "relays"))
    if led and led[1] > led[0]:
        groups.append(Group("LED strip channels", list(range(*led)), "led"))
    for out in data.get("outputs") or []:
        chans = [int(c) for c in out.get("channels") or [] if 0 <= int(c) < gpio_len]
        if chans:
            label = out.get("name") or out.get("type") or "output"
            groups.append(Group(str(label), chans, "output"))
    if virtual and virtual[1] > virtual[0]:
        claimed = {c for g in groups if g.kind == "output" for c in g.channels}
        rest = [c for c in range(*virtual) if c not in claimed]
        if rest:
            groups.append(Group("Virtual channels", rest, "virtual"))
    source = [int(x) for x in data.get("virtual_channel_source") or []]
    return PiLayout(gpio_len, base, groups, source)


def load(path: str) -> PiLayout:
    with open(path) as f:
        return from_api(json.load(f))
