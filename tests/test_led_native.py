"""Native LED strip over sACN: led_colors, driver_sacn and led_module (no BiblioPixel)."""

from unittest.mock import Mock

import numpy as np
import pytest

import driver_sacn
import led_colors
import led_module
from driver_sacn import SacnSender, split_universes

# E1.31 packet offsets (root layer 38 bytes, framing layer 77, DMP header 11)
SEQ_OFFSET = 111
UNIVERSE_OFFSET = 113
DATA_OFFSET = 126


def decode(packet):
    """(universe, sequence, data) of one E1.31 packet."""
    packet = bytes(packet)
    universe = int.from_bytes(packet[UNIVERSE_OFFSET:UNIVERSE_OFFSET + 2], "big")
    return universe, packet[SEQ_OFFSET], packet[DATA_OFFSET:]


class FakeSocket:
    """Records sendto calls; can be told to fail."""

    instances = []
    fail = False

    def __init__(self, *args):
        self.sent = []
        self.closed = False
        FakeSocket.instances.append(self)

    def setsockopt(self, *args):
        pass

    def settimeout(self, t):
        pass

    def sendto(self, data, addr):
        if self.fail:
            raise OSError("Network is unreachable")
        self.sent.append((bytes(data), addr))

    def close(self):
        self.closed = True


@pytest.fixture
def fake_socket(monkeypatch):
    FakeSocket.instances = []
    FakeSocket.fail = False
    monkeypatch.setattr(driver_sacn.socket, "socket", FakeSocket)
    return FakeSocket


def sent_frames(sock):
    return [decode(p) for p, _ in sock.sent]


# ---------------------------------------------------------------------------
# led_colors: values pinned from BiblioPixel 3.4.46 (verified against its source)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestLedColors:

    @pytest.mark.parametrize("hue,rgb", [(0, (255, 0, 0)), (64, (171, 171, 0)),
                                         (128, (0, 171, 85)), (192, (85, 0, 171)),
                                         (255, (253, 0, 3))])
    def test_hue_table_matches_bibliopixel(self, hue, rgb):
        assert led_colors.hue2rgb(hue) == rgb

    def test_hsv_desaturated_and_dim(self):
        assert led_colors.hsv2rgb_rainbow((0, 0, 255)) == (254, 254, 254)  # as BiblioPixel
        assert led_colors.hsv2rgb_rainbow((0, 255, 0)) == (0, 0, 0)

    def test_color_scale(self):
        assert led_colors.color_scale((255, 128, 0), 256) == (255, 128, 0)
        assert led_colors.color_scale((255, 128, 0), 128) == (127, 64, 0)
        assert led_colors.color_scale((255, 128, 0), 0) == (0, 0, 0)

    def test_colour_maps_import_without_bibliopixel(self):
        from led_color_maps import lspi_color_maps
        for name, cmap in lspi_color_maps.map.items():
            assert len(cmap) >= 256, name


# ---------------------------------------------------------------------------
# driver_sacn
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestSplitUniverses:

    def test_510_keeps_pixels_whole(self):
        parts = split_universes(bytes(range(256)) * 4, 1, 510)
        assert [(u, len(d)) for u, d in parts] == [(1, 510), (2, 510), (3, 4)]

    def test_exact_multiple_has_no_empty_universe(self):
        parts = split_universes(bytes(1020), 5, 510)
        assert [(u, len(d)) for u, d in parts] == [(5, 510), (6, 510)]

    def test_data_is_contiguous(self):
        data = bytes(i % 251 for i in range(1500))
        assert b"".join(d for _, d in split_universes(data, 1, 510)) == data

    @pytest.mark.parametrize("size", [0, 513])
    def test_bad_size(self, size):
        with pytest.raises(ValueError):
            split_universes(b"abc", 1, size)


@pytest.mark.unit
class TestSacnSender:

    def test_sends_each_universe_to_host(self, fake_socket):
        s = SacnSender("192.168.3.177", universe=1, universe_boundary=510)
        assert s.send(bytes(range(256)) * 3) is True
        sock = fake_socket.instances[0]
        assert {addr for _, addr in sock.sent} == {("192.168.3.177", 5568)}
        frames = sent_frames(sock)
        assert [(u, len(d)) for u, _, d in frames] == [(1, 510), (2, 258)]
        assert b"".join(d for _, _, d in frames) == bytes(range(256)) * 3

    def test_unchanged_frame_not_resent_and_sequence_advances(self, fake_socket):
        s = SacnSender("10.0.0.2")
        s.send(b"\x01\x02\x03")
        assert s.send(b"\x01\x02\x03") is False
        s.send(b"\x04\x05\x06")
        assert [seq for _, seq, _ in sent_frames(fake_socket.instances[0])] == [0, 1]

    def test_sequence_wraps(self, fake_socket):
        s = SacnSender("10.0.0.2")
        s.sequence = 255
        s.send(b"\x01")
        s.send(b"\x02")
        assert [seq for _, seq, _ in sent_frames(fake_socket.instances[0])] == [255, 0]

    def test_network_error_is_logged_not_raised_and_recovers(self, fake_socket, caplog):
        s = SacnSender("10.0.0.2")
        fake_socket.fail = True
        assert s.send(b"\x01\x02\x03") is False
        assert s.send(b"\x01\x02\x03") is False  # same frame retried, not deduped
        assert s.errors == 2
        assert all(sock.closed for sock in fake_socket.instances)
        assert caplog.text.count("sACN send to 10.0.0.2:5568 failed") == 1  # rate-limited
        fake_socket.fail = False
        assert s.send(b"\x01\x02\x03") is True  # reopens and recovers
        assert len(fake_socket.instances) == 3

    def test_needs_host(self):
        with pytest.raises(ValueError, match="sacn_address"):
            SacnSender("")

    def test_warns_when_boundary_splits_pixels(self, caplog):
        SacnSender("10.0.0.2", universe_boundary=512)
        assert "must match the receiver" in caplog.text


# ---------------------------------------------------------------------------
# led_module: strip patterns, end to end from a parsed LED config
# ---------------------------------------------------------------------------

LED_CFG = """[led]
led_connection = SACN
led_configuration = STRIP
strip_type =
channel_order = rgb
led_channel_configuration = LEDONLY
led_channel_count = 2
max_brightness = 100
per_channel = 4
custom_per_channel =
pattern_color_map = MONO
pattern_color = 255,255,255
pattern_type = FULL
enable_multicast = False
sacn_address = 192.168.3.177
sacn_port = 5568
universe_boundary = 510
universe_start = 1
device_id = 0
device_address =
hardware_id =
baud_rate = 1000000
update_throttle = 0
matrix_width = 2
matrix_height = 2
matrix_pattern_type = SBARS
beats = 5
image_path =
banner_text = hi
"""


def parse_led(tmp_path, **overrides):
    """Parse LED_CFG (with overrides) through configuration_manager.set_led."""
    import configuration_manager
    text = LED_CFG
    for key, value in overrides.items():
        text = "\n".join(f"{key} = {value}" if line.startswith(f"{key} =") else line
                         for line in text.splitlines()) + "\n"
    cfg = tmp_path / "led.cfg"
    cfg.write_text(text)
    config = configuration_manager.Configuration.__new__(configuration_manager.Configuration)
    config.config_dir = str(tmp_path) + "/"
    config.home_dir = str(tmp_path)
    config.hardware = Mock()
    config.hardware.get.return_value = 2
    config.set_led(config_file="led.cfg")
    return config.led


def pixels(sock, index=-1):
    """The last sent frame as an (n, 3) array (single-universe frames)."""
    _, _, data = sent_frames(sock)[index]
    return np.frombuffer(data, dtype=np.uint8).reshape(-1, 3)


@pytest.mark.unit
class TestSacnStripLed:

    def test_dispatches_native_for_sacn_strip(self, tmp_path, fake_socket):
        led = led_module.Led(parse_led(tmp_path))
        assert isinstance(led, led_module.SacnStripLed)
        assert led.led_count == 8

    def test_full_mono(self, tmp_path, fake_socket):
        led = led_module.Led(parse_led(tmp_path))
        led.write_all(np.array([1.0, 0.5]))
        px = pixels(fake_socket.instances[0])
        assert px.tolist() == [[255, 255, 255]] * 4 + [[127, 127, 127]] * 4

    def test_max_brightness_scales_like_bibliopixel(self, tmp_path, fake_socket):
        led = led_module.Led(parse_led(tmp_path, max_brightness=50))
        led.write_all(np.array([1.0, 0.0]))
        # int(0.5 * 255) = 127 -> level 127/255, then int(255 * level) = 127
        assert pixels(fake_socket.instances[0])[0].tolist() == [127, 127, 127]

    def test_cbars_grows_from_the_middle(self, tmp_path, fake_socket):
        led = led_module.Led(parse_led(tmp_path, pattern_type="CBARS", per_channel=8))
        led.write_all(np.array([0.5, 0.0]))
        lit = [i for i, p in enumerate(pixels(fake_socket.instances[0])) if p.any()]
        assert lit == [2, 3, 4, 5, 6]  # midl 4, +/- int(0.5 * 4) = 2, inclusive

    def test_freq1_colours_per_channel(self, tmp_path, fake_socket):
        led = led_module.Led(parse_led(tmp_path, pattern_color_map="FREQ1"))
        led.write_all(np.array([1.0, 1.0]))
        px = pixels(fake_socket.instances[0])
        assert tuple(px[0]) == led_colors.hue2rgb(0)
        assert tuple(px[4]) == led_colors.hue2rgb(127)

    def test_lbars(self, tmp_path, fake_socket):
        led = led_module.Led(parse_led(tmp_path, pattern_type="LBARS", per_channel=8))
        led.write_all(np.array([1.0, 0.0]))
        px = pixels(fake_socket.instances[0])
        assert tuple(px[4]) == led_colors.Green
        assert px[1:8].any(axis=1).all() and not px[0].any()

    def test_levels_out_of_range_are_clipped(self, tmp_path, fake_socket):
        led = led_module.Led(parse_led(tmp_path, pattern_color_map="MAP1"))
        led.write_all(np.array([1.7, -0.2]))
        px = pixels(fake_socket.instances[0])
        assert px[:4].any() and not px[4:].any()

    def test_all_on_and_off(self, tmp_path, fake_socket):
        led = led_module.Led(parse_led(tmp_path))
        led.all_leds_on()
        assert pixels(fake_socket.instances[0]).min() == 255
        led.all_leds_off()
        assert not pixels(fake_socket.instances[0]).any()

    def test_update_throttle_skips_frames(self, tmp_path, fake_socket):
        led = led_module.Led(parse_led(tmp_path, update_throttle=1))
        for level in (0.1, 0.2, 0.3, 0.4):
            led.write_all(np.array([level, level]))
        assert len(fake_socket.instances[0].sent) == 2

    def test_map1a_does_not_leak_between_instances(self, tmp_path, fake_socket):
        led_module.Led(parse_led(tmp_path, pattern_color_map="MAP1A", pattern_color="1,2,3"))
        assert led_module.color_map[255] == list(led_colors.hue2rgb(255))

    def test_multi_universe_strip(self, tmp_path, fake_socket):
        led = led_module.Led(parse_led(tmp_path, per_channel=200))
        led.write_all(np.array([1.0, 1.0]))
        frames = sent_frames(fake_socket.instances[0])
        assert [(u, len(d)) for u, _, d in frames] == [(1, 510), (2, 510), (3, 180)]


@pytest.mark.unit
class TestLegacyConfigs:

    @pytest.mark.parametrize("overrides", [{"led_connection": "SERIAL"},
                                           {"led_connection": "SPI", "strip_type": "APA102"}])
    def test_non_sacn_strip_explains_bibliopixel(self, tmp_path, overrides):
        pytest.importorskip("numpy")
        try:
            import bibliopixel  # noqa: F401
            pytest.skip("BiblioPixel is installed")
        except ImportError:
            pass
        with pytest.raises(ImportError, match="BiblioPixel"):
            led_module.Led(parse_led(tmp_path, **overrides))


@pytest.mark.unit
class TestHardwareSkipsBrokenLedConfig:

    def test_bad_led_config_is_logged_and_skipped(self, caplog):
        import hardware_controller
        hc = hardware_controller.Hardware.__new__(hardware_controller.Hardware)
        hc.cm = Mock()
        hc.cm.configs.led_multiprocess = False
        hc.led = []
        hc.outputs = []
        hc._add_led_output = Mock()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(hardware_controller.led_module, "Led",
                       Mock(side_effect=ValueError("sACN LED output needs sacn_address")))
            hc._add_led("led2.cfg")
        assert hc.led == [] and not hc._add_led_output.called
        assert "LED config led2.cfg disabled" in caplog.text
