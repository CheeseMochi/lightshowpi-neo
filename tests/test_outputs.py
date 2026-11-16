"""Tests for py/outputs: the Output interface, LedOutput and Hardware.write_outputs."""
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

import hardware_controller
from outputs.base import Output
from outputs.led import LedOutput

pytestmark = pytest.mark.unit

FRAME = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6], dtype="float32")


class TestOutputBase:
    def test_write_frame_must_be_implemented(self):
        with pytest.raises(NotImplementedError):
            Output().write_frame(FRAME)

    def test_defaults_are_noops(self):
        out = Output()
        out.start()
        out.all_on()
        out.all_off()
        out.close()
        out.close()
        assert out.latency_s == 0.0 and out.max_fps is None


class TestLedOutputSlicing:
    """Same slicing update_lights used to do inline."""

    @pytest.mark.parametrize("config, expected", [
        ("EXTEND", [0.5, 0.6]),            # channels after the 4 physical GPIO
        ("MIRROR", [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]),
        ("LEDONLY", [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]),
        ("extend", [0.5, 0.6]),
    ])
    def test_select(self, config, expected):
        led = Mock()
        LedOutput(led, config, physical_gpio_len=4, gpio_len=6).write_frame(FRAME)
        np.testing.assert_allclose(led.write_all.call_args.args[0], expected)

    def test_gpio_len_caps_non_extend(self):
        out = LedOutput(Mock(), "MIRROR", physical_gpio_len=4, gpio_len=3)
        np.testing.assert_allclose(out.select(FRAME), [0.1, 0.2, 0.3])

    def test_client_passes_whole_frame(self):
        out = LedOutput(Mock(), "EXTEND", physical_gpio_len=4, gpio_len=6, slice_levels=False)
        np.testing.assert_allclose(out.select(FRAME), FRAME)

    def test_all_on_off_match_legacy_calls(self):
        led = Mock()
        out = LedOutput(led, "MIRROR", 4, 6)
        out.all_on()
        assert led.all_set_on is True
        led.all_leds_on.assert_called_once_with()
        out.all_off()
        assert led.all_set_on is False
        led.all_leds_off.assert_called_once_with()


class TestHardwareOutputs:
    def _hc(self, monkeypatch, outputs, physical=2):
        cm = SimpleNamespace(hardware=SimpleNamespace(physical_gpio_len=physical, gpio_len=physical))
        monkeypatch.setattr(hardware_controller, "cm", cm)
        hc = hardware_controller.Hardware.__new__(hardware_controller.Hardware)
        hc.outputs = outputs
        hc.set_light = Mock()
        return hc

    def test_write_outputs_reaches_every_output(self, monkeypatch):
        a, b = Mock(), Mock()
        hc = self._hc(monkeypatch, [a, b])
        hc.write_outputs(FRAME)
        a.write_frame.assert_called_once_with(FRAME)
        b.write_frame.assert_called_once_with(FRAME)

    def test_turn_on_off_lights_reach_outputs(self, monkeypatch):
        out = Mock()
        hc = self._hc(monkeypatch, [out])
        hc.turn_on_lights()
        out.all_on.assert_called_once_with()
        hc.turn_off_lights()
        out.all_off.assert_called_once_with()
        assert hc.set_light.call_count == 4

    def test_no_outputs_attribute_is_tolerated(self, monkeypatch):
        hc = self._hc(monkeypatch, [])
        del hc.outputs
        hc.write_outputs(FRAME)
        hc.turn_off_lights()


class TestSetArrayReachesOutputs:
    def _hc(self, monkeypatch, out):
        cm = SimpleNamespace(hardware=SimpleNamespace(physical_gpio_len=3, gpio_len=3))
        monkeypatch.setattr(hardware_controller, "cm", cm)
        hc = hardware_controller.Hardware.__new__(hardware_controller.Hardware)
        hc.server = False
        hc.channels = [Mock() for _ in range(3)]
        hc.outputs = [out]
        return hc

    def test_ambient_list_reaches_outputs_as_ndarray(self, monkeypatch):
        out = Mock()
        hc = self._hc(monkeypatch, out)
        hc.set_array([1.0, 0.0, 0.5])  # ambient mode passes a plain list
        sent = out.write_frame.call_args.args[0]
        assert isinstance(sent, np.ndarray)
        np.testing.assert_allclose(sent * 255, [255, 0, 127.5])
        assert [c.set_action.call_args.args[1] for c in hc.channels] == [1.0, 0.0, 0.5]

    def test_show_frame_written_once(self, monkeypatch):
        out = Mock()
        hc = self._hc(monkeypatch, out)
        hc.set_array(np.array([0.2, 0.3, 0.4], dtype="float32"), True)
        assert out.write_frame.call_count == 1


class TestOutputLifecycle:
    def _hc(self, monkeypatch, outputs):
        cm = SimpleNamespace(hardware=SimpleNamespace(physical_gpio_len=0, gpio_len=0))
        monkeypatch.setattr(hardware_controller, "cm", cm)
        monkeypatch.setattr(hardware_controller.wiringpi, "wiringPiSetupPY", lambda: None, raising=False)
        hc = hardware_controller.Hardware.__new__(hardware_controller.Hardware)
        hc.outputs = outputs
        hc.enable_device = Mock()
        hc.set_pins_as_outputs = Mock()
        hc.set_pins_as_inputs = Mock()
        hc.network = Mock()
        return hc

    def test_initialize_starts_and_clean_up_closes_after_off(self, monkeypatch):
        calls = []
        out = Mock()
        out.start.side_effect = lambda: calls.append("start")
        out.all_off.side_effect = lambda: calls.append("off")
        out.close.side_effect = lambda: calls.append("close")
        hc = self._hc(monkeypatch, [out])
        hc.initialize()
        hc.clean_up()
        assert calls == ["start", "off", "off", "close"]

    def test_failing_output_does_not_block_others_or_gpio_cleanup(self, monkeypatch):
        bad, good = Mock(), Mock()
        bad.start.side_effect = OSError("no route")
        bad.close.side_effect = OSError("gone")
        hc = self._hc(monkeypatch, [bad, good])
        hc.initialize()
        good.start.assert_called_once_with()
        hc.clean_up()
        good.close.assert_called_once_with()
        hc.set_pins_as_inputs.assert_called_once_with()

    def test_threaded_output_gets_frames_after_initialize(self, monkeypatch):
        import time
        from outputs.threaded import ThreadedOutput
        inner = Mock(max_fps=None, latency_s=0.0)
        threaded = ThreadedOutput(inner)
        hc = self._hc(monkeypatch, [threaded])
        hc.initialize(reset=False)
        hc.write_outputs([0.5])
        deadline = time.monotonic() + 2
        while not inner.write_frame.called and time.monotonic() < deadline:
            time.sleep(0.005)
        hc.clean_up()
        inner.write_frame.assert_called()
        inner.all_off.assert_called()
        inner.close.assert_called_once_with()
