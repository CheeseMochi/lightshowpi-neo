#!/usr/bin/env python
#
# Licensed under the BSD license.  See full license in LICENSE file.
# http://www.lightshowpi.org/
"""
LED matrices over sACN, natively (no BiblioPixel).

A MATRIX config is a pixel strip folded into ``matrix_height`` rows of
``matrix_width`` pixels (zigzag/serpentine), e.g. 83 x 6 on an ESPixelStick.
``SacnMatrixLed`` draws the matrix patterns (SBARS, MBARS, CBARS, PINWHEEL,
CIRCLES, BANNER, IMAGE) and sends the frame with ``driver_sacn.SacnSender``.

Output matches the BiblioPixel version pixel for pixel
(``tests/test_led_matrix.py`` checks frames recorded from the legacy code):

* Same layout as legacy LightShowPi, which always built the matrix with
  ``serpentine=True, rotation=90, vert_flip=True`` (BANNER: rotation 270).
  Rotation 90 swaps the axes, so drawing coordinates are ``(row, column)``:
  x runs over ``matrix_height`` and y over ``matrix_width``.
* Same pixel addressing: ``coord_map[y][x]``, positions past the end are
  ignored and negative ones wrap like Python indexing, as BiblioPixel did.
* Same Bresenham line and Adafruit circle algorithms, same 6x4 font.

Differences: levels are clipped to 0-1, and inputs that crashed the legacy
code (more channels than images, colours or rows) are skipped instead.
"""

import logging

import numpy

import led_colors as colors
import led_font
from driver_sacn import SacnSender
from led_module import PixelStrip, color_map, scale

log = logging.getLogger(__name__)

PATTERNS = ("SBARS", "MBARS", "IMAGE", "PINWHEEL", "CBARS", "CIRCLES", "BANNER")

# Legacy LightShowPi layout (led_bibliopixel.Led defaults)
SERPENTINE = True
ROTATION = 90
VERT_FLIP = True


def rotate_and_flip(coord_map, rotation, flip):
    """Rotate a coordinate map by ``rotation`` degrees, then optionally flip rows."""
    for _ in range((-rotation % 360) // 90):
        coord_map = list(zip(*coord_map[::-1]))
    if flip:
        coord_map = coord_map[::-1]
    return coord_map


def make_matrix_coord_map(dx, dy, serpentine=True, rotation=0, y_flip=False):
    """``coord_map[y][x]`` -> strip index, as BiblioPixel's helper of the same name."""
    result = []
    for y in range(dy):
        if not serpentine or y % 2 == 0:
            result.append([(dx * y) + x for x in range(dx)])
        else:
            result.append([dx * (y + 1) - 1 - x for x in range(dx)])
    return rotate_and_flip(result, rotation, y_flip)


class PixelMatrix(object):
    """A ``PixelStrip`` addressed as a matrix, with the drawing calls the patterns use."""

    def __init__(self, width, height, send, serpentine=SERPENTINE,
                 rotation=ROTATION, vert_flip=VERT_FLIP):
        self.matrix_width = width
        self.matrix_height = height
        self.serpentine = serpentine
        self.vert_flip = vert_flip
        self.strip = PixelStrip(width * height, send)
        self.set_rotation(rotation, serpentine)
        # Drawing space: rotation 90/270 swaps the axes (fixed at construction,
        # as BiblioPixel did, even when a later map uses another rotation)
        if rotation in (90, 270):
            self.width, self.height = height, width
        else:
            self.width, self.height = width, height

    def set_rotation(self, rotation, serpentine=True):
        """Rebuild the coordinate map (the legacy ``mmcm``)."""
        self.coord_map = make_matrix_coord_map(self.matrix_width, self.matrix_height,
                                               serpentine=serpentine, rotation=rotation,
                                               y_flip=self.vert_flip)

    def set(self, x, y, color=None):
        """Set pixel (x, y). Out of range is ignored; negatives wrap (BiblioPixel)."""
        try:
            pixel = self.coord_map[y][x]
        except IndexError:
            return
        self.strip.colors[pixel] = color or (0, 0, 0)

    def all_off(self):
        self.strip.all_off()

    def set_brightness(self, brightness):
        self.strip.set_brightness(brightness)

    def push_to_driver(self):
        self.strip.push_to_driver()

    def draw_line(self, x0, y0, x1, y1, color):
        """Bresenham line, endpoints included."""
        steep = abs(y1 - y0) > abs(x1 - x0)
        if steep:
            x0, y0 = y0, x0
            x1, y1 = y1, x1
        if x0 > x1:
            x0, x1 = x1, x0
            y0, y1 = y1, y0
        dx = x1 - x0
        dy = abs(y1 - y0)
        err = dx / 2
        ystep = 1 if y0 < y1 else -1
        for x in range(x0, x1 + 1):
            if steep:
                self.set(y0, x, color)
            else:
                self.set(x, y0, color)
            err -= dy
            if err < 0:
                y0 += ystep
                err += dx

    def draw_circle(self, x0, y0, r, color):
        """Circle outline (midpoint algorithm, as Adafruit GFX)."""
        f = 1 - r
        ddf_x = 1
        ddf_y = -2 * r
        x = 0
        y = r
        self.set(x0, y0 + r, color)
        self.set(x0, y0 - r, color)
        self.set(x0 + r, y0, color)
        self.set(x0 - r, y0, color)
        while x < y:
            if f >= 0:
                y -= 1
                ddf_y += 2
                f += ddf_y
            x += 1
            ddf_x += 2
            f += ddf_x
            for px, py in ((x0 + x, y0 + y), (x0 - x, y0 + y), (x0 + x, y0 - y),
                           (x0 - x, y0 - y), (x0 + y, y0 + x), (x0 - y, y0 + x),
                           (x0 + y, y0 - x), (x0 - y, y0 - x)):
                self.set(px, py, color)

    def draw_char(self, x, y, char, color, bg):
        """Draw one 6x4 glyph at (x, y); returns the columns used."""
        code = ord(char)
        glyph = led_font.GLYPHS[code] if code < len(led_font.GLYPHS) else led_font.UNDEF
        fw = len(glyph)
        for i in range(fw + led_font.SEP):
            x_pos = x + i
            if max(0, 1 - fw) <= x_pos < self.width:
                line = glyph[i] if i < fw else 0
                for j in range(led_font.HEIGHT):
                    y_pos = y + j
                    if y_pos < self.height and y_pos + led_font.HEIGHT - 1 >= 0:
                        if line & 0x1:
                            self.set(x_pos, y_pos, color)
                        elif bg is not None and tuple(bg) != tuple(color):
                            self.set(x_pos, y_pos, bg)
                    line >>= 1
        return fw + led_font.SEP

    def draw_text(self, text, x, y, color, bg=colors.Off):
        """Draw a line of text from (x, y), stopping at the right edge."""
        for char in text:
            if char == '\n':
                y += led_font.HEIGHT
                x = 0
            elif char != '\r':
                x += self.draw_char(x, y, char, color, bg)
                if x >= self.width:
                    break

    def show_image(self, img):
        """Draw a PIL RGBA image at (0, 0), alpha scaling each pixel."""
        self.all_off()
        w = min(self.width, img.size[0])
        h = min(self.height, img.size[1])
        for x in range(w):
            for y in range(h):
                rgba = img.getpixel((x, y))
                r, g, b = rgba[:3]
                a = rgba[3] if len(rgba) == 4 else 255
                self.set(x, y, colors.Off if a == 0 else scale((r, g, b), a))


class SacnMatrixLed(object):
    """A pixel matrix on an sACN receiver, driven by show channel levels."""

    def __init__(self, led_config):
        self.led_config = led_config
        self.write_all = self.write_matrix
        self.all_set_on = False
        self.test = False

        channels = self.led_config.led_count
        self.leds = numpy.zeros(channels)
        self.pattern_color = self.led_config.pattern_color
        self.color_map = [list(c) for c in color_map]
        self.rgb = [self.color_map[int((float(x) / channels) * 255)] for x in range(channels)]
        self.skip = self.led_config.update_throttle
        self.update_skip = self.skip
        self.max_brightness = self.led_config.max_brightness / 100.0

        self.w = self.led_config.matrix_width
        self.h = self.led_config.matrix_height
        self.led_count = self.w * self.h
        self.patterns = self._check_patterns(list(self.led_config.matrix_pattern_type))

        self.sender = SacnSender(self.led_config.sacn_address,
                                 port=self.led_config.sacn_port,
                                 universe=self.led_config.universe_start,
                                 universe_boundary=self.led_config.universe_boundary,
                                 broadcast=self.led_config.sacn_broadcast)
        self.led = PixelMatrix(self.w, self.h, self.sender.send)

        # pattern state (legacy matrix_setup)
        self.p_num = 0
        self.p_type = None
        self.last_type = ""
        self.beats = 0
        self.drops = [[0] * self.h for _ in range(self.w)]
        self._len = (self.w * 2) + (self.h * 2) - 2
        self._step = 1
        self._bstep = 0
        self.midxa, self.midxb = self._centre(self.h)
        self.midya, self.midyb = self._centre(self.w)
        self.images = []
        if "IMAGE" in self.patterns:
            self._load_images()

        cmap = self.led_config.pattern_color_map
        if cmap == 'MAP1A':
            self.color_map[255] = self.pattern_color
        if cmap == 'MAP2A':
            self.color_map[0] = self.pattern_color

        self.led.set_brightness(int(self.max_brightness * 255))
        log.info(f"LED matrix: {self.w}x{self.h} ({self.led_count} pixels) over sACN to "
                 f"{self.led_config.sacn_address}, universe {self.led_config.universe_start}+, "
                 f"patterns {','.join(self.patterns)}")

    @staticmethod
    def _centre(n):
        """The centre pixel(s) of n: (a, b), equal when n is odd."""
        mid = int(n / 2)
        return (mid, mid - 1) if n % 2 == 0 else (mid, mid)

    @staticmethod
    def _check_patterns(names):
        names = [n.strip() for n in names if n.strip()]
        unknown = [n for n in names if n not in PATTERNS]
        if unknown:
            log.warning(f"Unknown matrix_pattern_type {','.join(unknown)} ignored "
                        f"(known: {', '.join(PATTERNS)})")
        names = [n for n in names if n in PATTERNS]
        return names or ["SBARS"]

    def _load_images(self):
        """Load image_path's frames for IMAGE; on failure, drop IMAGE and carry on."""
        try:
            from PIL import Image, ImageSequence
            for frame in ImageSequence.Iterator(Image.open(self.led_config.image_path)):
                rgba = Image.new("RGBA", frame.size)
                rgba.paste(frame)
                self.images.append(rgba)
            self.base_image = Image.new("RGBA", self.images[0].size)
        except Exception as e:  # missing Pillow, bad path, bad image
            log.error(f"IMAGE matrix pattern disabled: can't load "
                      f"{self.led_config.image_path!r} ({e})")
            self.images = []
            self.patterns = [p for p in self.patterns if p != "IMAGE"] or ["SBARS"]

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
        """Set strip pixel ``pin`` to hue ``color`` (0-255) and send."""
        self.led.strip.set(pin, scale(self.color_map[color], color))
        self.led.push_to_driver()

    def _select_pattern(self, pin_list):
        """Pick this frame's pattern; several patterns rotate on bass "beats"."""
        if len(self.patterns) == 1:
            return self.patterns[0]
        n = len(pin_list)
        for pin in range(n):
            self.beats += pin_list[pin] * (n / (pin + 1)) * 0.002
        if self.beats > self.led_config.beats and self._bstep == 0:
            self.beats = 0
            self.p_num = (self.p_num + 1) % len(self.patterns)
        return self.patterns[self.p_num]

    def _set_map(self, p_type):
        """BANNER draws on a map rotated 180 degrees from the others (legacy mmcm)."""
        if self.last_type == p_type:
            return
        self.last_type = p_type
        if p_type == 'BANNER':
            self.led.set_rotation((ROTATION + 180) % 360, SERPENTINE)
        else:
            self.led.set_rotation(ROTATION, True)

    def write_matrix(self, pin_list):
        """Draw one frame of channel levels (0-1) and send it."""
        if self.update_skip != 0:
            self.update_skip -= 1
            if self.update_skip >= 0:
                return

        pin_list = numpy.clip(numpy.asarray(pin_list, dtype=float), 0.0, 1.0)
        self.p_type = self._select_pattern(pin_list)
        self._set_map(self.p_type)
        self.led.all_off()
        if len(pin_list):
            getattr(self, "_draw_" + self.p_type.lower())(pin_list)
        self.led.push_to_driver()
        self.update_skip = self.skip

    # --- patterns (ported from led_bibliopixel.Led.write_matrix) -------------

    def _draw_sbars(self, pin_list):
        """Spectrum bars: one row per channel, length = level, hue along the bar."""
        h, w = self.h, self.w
        for y in range(h):
            y_ind = int((float(len(pin_list)) / h) * y)
            for x_cord in range(int(pin_list[y_ind] * float(w))):
                rgb = self.color_map[int(255.0 * float(x_cord) / float(w))]
                self.led.set(y_ind, x_cord, rgb)

    def _draw_mbars(self, pin_list):
        """Waterfall: each frame's levels scroll along the matrix width."""
        h, w = self.h, self.w
        self.drops.append([int(x * 255) for x in pin_list])
        for y_cord in range(w):
            row = self.drops[y_cord]
            for x in range(h):
                x_ind = int((float(len(pin_list)) / h) * x)
                level = row[x_ind] if x_ind < len(row) else 0
                if level > 64:
                    rgb = scale(self.color_map[255 - level], int(level * 0.5))
                    self.led.set(x, w - 1 - y_cord, rgb)
        del self.drops[0]

    def _draw_image(self, pin_list):
        """Blend the image's frames, one per channel, for channels above 0.55."""
        from PIL import ImageChops, ImageEnhance
        complete_image = self.base_image
        for pin in range(min(len(pin_list), len(self.images))):
            if pin_list[pin] > 0.55:
                complete_image = ImageChops.add_modulo(
                    complete_image,
                    ImageEnhance.Brightness(self.images[pin]).enhance(pin_list[pin]))
        self.led.show_image(ImageEnhance.Brightness(complete_image)
                            .enhance(self.max_brightness * 0.5))

    def _draw_pinwheel(self, pin_list):
        """Rainbow spokes from the centre, spinning faster with more bass."""
        h, w = self.h, self.w
        amt = 0
        for pin in range(len(pin_list)):
            amt += pin_list[pin] * (len(pin_list) / (pin + 1)) * 0.25
        amt = int(amt)

        pos = 0
        spokes = ([(self.midxa, self.midya, x, 0) for x in range(h)]
                  + [(self.midxb, self.midyb, h - 1, y) for y in range(w)]
                  + [(self.midxb, self.midyb, x, w - 1) for x in range(h - 1, -1, -1)]
                  + [(self.midxa, self.midya, 0, y) for y in range(w - 1, -1, -1)])
        for x0, y0, x1, y1 in spokes:
            self.led.draw_line(x0, y0, x1, y1, colors.hue_helper(pos, self._len, self._step))
            pos += 1

        self._step += amt
        if self._step >= 255:
            self._step = 0

    def _draw_cbars(self, pin_list):
        """Bars across the rows, growing from the middle, one channel per column band."""
        w = self.w
        channels = self.led_config.led_channel_count
        for y in range(w):
            index = int((y / float(w)) * float(channels))
            if index >= len(pin_list):
                continue
            level = pin_list[index]
            brightness = int(255 * level)
            rgb = scale(self.color_map[brightness], brightness)
            mlvl = int(level * self.midxa)
            self.led.draw_line(self.midxa - mlvl, y, self.midxb + mlvl, y, rgb)

    def _draw_circles(self, pin_list):
        """Concentric rings, one per channel, brightness = level."""
        w = self.w
        channels = self.led_config.led_channel_count
        for pin in range(min(channels, len(pin_list), len(self.rgb))):
            c = scale(self.rgb[pin], int(pin_list[pin] * 255))
            self.led.draw_circle(self.midxa, self.midyb, int(pin * ((w / 2) / channels)), c)

    def _draw_banner(self, pin_list):
        """Scroll banner_text, coloured by the loudest channel."""
        loudest = int(numpy.argmax(pin_list))
        rgb = self.rgb[min(loudest, len(self.rgb) - 1)]
        text = self.led_config.banner_text[int(self._bstep):int(self._bstep) + 4]
        self.led.draw_text(text, 1, 1, rgb, colors.Off)
        self._bstep += 0.1
        if self._bstep >= len(self.led_config.banner_text):
            self._bstep = 0
