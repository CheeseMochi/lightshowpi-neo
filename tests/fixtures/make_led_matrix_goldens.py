"""Record reference LED matrix frames from the legacy BiblioPixel code.

Writes tests/fixtures/led_matrix_goldens.json.gz, which tests/test_led_matrix.py
compares the native port (py/led_matrix.py) against. Only needed again if the
legacy reference or the scenarios change.

It runs the legacy ``write_matrix``/``mmcm`` from py/led_bibliopixel.py on
BiblioPixel 3.4.46's own coord map, line, circle and text code, taken from its
source with ``ast`` (BiblioPixel itself doesn't install on current Python):

    pip download --no-deps --no-binary :all: BiblioPixel==3.4.46 -d /tmp/bp
    tar xzf /tmp/bp/BiblioPixel-3.4.46.tar.gz -C /tmp/bp
    SYNCHRONIZED_LIGHTS_HOME=$(pwd) python tests/fixtures/make_led_matrix_goldens.py \\
        /tmp/bp/BiblioPixel-3.4.46
"""
import ast
import base64
import gzip
import json
import os
import random
import sys
import types

import numpy

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "py"))

import led_colors  # noqa: E402  (already verified identical to BiblioPixel's colours)

OUT = os.path.join(HERE, "led_matrix_goldens.json.gz")


def load_defs(path, names, namespace):
    """Exec the named top-level functions/assignments of a source file into namespace."""
    tree = ast.parse(open(path).read())
    wanted = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            wanted.append(node)
        elif isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in names for t in node.targets):
            wanted.append(node)
    exec(compile(ast.Module(body=wanted, type_ignores=[]), path, "exec"), namespace)
    return namespace


def load_methods(path, cls, names, namespace):
    """Exec the named methods of class ``cls`` as plain functions into namespace."""
    tree = ast.parse(open(path).read())
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            body = [n for n in node.body if isinstance(n, ast.FunctionDef) and n.name in names]
            exec(compile(ast.Module(body=body, type_ignores=[]), path, "exec"), namespace)
    return namespace


def bibliopixel(src):
    bp = os.path.join(src, "bibliopixel")
    ns = {}
    load_defs(os.path.join(bp, "layout/geometry/rotation.py"), {"rotate_and_flip"}, ns)
    load_defs(os.path.join(bp, "layout/geometry/matrix.py"), {"make_matrix_coord_map"}, ns)
    fonts_ns = load_defs(os.path.join(bp, "layout/font.py"), {"TINYFONT"}, {})
    ns["fonts"] = {"6x4": {"data": fonts_ns["TINYFONT"], "height": 6, "bounds": (0, 255),
                           "undef": [0x1f, 0x1f, 0x1f], "sep": 1}}
    md = {"COLORS": types.SimpleNamespace(Off=(0, 0, 0)), "log": None,
          "font": types.SimpleNamespace(default_font="6x4"),
          "math": __import__("math")}
    load_defs(os.path.join(bp, "layout/matrix_drawing.py"),
              {"bresenham_line", "draw_circle", "draw_char", "draw_text", "fill_rect",
               "_draw_fast_vline", "draw_line"}, md)
    ns["md"] = md
    ns["show_image"] = load_defs(os.path.join(bp, "util/image/load_image.py"), {"show_image"},
                                 {"COLORS": md["COLORS"],
                                  "color_scale": led_colors.color_scale})["show_image"]
    return ns


class FakeMatrix(object):
    """BiblioPixel's Matrix layout, reduced to what LightShowPi's patterns call."""

    def __init__(self, bp, width, height, serpentine, rotation, vert_flip):
        self.bp = bp
        self.n = width * height
        self.colors = [(0, 0, 0)] * self.n
        self.coord_map = bp["make_matrix_coord_map"](width, height, serpentine=serpentine,
                                                     rotation=rotation, y_flip=vert_flip)
        self.width, self.height = (height, width) if rotation in (90, 270) else (width, height)
        self.frames = []

    def set(self, x, y, color=None):  # Matrix._setColor -> __setNormal
        try:
            pixel = self.coord_map[y][x]
            self.colors[pixel] = tuple(color or (0, 0, 0))
        except IndexError:
            pass

    def all_off(self):
        self.colors = [(0, 0, 0)] * self.n

    def push_to_driver(self):
        self.frames.append(bytes(numpy.array(self.colors, dtype=numpy.uint8).flatten()))

    def drawLine(self, x0, y0, x1, y1, color=None):
        self.bp["md"]["bresenham_line"](self.set, x0, y0, x1, y1, color, None)

    def drawCircle(self, x0, y0, r, color=None):
        self.bp["md"]["draw_circle"](self.set, x0, y0, r, color)

    def drawText(self, text, x=0, y=0, color=None, bg=(0, 0, 0), font="6x4", font_scale=1):
        self.bp["md"]["draw_text"](self.bp["fonts"], self.set, text, self.width, self.height,
                                   x, y, color, bg, False, font, font_scale)


def legacy_led(bp, cfg):
    """A stub legacy Led with write_matrix/mmcm from led_bibliopixel.py."""
    from PIL import Image, ImageChops, ImageEnhance, ImageSequence

    color_map = [list(led_colors.hue2rgb(c)) for c in range(256)]
    image_mod = types.SimpleNamespace(
        showImage=lambda layout, path, img: (layout.all_off(),
                                             bp["show_image"](layout.set, layout.width,
                                                              layout.height, "", img)))
    ns = {"colors": types.SimpleNamespace(hue_helper=led_colors.hue_helper, Off=(0, 0, 0)),
          "scale": led_colors.color_scale, "color_map": color_map, "image": image_mod,
          "ImageChops": ImageChops, "ImageEnhance": ImageEnhance,
          "make_matrix_coord_map": bp["make_matrix_coord_map"]}
    load_methods(os.path.join(REPO, "py/led_bibliopixel.py"), "Led",
                 {"write_matrix", "mmcm"}, ns)

    led = types.SimpleNamespace(**cfg)
    led.led_config = types.SimpleNamespace(**cfg)
    w, h = cfg["matrix_width"], cfg["matrix_height"]
    led.serpentine, led.rotation, led.rotation_180, led.vert_flip = True, 90, 270, True
    led.led = FakeMatrix(bp, w, h, True, 90, True)
    led.update_skip = led.skip = 0
    led.p_type, led.p_num, led.beats, led.last_type = None, 0, 0, ""
    led.max_brightness = 1.0
    count = cfg["led_count"]
    led.rgb = [color_map[int((float(x) / count) * 255)] for x in range(count)]
    led.drops = [[0 for _ in range(h)] for _ in range(w)]
    led._len = (w * 2) + (h * 2) - 2
    led._step, led._bstep = 1, 0
    mid = int(h / 2)
    led.midxa, led.midxb = (mid, mid - 1) if float(mid) == h / 2 else (mid, mid)
    mid = int(w / 2)
    led.midya, led.midyb = (mid, mid - 1) if float(mid) == w / 2 else (mid, mid)
    led.images = []
    if "IMAGE" in cfg["matrix_pattern_type"]:
        for frame in ImageSequence.Iterator(Image.open(cfg["image_path"])):
            rgba = Image.new("RGBA", frame.size)
            rgba.paste(frame)
            led.images.append(rgba)
        led.base_image = Image.new("RGBA", led.images[0].size)
    led.write_matrix = types.MethodType(ns["write_matrix"], led)
    led.mmcm = types.MethodType(ns["mmcm"], led)
    return led


def scenario(bp, name, width, height, patterns, channels, pins, frames=12, beats=5,
             seed=0, banner_text="   No Worky!", image_path=""):
    rng = random.Random(seed)
    inputs = []
    for i in range(frames):
        levels = [round(rng.random(), 3) for _ in range(pins)]
        if i % 5 == 0:
            levels[0] = 1.0  # full-length bars and the clip edge
        inputs.append(levels)
    # led_count: channels the show feeds the LEDs (gpio_len under MIRROR)
    cfg = dict(matrix_width=width, matrix_height=height, matrix_pattern_type=patterns,
               led_channel_count=channels, led_count=pins, beats=beats, banner_text=banner_text,
               image_path=image_path)
    led = legacy_led(bp, cfg)
    for levels in inputs:
        led.write_matrix(numpy.array(levels))
    return {"config": cfg, "inputs": inputs,
            "frames": [base64.b64encode(f).decode() for f in led.led.frames]}


def main(src):
    bp = bibliopixel(src)
    coord_maps = []
    for dx, dy in ((83, 6), (5, 3), (4, 4), (3, 7)):
        for rotation in (0, 90, 180, 270):
            for serpentine in (True, False):
                for flip in (True, False):
                    cm = bp["make_matrix_coord_map"](dx, dy, serpentine=serpentine,
                                                     rotation=rotation, y_flip=flip)
                    coord_maps.append({"args": [dx, dy, serpentine, rotation, flip],
                                       "map": [list(r) for r in cm]})
    gif = os.path.join(REPO, "config/resources/16x16star8chan.gif")
    scenarios = {
        # production: 83 x 6 zigzag, SBARS, MIRROR feeds gpio_len = 7 channels
        "production_sbars": scenario(bp, "p", 83, 6, ["SBARS"], 6, 7, frames=20),
    }
    for pattern in ("SBARS", "MBARS", "PINWHEEL", "CBARS", "CIRCLES", "BANNER"):
        scenarios[f"83x6_{pattern.lower()}"] = scenario(bp, pattern, 83, 6, [pattern], 6, 6,
                                                        seed=1, banner_text="No Worky!")
        scenarios[f"16x16_{pattern.lower()}"] = scenario(bp, pattern, 16, 16, [pattern], 8, 8,
                                                         seed=2, banner_text="Hello!")
        scenarios[f"7x5_{pattern.lower()}"] = scenario(bp, pattern, 7, 5, [pattern], 5, 5,
                                                       seed=3, banner_text="ab")
    scenarios["16x16_image"] = scenario(bp, "IMAGE", 16, 16, ["IMAGE"], 8, 8, seed=4,
                                        image_path=gif)
    scenarios["16x16_rotation"] = scenario(bp, "rot", 16, 16,
                                           ["SBARS", "PINWHEEL", "BANNER", "MBARS"], 8, 8,
                                           frames=60, beats=1, seed=5, banner_text="Hi")
    for s in scenarios.values():
        if s["config"]["image_path"]:
            s["config"]["image_path"] = os.path.relpath(s["config"]["image_path"], REPO)
    with gzip.open(OUT, "wt") as f:
        json.dump({"source": "BiblioPixel 3.4.46 + led_bibliopixel.Led.write_matrix",
                   "coord_maps": coord_maps, "scenarios": scenarios}, f)
    print(f"wrote {OUT}: {len(coord_maps)} coord maps, {len(scenarios)} scenarios")


if __name__ == "__main__":
    main(sys.argv[1])
