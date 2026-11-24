#!/usr/bin/env python
#
# Licensed under the BSD license.  See full license in LICENSE file.
# http://www.lightshowpi.org/
#
# Author: Tom Enos (tomslick.ca@gmail.com)
# Author: Ken B
"""
LED strips for LightShowPi: show channel levels drawn as pixel patterns.

``Led(led_config)`` returns the object that drives one LED config:

* ``led_connection = SACN`` with ``led_configuration = STRIP`` (the usual
  setup, e.g. an ESPixelStick) runs natively here: numpy pixel buffer,
  ``led_colors`` for colours and ``driver_sacn.SacnSender`` on the wire.
  No BiblioPixel needed.
* ``led_connection = SACN`` with ``led_configuration = MATRIX`` (a strip
  folded zigzag into rows) runs natively in ``led_matrix.py``.
* SPI and SERIAL connections still use the BiblioPixel code in
  ``led_bibliopixel.py``. BiblioPixel is unmaintained and
  isn't installable on the Python versions Neo runs, so those configs raise
  ``ImportError`` with an explanation instead.

Pattern and colour behaviour matches the BiblioPixel version: same hue table,
same ``color_scale`` maths, same brightness scaling (``max_brightness`` as a
0-255 level applied as ``int(level * c)`` per channel), and colour order left
to the receiver, as before for sACN.
"""

import logging

import numpy

import led_colors as colors
from driver_sacn import SacnSender
from led_color_maps import lspi_color_maps

log = logging.getLogger(__name__)

color_map = [list(colors.hue2rgb(c)) for c in range(256)]
scale = colors.color_scale
int_map = [colors.Green for _ in range(0, 80)] + \
          [colors.Yellow for _ in range(80, 160)] + \
          [colors.Red for _ in range(160, 256)]


def Led(led_config):
    """Create the LED driver for ``led_config`` (see the module docstring)."""
    if led_config.led_connection == "SACN" and led_config.led_configuration == "STRIP":
        return SacnStripLed(led_config)
    if led_config.led_connection == "SACN" and led_config.led_configuration == "MATRIX":
        import led_matrix
        return led_matrix.SacnMatrixLed(led_config)
    try:
        import led_bibliopixel
    except ImportError as e:
        raise ImportError(
            f"LED config uses led_connection = {led_config.led_connection}, "
            f"led_configuration = {led_config.led_configuration}, which still needs "
            f"BiblioPixel ({e}). Only SACN (STRIP or MATRIX) runs without it.") from e
    return led_bibliopixel.Led(led_config)


class PixelStrip(object):
    """
    An RGB pixel buffer with the drawing calls the patterns use.

    Mirrors the BiblioPixel ``Strip`` calls ``led_module`` relied on:
    ``set``, ``fill`` (inclusive end; ``end < 0`` means the last pixel),
    ``all_off``, ``set_brightness`` and ``push_to_driver``.
    """

    def __init__(self, num, send):
        self.num = num
        self.colors = numpy.zeros((num, 3), dtype=numpy.int64)
        self.brightness = 255
        self._send = send

    def set(self, pixel, color):
        """Set one pixel; positions outside the strip are ignored."""
        if 0 <= pixel < self.num:
            self.colors[pixel] = color

    def fill(self, color, start=0, end=-1):
        """Set pixels ``start`` to ``end`` inclusive."""
        start = max(start, 0)
        if end < 0 or end >= self.num:
            end = self.num - 1
        if start <= end:
            self.colors[start:end + 1] = color

    def all_off(self):
        self.colors[:] = 0

    def set_brightness(self, brightness):
        """Overall brightness, 0-255."""
        self.brightness = brightness

    def frame(self):
        """The RGB bytes to send: each channel scaled by brightness, truncated."""
        level = self.brightness / 255.0
        scaled = (self.colors.clip(0, 255) * level).astype(numpy.uint8)
        return scaled.tobytes()

    def push_to_driver(self):
        self._send(self.frame())


class SacnStripLed(object):
    """A pixel strip on an sACN receiver, driven by show channel levels."""

    def __init__(self, led_config):
        self.led_config = led_config
        self.write_all = self.write_full
        self.all_set_on = False
        self.test = False

        channels = self.led_config.led_count
        self.leds = numpy.zeros(channels)

        self.per_channel = self.led_config.per_channel
        self.pattern_color = self.led_config.pattern_color
        self.pattern_color_map = self.led_config.pattern_color_map

        self.color_map = [list(c) for c in color_map]
        self.last = channels - 1
        self.rgb = [self.color_map[int((float(x) / (self.last + 1)) * 255)]
                    for x in range(channels)]
        self.skip = self.led_config.update_throttle
        self.update_skip = self.skip
        self.max_brightness = self.led_config.max_brightness / 100.0

        if self.led_config.custom_per_channel:
            self.led_count = sum(self.led_config.custom_per_channel)
        else:
            self.led_count = channels * self.led_config.per_channel

        self.sender = SacnSender(self.led_config.sacn_address,
                                 port=self.led_config.sacn_port,
                                 universe=self.led_config.universe_start,
                                 universe_boundary=self.led_config.universe_boundary,
                                 broadcast=self.led_config.sacn_broadcast)
        self.led = PixelStrip(self.led_count, self.sender.send)

        if self.pattern_color_map == 'MAP1A':
            self.color_map[255] = self.pattern_color
            self.pattern_color_map = 'MAP1'
        if self.pattern_color_map == 'MAP2A':
            self.color_map[0] = self.pattern_color
            self.pattern_color_map = 'MAP2'

        self.led.set_brightness(int(self.max_brightness * 255))
        log.info(f"LED strip: {self.led_count} pixels over sACN to "
                 f"{self.led_config.sacn_address}, universe {self.led_config.universe_start}+")

    def exit_function(self):
        if not self.all_set_on:
            self.all_leds_off()

    def all_leds_off(self):
        self.leds = numpy.zeros(self.led_config.led_count)
        self.led.all_off()
        self.led.push_to_driver()

    def all_leds_on(self):
        self.leds = numpy.ones(self.led_config.led_count)
        self.update_skip = 0
        self.write_all(self.leds)

    def write_leds(self, pin, value):
        self.leds[pin] = value
        self.update_skip = 0
        self.write_all(self.leds)

    def write(self, pin, color):
        self.led.set(pin, scale(self.color_map[color], color))
        self.led.push_to_driver()

    def channel_color(self, pin, level, brightness):
        """The RGB colour for one channel at ``level`` (0-1), per ``pattern_color_map``."""
        cmap = self.pattern_color_map
        if cmap == 'MONO':
            return (int(level * self.pattern_color[0]),
                    int(level * self.pattern_color[1]),
                    int(level * self.pattern_color[2]))
        if cmap == 'FREQ1':
            rgb = self.rgb[pin]
            return (int(rgb[0] * level), int(rgb[1] * level), int(rgb[2] * level))
        if cmap == 'FREQ1A':
            rgb = self.rgb[pin] if brightness < 255 else self.pattern_color
            return (int(rgb[0] * level), int(rgb[1] * level), int(rgb[2] * level))
        if cmap == 'MAP1':
            return scale(self.color_map[brightness], brightness)
        if cmap == 'MAP2':
            return scale(self.color_map[255 - brightness], brightness)
        if cmap in lspi_color_maps.map:
            return scale(lspi_color_maps.map[cmap][255 - brightness], brightness)
        return (brightness, brightness, brightness)

    def write_full(self, pin_list):
        """Draw one frame of channel levels (0-1) and send it."""
        if self.update_skip != 0:
            self.update_skip -= 1
            if self.update_skip >= 0:
                return

        self.led.all_off()

        pin_list = numpy.clip(numpy.asarray(pin_list, dtype=float), 0.0, 1.0)
        brightnesses = (pin_list * 255).astype(int)
        half_channels = self.led_config.per_channel / 2
        midl = int(half_channels)
        custom = self.led_config.custom_per_channel
        pattern = self.led_config.pattern_type

        for pin, (level, brightness) in enumerate(zip(pin_list, brightnesses)):
            if custom:
                if pin >= len(custom):
                    break
                sled = sum(custom[0:pin])
                midl = int(custom[pin] / 2)
            else:
                sled = pin * self.per_channel

            rgb = self.channel_color(pin, level, brightness)

            if pattern == 'CBARS':
                mlvl = int(level * midl)
                self.led.fill(rgb, sled + midl - mlvl, sled + midl + mlvl)

            elif pattern == 'FULL':
                self.led.fill(rgb, sled, sled + self.led_config.per_channel - 1)

            elif pattern == 'LBARS':
                midl = int(half_channels) + sled
                for gled in range(0, int(half_channels * level)):
                    self.led.set(midl + gled, int_map[int((float(gled) / half_channels) * 255)])
                    self.led.set(midl - gled, int_map[int((float(gled) / half_channels) * 255)])

        self.led.push_to_driver()
        self.update_skip = self.skip
