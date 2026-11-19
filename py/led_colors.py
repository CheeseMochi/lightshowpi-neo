"""
Colour helpers for the LED outputs, replacing the parts of ``bibliopixel.colors``
that LightShowPi used.

BiblioPixel is unmaintained and doesn't install on Python 3.8+, so these few
helpers are ported here. The hue table and ``color_scale`` reproduce
BiblioPixel 3.4.46 exactly (``hsv2rgb_rainbow``, itself from FastLED), so LED
colours look the same as they did on the original LightShowPi.

Ported from BiblioPixel (https://github.com/ManiacalLabs/BiblioPixel),
MIT licence, Copyright (c) 2016 Maniacal Labs, LLC / Adam M. Haile.
"""

# Named colours used by led_module and led_color_maps (BiblioPixel's classic
# names and values).
Off = (0, 0, 0)
Blue = (0, 0, 255)
BlueViolet = (138, 43, 226)
Brown = (165, 42, 42)
Chocolate = (210, 105, 30)
Cyan = (0, 255, 255)
DeepPink = (255, 20, 147)
Fuchsia = (255, 0, 255)
Gold = (255, 215, 0)
Green = (0, 255, 0)
Khaki = (240, 230, 140)
LightPink = (255, 182, 193)
Lime = (0, 255, 0)
OrangeRed = (255, 69, 0)
Orchid = (218, 112, 214)
Plum = (221, 160, 221)
Red = (255, 0, 0)
RoyalBlue = (65, 105, 225)
SaddleBrown = (139, 69, 19)
Sienna = (160, 82, 45)
Silver = (192, 192, 192)
Snow = (255, 250, 250)
White = (255, 255, 255)
Yellow = (255, 255, 0)


def color_scale(color, level):
    """Scale an RGB tuple by ``level`` (0-256): ``int(c * level) >> 8`` per channel."""
    return tuple(int(c * level) >> 8 for c in color)


def _scale8_video(i, scale):
    if i == 0:
        return 0
    return ((i * scale) >> 8) + (1 if scale else 0)


def hsv2rgb_rainbow(hsv):
    """HSV (each 0-255) to RGB with an even visual spread of hues (FastLED's rainbow)."""
    h, s, v = hsv
    offset8 = (h & 0x1F) * 8
    third = (offset8 * (256 // 3)) >> 8
    twothirds = third << 1

    if not h & 0x80:
        if not h & 0x40:
            r, g, b = (255 - third, third, 0) if not h & 0x20 else (171, 85 + third, 0)
        else:
            r, g, b = (171 - twothirds, 171 + third, 0) if not h & 0x20 else (0, 255 - third, third)
    else:
        if not h & 0x40:
            r, g, b = (0, 171 - twothirds, 85 + twothirds) if not h & 0x20 else (third, 0, 255 - third)
        else:
            r, g, b = (85 + third, 0, 171 - third) if not h & 0x20 else (171 + third, 0, 85 - third)

    if s != 255:
        r, g, b = (_scale8_video(c, s) for c in (r, g, b))
        floor = ((255 - s) * (255 - s)) >> 8
        r, g, b = r + floor, g + floor, b + floor

    if v != 255:
        v = _scale8_video(v, v)
        r, g, b = (_scale8_video(c, v) for c in (r, g, b))

    return (r, g, b)


HUE_RAINBOW = [hsv2rgb_rainbow((hue, 255, 255)) for hue in range(256)]


def hue2rgb(hue):
    """Fully saturated, full brightness RGB for a hue of 0-255."""
    return HUE_RAINBOW[int(hue)]


def hue_helper(pos, length, cycle_step):
    """Hue for position ``pos`` of ``length``, rotated by ``cycle_step``."""
    return hue2rgb(((pos * 255 // length) + cycle_step) % 255)
