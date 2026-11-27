"""
Show channel layout: which show channels are GPIO, LED EXTEND and virtual,
and which outputs read which channels.

Computed from the config files the same way the show resolves them
(``configuration_manager.load_channel_config``: LED configs first, then
``[hardware] virtual_channels``), on a fresh Configuration each call so the
API's own Configuration is never changed. Tokens and hosts are never returned.
"""

import importlib
import logging
from typing import Dict, List, Optional

import configuration_manager

log = logging.getLogger(__name__)

_led_available: Optional[bool] = None


def led_module_available() -> bool:
    """Hardware loads LED configs only when led_module imports; mirror that."""
    global _led_available
    if _led_available is None:
        try:
            importlib.import_module("led_module")
            _led_available = True
        except Exception as error:  # ImportError or a broken optional dependency
            log.info("led_module not importable (%s); LED configs ignored in layout", error)
            _led_available = False
    return _led_available


def _clean(value) -> str:
    """Drop an inline ``# comment`` (the config loader keeps it in the value)."""
    return str(value or "").split(" #", 1)[0].split("\t#", 1)[0].strip()


def _output_channels(section: dict, gpio_len: int) -> List[int]:
    """Channels an ``[output:<name>]`` reads (as NanoleafOutput.from_config)."""
    text = _clean(section.get("channels", ""))
    if not text:
        return list(range(gpio_len))
    return [int(c) for c in text.split(",")]


def _led_channels(led: dict, extend_end: int) -> List[int]:
    """Channels an LED config displays (as LedOutput.select on the server)."""
    if (led.get("channel_configuration") or "").upper() == "EXTEND":
        return list(range(led["physical_gpio_len"], extend_end))
    return list(range(led["gpio_len"]))


def channel_layout(config_dir: str, labels: Optional[Dict[int, str]] = None) -> dict:
    """
    The show's channel layout.

    Args:
        config_dir: directory holding defaults.cfg / overrides.cfg
        labels: optional {channel: name} (relay names from the device registry)

    Returns:
        dict: gpio_len, physical / led_extend / virtual ranges ([start, end)
        or None), virtual_sources, outputs [{name, type, channels}],
        channel_labels and warnings
    """
    cm, leds, errors = configuration_manager.load_channel_config(
        config_dir, load_leds=led_module_available())
    layout = cm.channel_layout()
    hw = cm.hardware
    base = getattr(hw, "base_channel_count", hw.gpio_len)
    warnings = list(errors)

    outputs = []
    for led in leds:
        outputs.append({"name": "led:%s" % led["name"], "type": "led",
                        "channels": _led_channels(led, base)})
    for section in cm.outputs:
        name = section.get("name", "?")
        try:
            channels = _output_channels(section, hw.gpio_len)
        except ValueError:
            warnings.append(f"output {name}: channels is not a list of numbers")
            channels = []
        out_of_range = [c for c in channels if not 0 <= c < hw.gpio_len]
        if out_of_range:
            warnings.append(f"output {name}: channels {out_of_range} are not show channels "
                            f"(0-{hw.gpio_len - 1}); those segments stay dark")
        outputs.append({"name": name, "type": _clean(section.get("type", "")).lower(),
                        "channels": channels})

    layout.update({
        "virtual_sources": list(getattr(hw, "virtual_channel_sources", []) or []),
        "outputs": outputs,
        "channel_labels": {str(k): v for k, v in sorted((labels or {}).items())
                           if 0 <= k < hw.gpio_len},
        "warnings": warnings,
    })
    return layout
