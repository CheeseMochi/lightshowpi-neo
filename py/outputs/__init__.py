"""Light outputs driven by the show: LEDs today, Nanoleaf next.

Every output takes a frame of channel levels (floats 0..1, one per show
channel) through the same small interface, so the show loop, ambient mode and
on/off helpers can drive them all without knowing what they are.
"""
from outputs.base import Output  # noqa: F401
