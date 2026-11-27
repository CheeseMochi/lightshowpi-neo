"""Virtual show channels ([hardware] virtual_channels).

The default (0) must leave every channel list, FFT size and frame exactly as
before; with N > 0 the extra columns reach outputs (Nanoleaf) but never GPIO
or LED strips, and FFT mode mirrors base channels into them.
"""
import configparser
import importlib
import logging
import os
import shutil
import sys
import types
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import configuration_manager
import hardware_controller
import nanoleaf_protocol as nl
import virtual_channels as vc
from outputs.led import LedOutput
from outputs.nanoleaf import NanoleafOutput
from outputs.threaded import ThreadedOutput

pytestmark = pytest.mark.unit

REPO_CONFIG = os.path.join(os.path.dirname(__file__), "..", "config")


# ---------------------------------------------------------------------------
# helpers module
# ---------------------------------------------------------------------------

class TestHelpers:
    @pytest.mark.parametrize("text, expected", [
        ("", 0), (None, 0), ("0", 0), ("6", 6), (" 3  # nanoleaf", 3),
        ("six", 0), ("-2", 0), ("1.5", 0),
    ])
    def test_parse_count(self, text, expected):
        assert vc.parse_count(text) == expected

    def test_default_sources_wrap(self):
        assert vc.default_sources(6, 7) == [0, 1, 2, 3, 4, 5]
        assert vc.default_sources(5, 2) == [0, 1, 0, 1, 0]
        assert vc.default_sources(0, 7) == []

    @pytest.mark.parametrize("text, expected", [
        ("", [0, 1, 2]),
        ("6,5,4", [6, 5, 4]),
        ("6, 5, 4  # reversed", [6, 5, 4]),
        ("1,2", [0, 1, 2]),        # wrong length -> default
        ("1,2,7", [0, 1, 2]),      # 7 is not a base channel -> default
        ("a,b,c", [0, 1, 2]),
    ])
    def test_parse_sources(self, text, expected, caplog):
        assert vc.parse_sources(text, 3, 7) == expected

    def test_append_mirrored_is_identity_without_virtual(self):
        levels = np.array([0.1, 0.2], dtype="float32")
        assert vc.append_mirrored(levels, []) is levels

    def test_append_mirrored(self):
        levels = np.array([0.1, 0.2, 0.3], dtype="float32")
        out = vc.append_mirrored(levels, [2, 0])
        np.testing.assert_allclose(out, [0.1, 0.2, 0.3, 0.3, 0.1])
        assert out.dtype == levels.dtype

    def test_append_mirrored_no_base(self):
        np.testing.assert_allclose(vc.append_mirrored(np.zeros(0), [0, 0]), [0, 0])


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------

LED_EXTEND = {"led_channel_configuration": "EXTEND", "led_channel_count": "4"}


@pytest.fixture
def config_dir(tmp_path):
    """A config dir with the real defaults.cfg and LED template."""
    d = tmp_path / "config"
    d.mkdir()
    shutil.copy(os.path.join(REPO_CONFIG, "defaults.cfg"), d / "defaults.cfg")
    shutil.copy(os.path.join(REPO_CONFIG, "led1.defaults.cfg"), d / "led1.defaults.cfg")
    (d / "overrides.cfg").write_text("[hardware]\ngpio_pins = 0,1,2,3,4,5,6\n")
    return d


def write_overrides(config_dir, text):
    (config_dir / "overrides.cfg").write_text(
        "[hardware]\ngpio_pins = 0,1,2,3,4,5,6\n" + text)


def write_led(config_dir, name, **values):
    led = configparser.RawConfigParser(allow_no_value=True)
    led.read(config_dir / "led1.defaults.cfg")
    for key, value in values.items():
        led.set("led", key, value)
    with open(config_dir / name, "w") as f:
        led.write(f)


def hardware_lists(hw):
    return (hw.gpio_len, hw.physical_gpio_len, list(hw.gpio_pins), list(hw.pin_modes),
            list(hw.is_pin_pwm), list(hw.active_low_mode))


class TestConfiguration:
    def test_defaults_document_the_keys(self):
        cfg = configparser.RawConfigParser(allow_no_value=True)
        cfg.read(os.path.join(REPO_CONFIG, "defaults.cfg"))
        assert cfg.get("hardware", "virtual_channels") == "0"
        assert cfg.get("hardware", "virtual_channel_source") == ""

    def test_zero_changes_nothing(self, config_dir):
        cm, _, _ = configuration_manager.load_channel_config(str(config_dir), load_leds=False)
        hw = cm.hardware
        # load_channel_config already called it; compare with a fresh set_hardware
        fresh = configuration_manager.Configuration.__new__(configuration_manager.Configuration)
        fresh.config = cm.config
        fresh.set_hardware()
        assert hardware_lists(hw) == hardware_lists(fresh.hardware)
        assert hw.base_channel_count == hw.gpio_len == 7
        assert hw.virtual_channel_count == 0 and hw.virtual_channel_sources == []
        assert cm.channel_layout() == {"gpio_len": 7, "physical": [0, 7],
                                       "led_extend": None, "virtual": None}

    def test_zero_with_led_extend_unchanged(self, config_dir):
        write_led(config_dir, "led2.cfg", **LED_EXTEND)
        write_overrides(config_dir, "[configs]\nled_config = led2.cfg\n")
        cm, leds, errors = configuration_manager.load_channel_config(str(config_dir))
        assert errors == []
        hw = cm.hardware
        assert (hw.gpio_len, hw.physical_gpio_len, hw.base_channel_count) == (11, 7, 11)
        assert hw.gpio_pins == [0, 1, 2, 3, 4, 5, 6, 1000, 1001, 1002, 1003]
        assert len(hw.active_low_mode) == 7   # EXTEND never padded it; still untouched
        assert leds == [{"name": "led2.cfg", "channel_configuration": "EXTEND",
                         "physical_gpio_len": 7, "gpio_len": 11}]

    def test_virtual_channels_appended(self, config_dir):
        write_overrides(config_dir, "virtual_channels = 6\n")
        cm, _, _ = configuration_manager.load_channel_config(str(config_dir), load_leds=False)
        hw = cm.hardware
        assert hw.gpio_len == 13
        assert hw.physical_gpio_len == 7
        assert hw.base_channel_count == 7
        assert hw.virtual_channel_count == 6
        assert hw.virtual_channel_sources == [0, 1, 2, 3, 4, 5]
        assert hw.gpio_pins == [0, 1, 2, 3, 4, 5, 6] + [2000 + i for i in range(6)]
        assert hw.pin_modes[7:] == ["pwm"] * 6
        assert hw.is_pin_pwm[7:] == [True] * 6
        assert hw.active_low_mode[7:] == [False] * 6
        for name in ("gpio_pins", "pin_modes", "is_pin_pwm", "active_low_mode"):
            assert len(getattr(hw, name)) == 13, name
        assert cm.channel_layout() == {"gpio_len": 13, "physical": [0, 7],
                                       "led_extend": None, "virtual": [7, 13]}

    def test_after_led_extend(self, config_dir):
        write_led(config_dir, "led2.cfg", **LED_EXTEND)
        write_overrides(config_dir, "virtual_channels = 2\nvirtual_channel_source = 8,0\n"
                                    "[configs]\nled_config = led2.cfg\n")
        cm, _, _ = configuration_manager.load_channel_config(str(config_dir))
        hw = cm.hardware
        assert (hw.gpio_len, hw.physical_gpio_len, hw.base_channel_count) == (13, 7, 11)
        assert hw.gpio_pins[7:] == [1000, 1001, 1002, 1003, 2000, 2001]
        assert hw.virtual_channel_sources == [8, 0]   # an LED EXTEND channel can be a source
        assert len(hw.active_low_mode) == 13
        assert cm.channel_layout()["led_extend"] == [7, 11]
        assert cm.channel_layout()["virtual"] == [11, 13]

    def test_idempotent(self, config_dir):
        write_overrides(config_dir, "virtual_channels = 3\n")
        cm, _, _ = configuration_manager.load_channel_config(str(config_dir), load_leds=False)
        before = hardware_lists(cm.hardware)
        assert cm.add_virtual_channels() == 3
        assert hardware_lists(cm.hardware) == before

    @pytest.mark.parametrize("text", ["virtual_channels = lots\n", "virtual_channels = -1\n"])
    def test_invalid_count_is_off(self, config_dir, text, caplog):
        write_overrides(config_dir, text)
        with caplog.at_level(logging.WARNING):
            cm, _, _ = configuration_manager.load_channel_config(str(config_dir), load_leds=False)
        assert cm.hardware.gpio_len == 7 and cm.hardware.virtual_channel_count == 0
        assert "virtual_channels" in caplog.text

    def test_invalid_source_uses_default(self, config_dir, caplog):
        write_overrides(config_dir, "virtual_channels = 2\nvirtual_channel_source = 3,99\n")
        with caplog.at_level(logging.WARNING):
            cm, _, _ = configuration_manager.load_channel_config(str(config_dir), load_leds=False)
        assert cm.hardware.virtual_channel_sources == [0, 1]
        assert "virtual_channel_source" in caplog.text

    def test_missing_keys_fall_back(self, config_dir):
        # An old defaults.cfg without the keys: same as 0
        text = (config_dir / "defaults.cfg").read_text()
        text = "\n".join(line for line in text.splitlines()
                         if not line.startswith("virtual_channel"))
        (config_dir / "defaults.cfg").write_text(text)
        cm, _, _ = configuration_manager.load_channel_config(str(config_dir), load_leds=False)
        assert cm.hardware.gpio_len == 7 and cm.hardware.virtual_channel_count == 0


# ---------------------------------------------------------------------------
# hardware
# ---------------------------------------------------------------------------

class TestHardware:
    def _hc(self, monkeypatch, virtual=0, physical=3):
        base = physical
        hw = SimpleNamespace(physical_gpio_len=physical, gpio_len=base + virtual,
                             base_channel_count=base)
        monkeypatch.setattr(hardware_controller, "cm", SimpleNamespace(hardware=hw))
        hc = hardware_controller.Hardware.__new__(hardware_controller.Hardware)
        hc.server = False
        hc.channels = [Mock() for _ in range(base + virtual)]
        hc.outputs = [Mock()]
        return hc

    def test_set_array_never_touches_virtual_pins(self, monkeypatch, caplog):
        hc = self._hc(monkeypatch, virtual=2)
        frame = np.array([0.1, 0.2, 0.3, 0.9, 0.8], dtype="float32")
        with caplog.at_level(logging.WARNING):
            hc.set_array(frame, True)
        assert [c.set_action.call_args.args[1] for c in hc.channels[:3]] == \
            pytest.approx([0.1, 0.2, 0.3])
        assert not hc.channels[3].set_action.called
        assert not hc.channels[4].set_action.called
        np.testing.assert_allclose(hc.outputs[0].write_frame.call_args.args[0], frame)
        assert "not equal to physical_gpio_length" not in caplog.text   # no per-frame warning

    def test_set_array_still_warns_on_odd_lengths(self, monkeypatch, caplog):
        hc = self._hc(monkeypatch, virtual=2)
        with caplog.at_level(logging.WARNING):
            hc.set_array(np.zeros(4), True)
        assert "not equal to physical_gpio_length" in caplog.text

    def test_pins_set_up_only_for_base_channels(self, monkeypatch):
        hc = self._hc(monkeypatch, virtual=2)
        hc.set_pins_as_outputs()
        hc.set_pins_as_inputs()
        assert [c.set_as_output.called for c in hc.channels] == [True] * 3 + [False] * 2
        assert [c.set_as_input.called for c in hc.channels] == [True] * 3 + [False] * 2

    def test_pins_without_base_count_use_gpio_len(self, monkeypatch):
        hw = SimpleNamespace(physical_gpio_len=2, gpio_len=4)   # older stub shape
        monkeypatch.setattr(hardware_controller, "cm", SimpleNamespace(hardware=hw))
        hc = hardware_controller.Hardware.__new__(hardware_controller.Hardware)
        hc.channels = [Mock() for _ in range(4)]
        hc.set_pins_as_outputs()
        assert all(c.set_as_output.called for c in hc.channels)

    def test_bound_led_extend_reaches_threaded_and_plain(self):
        hc = hardware_controller.Hardware.__new__(hardware_controller.Hardware)
        plain = LedOutput(Mock(), "EXTEND", physical_gpio_len=3, gpio_len=7)
        wrapped = ThreadedOutput(LedOutput(Mock(), "EXTEND", physical_gpio_len=3, gpio_len=7))
        other = Mock(spec=["write_frame"])
        hc.outputs = [plain, wrapped, other]
        hc._bound_led_extend(7)
        assert plain.extend_end == 7 and wrapped.inner.extend_end == 7

    def test_full_hardware_with_virtual_channels(self, tmp_path):
        overrides = tmp_path / "virtual.cfg"
        overrides.write_text("[hardware]\ngpio_pins = 0,1,2\nvirtual_channels = 2\n"
                             "[configs]\nled_config =\n[network]\nnetworking = off\n")
        hc = hardware_controller.Hardware(param_config=str(overrides))
        hw = hc.cm.hardware
        assert (hw.gpio_len, hw.physical_gpio_len, hw.base_channel_count) == (5, 3, 3)
        assert len(hc.channels) == 5
        assert [c.pin_number for c in hc.channels] == [0, 1, 2, 2000, 2001]

    def test_full_hardware_default_unchanged(self, tmp_path):
        overrides = tmp_path / "plain.cfg"
        overrides.write_text("[hardware]\ngpio_pins = 0,1,2\n"
                             "[configs]\nled_config =\n[network]\nnetworking = off\n")
        hc = hardware_controller.Hardware(param_config=str(overrides))
        hw = hc.cm.hardware
        assert (hw.gpio_len, hw.physical_gpio_len, hw.base_channel_count) == (3, 3, 3)
        assert hw.gpio_pins == [0, 1, 2]
        assert [c.pin_number for c in hc.channels] == [0, 1, 2]


# ---------------------------------------------------------------------------
# LED and Nanoleaf outputs
# ---------------------------------------------------------------------------

FRAME = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8], dtype="float32")


class TestLedOutputSlice:
    def test_extend_unbounded_by_default(self):
        out = LedOutput(Mock(), "EXTEND", physical_gpio_len=3, gpio_len=6)
        assert out.extend_end is None
        np.testing.assert_allclose(out.select(FRAME), FRAME[3:])

    def test_extend_stops_before_virtual(self):
        out = LedOutput(Mock(), "EXTEND", physical_gpio_len=3, gpio_len=6)
        out.extend_end = 6
        np.testing.assert_allclose(out.select(FRAME), [0.4, 0.5, 0.6])

    def test_mirror_ignores_virtual_columns(self):
        out = LedOutput(Mock(), "MIRROR", physical_gpio_len=6, gpio_len=6)
        np.testing.assert_allclose(out.select(FRAME), FRAME[:6])

    def test_client_still_unsliced(self):
        out = LedOutput(Mock(), "EXTEND", physical_gpio_len=3, gpio_len=6, slice_levels=False)
        out.extend_end = 6
        np.testing.assert_allclose(out.select(FRAME), FRAME)


class FakeClient:
    def __init__(self, host, port, token, timeout):
        pass

    def length(self):
        return 20

    def set_state(self, **kw):
        return nl.Response(204, None, b"")

    def enable_ext_control(self, version="v2"):
        return nl.Response(204, None, b"")


class FakeSender:
    def __init__(self, host, port):
        self.frames = []

    def send(self, packets):
        leds = {}
        for p in packets:
            for led, r, g, b, w, t in nl.decode_frame_v2(p):
                leds[led] = (r, g, b)
        self.frames.append(leds)
        return 0.0

    def close(self):
        pass


class TestNanoleafOnVirtualChannels:
    def test_nanoleaf_reads_virtual_columns(self):
        # 3 relays + 2 virtual channels; the strip maps to the virtual ones
        out = NanoleafOutput("10.0.0.9", "tok", [3, 4], colors=[(255, 255, 255)] * 2,
                             client_factory=FakeClient, sender_factory=FakeSender,
                             clock=lambda: 0.0)
        out.start()
        out.write_frame(np.array([1.0, 1.0, 1.0, 0.0, 0.5], dtype="float32"))
        frame = out._sender.frames[-1]
        assert frame[0] == (0, 0, 0)          # segment 0 <- channel 3 (off)
        assert frame[19] == (127, 127, 127)   # segment 1 <- channel 4 (half)

    def test_fft_mirror_feeds_nanoleaf(self):
        base = np.array([0.0, 1.0, 0.25], dtype="float32")
        frame = vc.append_mirrored(base, [2, 1])
        out = NanoleafOutput("10.0.0.9", "tok", [3, 4], colors=[(200, 200, 200)] * 2,
                             client_factory=FakeClient, sender_factory=FakeSender,
                             clock=lambda: 0.0)
        out.start()
        out.write_frame(frame)
        got = out._sender.frames[-1]
        assert got[0] == (50, 50, 50) and got[19] == (200, 200, 200)


# ---------------------------------------------------------------------------
# synchronized_lights.update_lights
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sl():
    """synchronized_lights without ALSA (same stub as test_get_song_sequence)."""
    stubbed = False
    try:
        import alsaaudio  # noqa: F401
    except ImportError:
        sys.modules["alsaaudio"] = types.ModuleType("alsaaudio")
        stubbed = True
    had_module = "synchronized_lights" in sys.modules
    try:
        module = importlib.import_module("synchronized_lights")
    except Exception as error:  # pragma: no cover - depends on the platform
        module = None
        reason = str(error)
    try:
        if module is None:
            pytest.skip(f"synchronized_lights not importable: {reason}")
        yield module
    finally:
        if stubbed:
            sys.modules.pop("alsaaudio", None)
            if not had_module:
                sys.modules.pop("synchronized_lights", None)


def make_show(sl, monkeypatch, base, sources, sequence_type="auto"):
    hc = Mock()
    monkeypatch.setattr(sl, "hc", hc)
    monkeypatch.setattr(sl, "cm", SimpleNamespace(
        custom_sequences=SimpleNamespace(brightness_range=255)))
    show = sl.Lightshow.__new__(sl.Lightshow)
    show.sequence_type = sequence_type
    show.terminal = None
    show.mean = np.full(base, 12.0, dtype="float32")
    show.std = np.full(base, 1.5, dtype="float32")
    show.sd_low, show.sd_high = 0.5, 0.75
    show.attenuate_pct = 0.0
    show.decay_factor = 0.0
    show.decay = np.zeros(base, dtype="float32")
    show.fft_channel_count = base
    show.virtual_sources = sources
    return show, hc


class TestUpdateLights:
    MATRIX = np.array([11.5, 12.0, 13.0, 14.0], dtype="float32")

    def test_default_frame_unchanged(self, sl, monkeypatch):
        show, hc = make_show(sl, monkeypatch, 4, [])
        show.update_lights(self.MATRIX)
        sent = hc.set_array.call_args.args[0]
        expected = np.clip((self.MATRIX - 12.0 + 1.5 * 0.5) / (1.5 * 1.25), 0.0, 1.0)
        assert len(sent) == 4
        np.testing.assert_allclose(sent, expected, rtol=1e-6)

    def test_fft_mirrors_into_virtual(self, sl, monkeypatch):
        show, hc = make_show(sl, monkeypatch, 4, [3, 0])
        show.update_lights(self.MATRIX)
        sent = hc.set_array.call_args.args[0]
        assert len(sent) == 6
        np.testing.assert_allclose(sent[4:], [sent[3], sent[0]])
        assert hc.set_array.call_args.args[1] is True

    def test_csv_rows_are_not_mirrored(self, sl, monkeypatch):
        show, hc = make_show(sl, monkeypatch, 4, [3, 0], sequence_type="csv")
        row = np.array([255, 0, 0, 0, 0, 255], dtype="float32")
        show.update_lights(row)
        np.testing.assert_allclose(hc.set_array.call_args.args[0], [1, 0, 0, 0, 0, 1])


# ---------------------------------------------------------------------------
# API: GET /api/layout
# ---------------------------------------------------------------------------

@pytest.fixture
def layout_client(config_dir, monkeypatch):
    from api.core.auth import get_current_user
    from api.routers import devices as devices_router
    from api.routers import layout as layout_router
    from api.routers.config_files import get_config_dir

    monkeypatch.setattr(devices_router, "_store", None)
    app = FastAPI()
    app.include_router(layout_router.router)
    app.dependency_overrides[get_config_dir] = lambda: str(config_dir)
    app.dependency_overrides[get_current_user] = lambda: {"username": "tester", "id": 1}
    return TestClient(app)


class TestLayoutEndpoint:
    def test_default_layout(self, layout_client):
        r = layout_client.get("/api/layout")
        assert r.status_code == 200
        assert r.json() == {"gpio_len": 7, "physical": [0, 7], "led_extend": None,
                            "virtual": None, "virtual_sources": [], "outputs": [],
                            "channel_labels": {}, "warnings": []}

    def test_nanoleaf_on_virtual_channels(self, layout_client, config_dir):
        write_led(config_dir, "led2.cfg", led_configuration="MATRIX",
                  led_channel_configuration="MIRROR")
        write_overrides(config_dir, "virtual_channels = 6\n"
                                    "[configs]\nled_config = led2.cfg\n"
                                    "[output:nanoleaf]\ntype = nanoleaf\nhost = 10.0.0.5\n"
                                    "token = s3cret\nchannels = 7,8,9,10,11,12  # own pattern\n")
        body = layout_client.get("/api/layout").json()
        assert body["gpio_len"] == 13
        assert body["physical"] == [0, 7]
        assert body["led_extend"] is None
        assert body["virtual"] == [7, 13]
        assert body["virtual_sources"] == [0, 1, 2, 3, 4, 5]
        outputs = {o["name"]: o for o in body["outputs"]}
        assert outputs["nanoleaf"] == {"name": "nanoleaf", "type": "nanoleaf",
                                       "channels": [7, 8, 9, 10, 11, 12]}
        assert outputs["led:led2.cfg"]["channels"] == list(range(7))
        assert "s3cret" not in str(body) and "10.0.0.5" not in str(body)
        assert body["warnings"] == []

    def test_extend_and_out_of_range_warning(self, layout_client, config_dir):
        write_led(config_dir, "led2.cfg", **LED_EXTEND)
        write_overrides(config_dir, "virtual_channels = 2\n"
                                    "[configs]\nled_config = led2.cfg\n"
                                    "[output:nl]\ntype = nanoleaf\nhost = h\ntoken = t\n"
                                    "channels = 11,12,13\n")
        body = layout_client.get("/api/layout").json()
        assert (body["gpio_len"], body["led_extend"], body["virtual"]) == (13, [7, 11], [11, 13])
        outputs = {o["name"]: o for o in body["outputs"]}
        assert outputs["led:led2.cfg"]["channels"] == [7, 8, 9, 10]
        assert any("[13]" in w for w in body["warnings"])

    def test_repeated_calls_do_not_grow(self, layout_client, config_dir):
        write_led(config_dir, "led2.cfg", **LED_EXTEND)
        write_overrides(config_dir, "virtual_channels = 2\n[configs]\nled_config = led2.cfg\n")
        first = layout_client.get("/api/layout").json()
        assert layout_client.get("/api/layout").json() == first

    def test_relay_labels(self, layout_client, monkeypatch):
        from api.routers import devices as devices_router
        store = Mock()
        store.list.return_value = [{"channel": 1, "name": "Snowflake"},
                                   {"channel": 99, "name": "Ghost"}]
        monkeypatch.setattr(devices_router, "_store", store)
        body = layout_client.get("/api/layout").json()
        assert body["channel_labels"] == {"1": "Snowflake"}

    def test_requires_login(self, config_dir):
        from api.routers import layout as layout_router
        from api.routers.config_files import get_config_dir
        app = FastAPI()
        app.include_router(layout_router.router)
        app.dependency_overrides[get_config_dir] = lambda: str(config_dir)
        assert TestClient(app).get("/api/layout").status_code in (401, 403)
