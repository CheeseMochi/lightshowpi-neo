"""Virtual show channels: extra, non-physical columns after every other channel.

``[hardware] virtual_channels = N`` appends N show channels after the GPIO
channels and any LED EXTEND channels. GPIO never drives them; outputs such as
Nanoleaf can map to them (``[output:<name>] channels``), so they can run a
pattern of their own.

* CSV sequences supply every column (``gpio_len`` = base + N).
* In FFT mode only the base channels are analysed (so FFT bins, ``.sync``
  caches and ``custom_channel_mapping`` are unchanged) and each virtual channel
  mirrors one base channel: ``virtual_channel_source[i]`` (0-based), default
  ``i mod base``.

With N = 0 nothing in this module changes a frame.
"""
import logging

log = logging.getLogger(__name__)

# gpio_pins placeholder numbers for virtual channels (LED EXTEND uses 1000+)
PIN_BASE = 2000


def base_channel_count(hardware):
    """Show channels before any virtual channels (``gpio_len`` when there are none).

    :param hardware: ``cm.hardware`` (or a stub with at least ``gpio_len``)
    """
    return getattr(hardware, "base_channel_count", hardware.gpio_len)


def mirror_sources(hardware):
    """Base channel mirrored by each virtual channel in FFT mode ([] = none)."""
    return list(getattr(hardware, "virtual_channel_sources", None) or [])


def _strip_comment(text):
    """Drop an inline ``# comment`` (the config loader keeps it in the value)."""
    return (text or "").split("#", 1)[0].strip()


def parse_count(text):
    """``virtual_channels`` value -> int >= 0. Blank or invalid -> 0 (with a warning)."""
    text = _strip_comment(text)
    if not text:
        return 0
    try:
        count = int(text)
    except ValueError:
        log.warning("[hardware] virtual_channels = %r is not a whole number; using 0", text)
        return 0
    if count < 0:
        log.warning("[hardware] virtual_channels = %d is negative; using 0", count)
        return 0
    return count


def default_sources(count, base):
    """Virtual channel i mirrors base channel ``i mod base`` (0 when base is 0)."""
    if base <= 0:
        return [0] * count if count else []
    return [i % base for i in range(count)]


def parse_sources(text, count, base):
    """``virtual_channel_source`` -> list of ``count`` base channel indexes.

    Blank -> the default. A bad list (not numbers, wrong length, or a channel
    outside 0..base-1) is ignored with a warning and the default is used.
    """
    default = default_sources(count, base)
    text = _strip_comment(text)
    if not count or not text:
        return default
    try:
        sources = [int(part) for part in text.split(",")]
    except ValueError:
        log.warning("[hardware] virtual_channel_source = %r is not a list of numbers; "
                    "using the default", text)
        return default
    if len(sources) != count:
        log.warning("[hardware] virtual_channel_source has %d entries but virtual_channels = %d; "
                    "using the default", len(sources), count)
        return default
    bad = [s for s in sources if not 0 <= s < base]
    if bad:
        log.warning("[hardware] virtual_channel_source %s outside 0..%d; using the default",
                    bad, base - 1)
        return default
    return sources


def append_mirrored(levels, sources):
    """Append one mirrored level per virtual channel to an FFT frame.

    :param levels: base-channel levels (ndarray)
    :param sources: base channel index for each virtual channel
    :return: ``levels`` itself when there are no virtual channels, otherwise a
        new array of ``len(levels) + len(sources)`` values
    """
    if not sources:
        return levels
    import numpy as np  # here so configuration_manager's import stays numpy-free

    levels = np.asarray(levels)
    if len(levels) == 0:
        return np.zeros(len(sources), dtype=levels.dtype)
    return np.concatenate([levels, levels[sources]])
