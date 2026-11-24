"""Native LED matrix over sACN (py/led_matrix.py), checked against the legacy BiblioPixel code.

The goldens in fixtures/led_matrix_goldens.json.gz were recorded by
fixtures/make_led_matrix_goldens.py from led_bibliopixel.Led.write_matrix
running on BiblioPixel 3.4.46's own drawing code.
"""
import base64
import gzip
import json
import os
import time

import numpy as np
import pytest

import led_matrix
import led_module
from tests.test_led_native import fake_socket, parse_led, sent_frames  # noqa: F401  (fixture)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDENS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                       "led_matrix_goldens.json.gz")

with gzip.open(GOLDENS, "rt") as _f:
    GOLDEN = json.load(_f)


def matrix_led(tmp_path, led_count=None, **overrides):
    """A SacnMatrixLed from the test LED config with MATRIX settings.

    ``led_count`` is how many show channels feed the LEDs (gpio_len under MIRROR);
    it defaults to ``led_channel_count``.
    """
    settings = dict(led_configuration="MATRIX", led_channel_configuration="MIRROR",
                    update_throttle=0, max_brightness=100)
    settings.update(overrides)
    config = parse_led(tmp_path, **{k: str(v) for k, v in settings.items()})
    config.led_count = led_count or config.led_channel_count
    return led_module.Led(config)


def colours(led):
    """The pixel buffer before brightness, as the goldens store it."""
    return bytes(led.led.strip.colors.clip(0, 255).astype(np.uint8).flatten())


@pytest.mark.unit
class TestCoordMap:

    @pytest.mark.parametrize("case", GOLDEN["coord_maps"],
                             ids=lambda c: "x".join(map(str, c["args"])))
    def test_matches_bibliopixel(self, case):
        dx, dy, serpentine, rotation, flip = case["args"]
        got = led_matrix.make_matrix_coord_map(dx, dy, serpentine=serpentine,
                                               rotation=rotation, y_flip=flip)
        assert [list(r) for r in got] == case["map"]

    def test_zigzag_rows(self):
        assert led_matrix.make_matrix_coord_map(3, 2) == [[0, 1, 2], [5, 4, 3]]


@pytest.mark.unit
class TestPatternsMatchLegacy:

    @pytest.mark.parametrize("name", sorted(GOLDEN["scenarios"]))
    def test_frames(self, name, tmp_path, fake_socket):  # noqa: F811
        s = GOLDEN["scenarios"][name]
        cfg = s["config"]
        image_path = os.path.join(REPO, cfg["image_path"]) if cfg["image_path"] else ""
        led = matrix_led(tmp_path, matrix_width=cfg["matrix_width"],
                         matrix_height=cfg["matrix_height"],
                         matrix_pattern_type=",".join(cfg["matrix_pattern_type"]),
                         led_channel_count=cfg["led_channel_count"], beats=cfg["beats"],
                         led_count=cfg["led_count"],
                         banner_text=f"\"{cfg['banner_text']}\"", image_path=image_path)
        assert any(base64.b64decode(f).strip(b"\0") for f in s["frames"]), "empty golden"
        for i, (levels, frame) in enumerate(zip(s["inputs"], s["frames"])):
            led.write_all(np.array(levels))
            assert colours(led) == base64.b64decode(frame), f"frame {i} ({led.p_type})"


@pytest.mark.unit
class TestSacnMatrixLed:

    def test_dispatches_native_for_sacn_matrix(self, tmp_path, fake_socket):  # noqa: F811
        led = matrix_led(tmp_path, matrix_width=83, matrix_height=6, led_channel_count=6,
                         led_count=7)
        assert isinstance(led, led_matrix.SacnMatrixLed)
        assert led.led_count == 498

    def test_production_layout_sends_498_pixels(self, tmp_path, fake_socket):  # noqa: F811
        led = matrix_led(tmp_path, matrix_width=83, matrix_height=6, led_channel_count=6,
                         universe_boundary=510)
        led.write_all(np.ones(7))
        frames = sent_frames(fake_socket.instances[0])
        assert [(u, len(d)) for u, _, d in frames] == [(1, 510), (2, 510), (3, 474)]

    def test_sbars_row_zero_is_first_strip_row(self, tmp_path, fake_socket):  # noqa: F811
        """Channel 0 at full level lights one whole 83-pixel run of the strip, in hue order."""
        led = matrix_led(tmp_path, matrix_width=83, matrix_height=6, led_channel_count=6)
        led.write_all(np.array([1.0, 0, 0, 0, 0, 0, 0]))
        px = led.led.strip.colors
        lit = [i for i in range(498) if px[i].any()]
        assert len(lit) == 83 and lit == list(range(lit[0], lit[0] + 83))

    def test_max_brightness_scales_the_frame(self, tmp_path, fake_socket):  # noqa: F811
        led = matrix_led(tmp_path, max_brightness=50, matrix_width=4, matrix_height=2,
                         led_channel_count=2)
        led.write_all(np.array([1.0, 1.0]))
        _, _, data = sent_frames(fake_socket.instances[0])[-1]
        assert max(data) == 127

    def test_all_on_and_off(self, tmp_path, fake_socket):  # noqa: F811
        led = matrix_led(tmp_path, matrix_width=4, matrix_height=2, led_channel_count=2)
        led.all_leds_on()
        assert sent_frames(fake_socket.instances[0])[-1][2].strip(b"\0")
        led.all_leds_off()
        assert not sent_frames(fake_socket.instances[0])[-1][2].strip(b"\0")

    def test_update_throttle_skips_frames(self, tmp_path, fake_socket):  # noqa: F811
        led = matrix_led(tmp_path, update_throttle=1, matrix_width=4, matrix_height=2,
                         led_channel_count=2)
        for level in (0.1, 0.4, 0.7, 1.0):
            led.write_all(np.array([level, level]))
        assert len(fake_socket.instances[0].sent) == 2

    def test_levels_out_of_range_are_clipped(self, tmp_path, fake_socket):  # noqa: F811
        led = matrix_led(tmp_path, matrix_width=8, matrix_height=2, led_channel_count=2)
        led.write_all(np.array([1.7, -0.3]))
        clipped = colours(led)
        led.write_all(np.array([1.0, 0.0]))
        assert clipped == colours(led)

    def test_more_channels_than_rows_does_not_crash(self, tmp_path, fake_socket):  # noqa: F811
        for pattern in led_matrix.PATTERNS:
            if pattern == "IMAGE":
                continue
            led = matrix_led(tmp_path, matrix_width=5, matrix_height=3, led_channel_count=3,
                             matrix_pattern_type=pattern)
            for _ in range(3):
                led.write_all(np.linspace(0.2, 1.0, 9))

    def test_unknown_pattern_falls_back_to_sbars(self, tmp_path, fake_socket, caplog):  # noqa: F811
        led = matrix_led(tmp_path, matrix_pattern_type="SPARKLE")
        assert led.patterns == ["SBARS"] and "SPARKLE" in caplog.text

    def test_missing_image_drops_image_pattern(self, tmp_path, fake_socket, caplog):  # noqa: F811
        led = matrix_led(tmp_path, matrix_pattern_type="IMAGE,SBARS",
                         image_path=str(tmp_path / "missing.gif"))
        assert led.patterns == ["SBARS"]
        assert "IMAGE matrix pattern disabled" in caplog.text
        led.write_all(np.array([1.0, 1.0]))

    def test_production_sbars_render_is_fast(self, tmp_path, fake_socket):  # noqa: F811
        """83 x 6 SBARS stays well under a frame budget (the Pi 4 is several times slower)."""
        led = matrix_led(tmp_path, matrix_width=83, matrix_height=6, led_channel_count=6)
        levels = np.full(7, 1.0)
        start = time.perf_counter()
        for _ in range(50):
            led.write_all(levels)
        per_frame = (time.perf_counter() - start) / 50
        assert per_frame < 0.01, f"{per_frame * 1000:.1f} ms per frame"
