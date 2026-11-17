"""NanoleafOutput: segment mapping, stream setup and recovery, config wiring.

Uses a fake HTTP client and a fake UDP sender that decodes v2 frames.
"""
import configparser
import time
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

import hardware_controller
import nanoleaf_protocol as nl
from outputs.nanoleaf import NanoleafOutput, parse_color, rainbow, segments
from outputs.threaded import ThreadedOutput

pytestmark = pytest.mark.unit


class FakeClient:
    def __init__(self, host, port, token, timeout):
        self.calls = []
        self.fail = None
        self.leds = 60

    def _r(self, what, status=204):
        self.calls.append(what)
        if self.fail:
            raise OSError(self.fail)
        return nl.Response(status, None, b"")

    def length(self):
        self.calls.append("length")
        return self.leds

    def set_state(self, **kw):
        return self._r(("state", tuple(sorted(kw.items()))))

    def enable_ext_control(self, version="v2"):
        return self._r(("extControl", version))

    def info(self):
        self.calls.append("info")
        return nl.Response(200, {"state": {"on": {"value": False}, "brightness": {"value": 80},
                                           "colorMode": "effect"}}, b"")

    def selected_effect(self):
        return "Northern Lights"

    def select_effect(self, name):
        return self._r(("select", name))


class FakeSender:
    def __init__(self, host, port):
        self.frames = []
        self.closed = False

    def send(self, packets):
        leds = {}
        for p in packets:
            for led, r, g, b, w, t in nl.decode_frame_v2(p):
                leds[led] = (r, g, b)
        self.frames.append(leds)
        return 0.0

    def close(self):
        self.closed = True


def make(channels=(0, 1, 2), clock=None, **kw):
    t = clock or [0.0]
    out = NanoleafOutput("10.0.0.9", "tok", list(channels), client_factory=FakeClient,
                         sender_factory=FakeSender, clock=lambda: t[0], **kw)
    out.start()
    return out, t


class TestMapping:
    def test_segments_cover_strip_evenly(self):
        segs = segments(60, 7)
        assert sum(len(s) for s in segs) == 60
        assert segs[0].start == 0 and segs[-1].stop == 60
        assert max(len(s) for s in segs) - min(len(s) for s in segs) <= 1

    def test_levels_scale_segment_colours(self):
        out, _ = make(colors=[(255, 0, 0), (0, 255, 0), (0, 0, 255)])
        out.write_frame(np.array([1.0, 0.5, 0.0]))
        frame = out._sender.frames[-1]
        assert len(frame) == 60
        assert frame[0] == (255, 0, 0) and frame[19] == (255, 0, 0)
        assert frame[20] == (0, 127, 0)
        assert frame[59] == (0, 0, 0)

    def test_channel_map_and_out_of_range(self):
        out, _ = make(channels=[2, 9], colors=[(255, 255, 255), (255, 255, 255)])
        out.write_frame(np.array([0.0, 0.0, 1.0]))
        frame = out._sender.frames[-1]
        assert frame[0] == (255, 255, 255)   # segment 0 <- channel 2
        assert frame[59] == (0, 0, 0)        # channel 9 doesn't exist -> off

    def test_reverse_and_color_order(self):
        out, _ = make(channels=[0], colors=[(10, 20, 30)], reverse=True, color_order="grb")
        out.leds = 4
        out.write_frame(np.array([1.0]))
        frame = out._sender.frames[-1]
        assert set(frame) == {0, 1, 2, 3}
        assert frame[0] == (20, 10, 30)

    def test_all_on_off(self):
        out, _ = make(colors=[(255, 0, 0)] * 3)
        out.all_on()
        assert set(out._sender.frames[-1].values()) == {(255, 0, 0)}
        out.all_off()
        assert set(out._sender.frames[-1].values()) == {(0, 0, 0)}

    def test_default_colours_are_rainbow(self):
        assert rainbow(3)[0] == (255, 0, 0)
        out, _ = make(channels=range(7))
        assert len(out.colors) == 7 and len(set(out.colors)) == 7

    @pytest.mark.parametrize("text,rgb", [("#ff8000", (255, 128, 0)), ("00ff00", (0, 255, 0)),
                                          ("1, 2, 3", (1, 2, 3))])
    def test_parse_color(self, text, rgb):
        assert parse_color(text) == rgb


class TestStreamSetup:
    def test_first_frame_sets_up_stream(self):
        out, _ = make(brightness=60, restore=True)
        out.write_frame(np.zeros(3))
        calls = out._client.calls
        assert calls[0] == "info"                     # snapshot for restore
        assert "length" in calls
        assert ("state", (("on", True),)) in calls
        assert ("state", (("brightness", 60),)) in calls
        assert calls[-1] == ("extControl", "v2")

    def test_stream_mode_reasserted_periodically(self):
        out, t = make(reassert_s=30)
        out.write_frame(np.zeros(3))
        n = out._client.calls.count(("extControl", "v2"))
        t[0] += 10
        out.write_frame(np.zeros(3))
        assert out._client.calls.count(("extControl", "v2")) == n
        t[0] += 25
        out.write_frame(np.zeros(3))
        assert out._client.calls.count(("extControl", "v2")) == n + 1
        assert "info" not in out._client.calls       # restore off by default: no snapshot

    def test_http_failure_raises_for_threaded_backoff(self):
        out, _ = make()
        out._client.fail = "no route to host"
        with pytest.raises(OSError):
            out.write_frame(np.zeros(3))

    def test_rejected_stream_mode_raises(self):
        out, _ = make()
        out._client.enable_ext_control = lambda v="v2": nl.Response(400, None, b"")
        with pytest.raises(nl.NanoleafError):
            out.write_frame(np.zeros(3))

    def test_close_blacks_out_and_restores(self):
        out, _ = make(restore=True)
        out.write_frame(np.ones(3))
        sender = out._sender
        out.close()
        assert set(sender.frames[-1].values()) == {(0, 0, 0)}
        assert ("select", "Northern Lights") in out._client.calls
        assert sender.closed

    def test_requires_host_token_channels(self):
        with pytest.raises(ValueError):
            NanoleafOutput("", "tok", [0], client_factory=FakeClient, sender_factory=FakeSender)
        with pytest.raises(ValueError):
            NanoleafOutput("h", "", [0], client_factory=FakeClient, sender_factory=FakeSender)
        with pytest.raises(ValueError):
            NanoleafOutput("h", "t", [0], color_order="xyz",
                           client_factory=FakeClient, sender_factory=FakeSender)


def test_close_without_restore_leaves_strip_dark():
    out, _ = make()
    out.write_frame(np.ones(3))
    sender = out._sender
    out.close()
    assert set(sender.frames[-1].values()) == {(0, 0, 0)}
    assert not [c for c in out._client.calls if isinstance(c, tuple) and c[0] == "select"]


class TestThreadedRecovery:
    def test_lights_dropping_off_wifi_never_reach_the_caller(self):
        inner, _ = make()
        threaded = ThreadedOutput(inner, initial_backoff_s=0.02, max_backoff_s=0.05)
        threaded.start()
        try:
            inner._client.fail = "host unreachable"
            for _ in range(20):
                threaded.write_frame(np.ones(3))   # must never raise
                time.sleep(0.005)
            deadline = time.time() + 2
            while threaded.failures == 0 and time.time() < deadline:
                time.sleep(0.01)
            assert threaded.failures > 0
            inner._client.fail = None              # lights come back
            deadline = time.time() + 2
            while not (threaded.healthy and threaded.frames_sent) and time.time() < deadline:
                threaded.write_frame(np.ones(3))
                time.sleep(0.02)
            assert threaded.healthy and threaded.frames_sent > 0
        finally:
            threaded.close()


class TestConfigWiring:
    def _cm(self, text, gpio_len=7):
        import configuration_manager
        # Same parser settings as configuration_manager (no inline comment stripping)
        cfg = configparser.RawConfigParser(allow_no_value=True)
        cfg.read_string(text)
        c = configuration_manager.Configuration.__new__(configuration_manager.Configuration)
        c.config = cfg
        c.set_outputs()
        c.hardware = SimpleNamespace(gpio_len=gpio_len)
        return c

    def _hc(self, cm):
        hc = hardware_controller.Hardware.__new__(hardware_controller.Hardware)
        hc.cm = cm
        hc.outputs = []
        return hc

    def test_section_builds_threaded_nanoleaf(self, monkeypatch):
        monkeypatch.setattr(nl, "NanoleafClient", FakeClient)
        cm = self._cm("[output:nanoleaf]\ntype = nanoleaf\nhost = 192.168.104.164\ntoken = abc\n"
                      "channels = 0,1,2\nmax_fps = 10    # inline comment\nreverse = true\n")
        hc = self._hc(cm)
        hc._add_configured_outputs()
        assert len(hc.outputs) == 1
        out = hc.outputs[0]
        assert isinstance(out, ThreadedOutput) and isinstance(out.inner, NanoleafOutput)
        assert out.max_fps == 10.0
        assert out.inner.channels == [0, 1, 2] and out.inner.reverse is True

    def test_colors_with_spaces_and_hashes(self, monkeypatch):
        monkeypatch.setattr(nl, "NanoleafClient", FakeClient)
        cm = self._cm("[output:nl]\ntype = nanoleaf\nhost = h\ntoken = t\nchannels = 0,1\n"
                      "colors = #ff0000; #00ff00\n")
        hc = self._hc(cm)
        hc._add_configured_outputs()
        assert hc.outputs[0].inner.colors == [(255, 0, 0), (0, 255, 0)]

    def test_default_channels_are_all(self, monkeypatch):
        monkeypatch.setattr(nl, "NanoleafClient", FakeClient)
        cm = self._cm("[output:nl]\ntype = nanoleaf\nhost = h\ntoken = t\n", gpio_len=7)
        hc = self._hc(cm)
        hc._add_configured_outputs()
        assert hc.outputs[0].inner.channels == list(range(7))

    def test_disabled_unknown_and_broken_sections_are_skipped(self, caplog, tmp_path):
        cm = self._cm("[output:off]\ntype = nanoleaf\nenabled = false\nhost = h\ntoken = t\n"
                      "[output:what]\ntype = hue\n"
                      "[output:broken]\ntype = nanoleaf\nhost = h\n"
                      f"token_file = {tmp_path}/none.json\n")
        hc = self._hc(cm)
        with caplog.at_level("ERROR"):
            hc._add_configured_outputs()
        assert hc.outputs == []
        assert "unknown type 'hue'" in caplog.text
        assert "output broken (nanoleaf) not started" in caplog.text

    def test_token_from_token_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(nl, "NanoleafClient", FakeClient)
        path = nl.save_token("192.168.104.164", "saved-token", str(tmp_path / "t.json"))
        cm = self._cm(f"[output:nl]\ntype = nanoleaf\nhost = 192.168.104.164\ntoken_file = {path}\n")
        hc = self._hc(cm)
        hc._add_configured_outputs()
        assert len(hc.outputs) == 1

    def test_no_output_sections_by_default(self):
        import os
        cfg = configparser.RawConfigParser()
        cfg.read(os.path.join(os.path.dirname(__file__), "..", "config", "defaults.cfg"))
        assert not [s for s in cfg.sections() if s.startswith("output:")]
