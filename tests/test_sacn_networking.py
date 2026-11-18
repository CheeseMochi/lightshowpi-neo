"""sACN (E1.31) Pi-to-Pi networking: config, server encode, client decode, end to end.

All sockets are fakes, so these run anywhere.
"""
import configparser
import errno
import json
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

import hardware_controller
import networking
import networking_sacn
from e131packet import E131Packet
from outputs.led import LedOutput

pytestmark = pytest.mark.unit


class FakeSendSocket:
    def __init__(self, fail_with=None):
        self.sent = []
        self.fail_with = fail_with

    def sendto(self, data, addr):
        if self.fail_with is not None:
            raise OSError(self.fail_with, "boom")
        self.sent.append((bytes(data), addr))


class FakeRecvSocket:
    def __init__(self, packets):
        self.packets = list(packets)

    def recvfrom(self, size):
        if not self.packets:
            raise BlockingIOError()
        return self.packets.pop(0), ("10.0.0.1", 5568)


def make_cm(mode, gpio_len=7, address="", universe_start=100, boundary=512,
            multicast=False, channels=None):
    network = SimpleNamespace(
        networking=mode, sacn_address=address, sacn_port=5568, sacn_control_port=8889,
        universe_start=universe_start, universe_boundary=boundary,
        enable_multicast=multicast, sacn_priority=100,
        channels=channels if channels is not None else list(range(gpio_len)),
        get_overrides=True)
    return SimpleNamespace(network=network, hardware=SimpleNamespace(gpio_len=gpio_len))


def make_sacn(monkeypatch, mode, **kw):
    monkeypatch.setattr(networking_sacn.SACNNetworking, "setup", lambda self: None)
    net = networking_sacn.SACNNetworking(make_cm(mode, **kw))
    net.ready = True
    if mode == "sacn_server":
        net.target_address = net.target_addresses[0] if net.target_addresses else "<broadcast>"
        net.network_stream = FakeSendSocket()
        net.control_stream = FakeSendSocket()
    return net


def packets_of(sock):
    return [data for data, _ in sock.sent]


class TestConfig:
    def _network(self, extra):
        import configuration_manager
        cfg = configparser.RawConfigParser()
        cfg.read_string("[network]\nnetworking = sacn_server\nip_clients =\nport = 8888\n"
                        "buffer = 1024\nchannels =\nget_overrides = False\n" + extra)
        c = configuration_manager.Configuration.__new__(configuration_manager.Configuration)
        c.config = cfg
        c.gpio_len = 7
        c.set_network()
        return c.network

    def test_sacn_keys_are_read(self):
        net = self._network("sacn_address = 192.168.3.50, 192.168.3.51\nsacn_port = 6000\n"
                            "universe_start = 200\nenable_multicast = True\n")
        assert net.sacn_address == "192.168.3.50, 192.168.3.51"
        assert net.sacn_port == 6000 and net.universe_start == 200 and net.enable_multicast is True

    def test_defaults_when_missing(self):
        net = self._network("")
        assert net.sacn_address == "" and net.sacn_port == 5568
        assert net.universe_start == 100 and net.universe_boundary == 512

    def test_defaults_cfg_universe_is_clear_of_led_universe_1(self):
        import os
        cfg = configparser.RawConfigParser()
        cfg.read(os.path.join(os.path.dirname(__file__), "..", "config", "defaults.cfg"))
        assert cfg.getint("network", "universe_start") != 1


class TestModes:
    @pytest.mark.parametrize("mode,server,client", [
        ("server", True, False), ("serverjson", True, False), ("sacn_server", True, False),
        ("client", False, True), ("sacn_client", False, True), ("off", False, False)])
    def test_mode_helpers(self, mode, server, client):
        assert networking.is_server_mode(mode) is server
        assert networking.is_client_mode(mode) is client

    def test_factory_picks_sacn(self, monkeypatch):
        monkeypatch.setattr(networking_sacn.SACNNetworking, "setup", lambda self: None)
        assert isinstance(networking.create(make_cm("sacn_client")), networking_sacn.SACNNetworking)

    def test_factory_picks_legacy(self, monkeypatch):
        monkeypatch.setattr(networking.Networking, "setup", lambda self: None)
        cm = make_cm("server")
        cm.network.ip_clients = [""]
        cm.network.port = 8888
        cm.network.buffer = 1024
        assert type(networking.create(cm)) is networking.Networking


class TestServerSend:
    def test_bcastarray_unicasts_to_every_client(self, monkeypatch):
        net = make_sacn(monkeypatch, "sacn_server", address="10.0.0.2, 10.0.0.3")
        net.broadcast(networking.BcastArray(array=np.array([1.0, 0.5, 0, 0, 0, 0, 0.25]),
                                            use_overrides=True))
        addrs = [a for _, a in net.network_stream.sent]
        assert addrs == [("10.0.0.2", 5568), ("10.0.0.3", 5568)]
        parsed = net.parse_e131_packet(packets_of(net.network_stream)[0])
        assert parsed["universe"] == 100
        assert parsed["dmx_data"] == [255, 127, 0, 0, 0, 0, 63]

    def test_bcastpin_resends_full_frame(self, monkeypatch):
        net = make_sacn(monkeypatch, "sacn_server", address="10.0.0.2")
        net.broadcast(networking.BcastArray(array=np.full(7, 1.0), use_overrides=False))
        net.broadcast(networking.BcastPin(pin=3, brightness=0.0, use_overrides=False))
        parsed = net.parse_e131_packet(packets_of(net.network_stream)[-1])
        assert parsed["dmx_data"] == [255, 255, 255, 0, 255, 255, 255]

    def test_plain_list_keepalive(self, monkeypatch):
        net = make_sacn(monkeypatch, "sacn_server", address="10.0.0.2")
        net.broadcast([1.0, 0.0, 1.0])
        assert net.parse_e131_packet(packets_of(net.network_stream)[0])["dmx_data"] == [255, 0, 255]

    def test_overrides_go_to_control_plane(self, monkeypatch):
        net = make_sacn(monkeypatch, "sacn_server", address="10.0.0.2")
        net.broadcast(networking.BcastOverrides(always_off=[3], always_on=[], inverted=[]))
        assert net.network_stream.sent == []
        data, addr = net.control_stream.sent[0]
        assert addr == ("10.0.0.2", 8889)
        assert json.loads(data)["always_off"] == [3]

    def test_multiple_universes(self, monkeypatch):
        net = make_sacn(monkeypatch, "sacn_server", address="10.0.0.2", gpio_len=10, boundary=4)
        net.broadcast(np.linspace(0, 1, 10))
        universes = [net.parse_e131_packet(p)["universe"] for p in packets_of(net.network_stream)]
        assert universes == [100, 101, 102]

    @pytest.mark.parametrize("err", [errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EBADF])
    def test_send_errors_never_raise(self, monkeypatch, err):
        net = make_sacn(monkeypatch, "sacn_server", address="10.0.0.2")
        net.network_stream = FakeSendSocket(fail_with=err)
        net.broadcast(np.zeros(7))

    def test_sequence_increments(self, monkeypatch):
        net = make_sacn(monkeypatch, "sacn_server", address="10.0.0.2")
        for _ in range(3):
            net.broadcast(np.zeros(7))
        seqs = [net.parse_e131_packet(p)["sequence"] for p in packets_of(net.network_stream)]
        assert seqs == [0, 1, 2]


def packet(levels, universe=100, sequence=0):
    data = [int(v * 255) for v in levels]
    return bytes(E131Packet(name="LightShowPi", universe=universe, data=data,
                            sequence=sequence).packet_data)


class TestClientReceive:
    def _client(self, monkeypatch, packets, **kw):
        net = make_sacn(monkeypatch, "sacn_client", **kw)
        net.network_stream = FakeRecvSocket(packets)
        return net

    def test_levels_round_trip(self, monkeypatch):
        net = self._client(monkeypatch, [packet([1.0, 0, 0, 1.0, 0, 0, 1.0])])
        (levels,) = net.receive()
        np.testing.assert_allclose(levels, [1, 0, 0, 1, 0, 0, 1])

    def test_all_off_frame_is_all_zero(self, monkeypatch):
        net = self._client(monkeypatch, [packet([1.0] * 7, sequence=0), packet([0.0] * 7, sequence=1)])
        net.receive()
        (levels,) = net.receive()
        assert not levels.any()

    def test_out_of_order_packet_dropped(self, monkeypatch):
        net = self._client(monkeypatch, [packet([1.0] * 7, sequence=10), packet([0.0] * 7, sequence=9)])
        assert net.receive() is not None
        assert net.receive() is None

    def test_sequence_wraparound_accepted(self, monkeypatch):
        net = self._client(monkeypatch, [packet([1.0] * 7, sequence=255), packet([0.0] * 7, sequence=0)])
        net.receive()
        assert net.receive() is not None

    def test_foreign_universe_ignored(self, monkeypatch):
        # e.g. the ESPixelStick's LED universe 1 on the same port
        net = self._client(monkeypatch, [packet([1.0] * 7, universe=1)])
        assert net.receive() is None

    def test_garbage_ignored(self, monkeypatch):
        net = self._client(monkeypatch, [b"not e1.31"])
        assert net.receive() is None

    def test_multi_universe_frame_assembled(self, monkeypatch):
        net = self._client(monkeypatch, [packet([1, 1, 1, 1], universe=100),
                                         packet([0.0, 1.0], universe=101)], boundary=4)
        net.receive()
        (levels,) = net.receive()
        np.testing.assert_allclose(levels, [1, 1, 1, 1, 0, 1])

    def test_control_plane_is_non_blocking(self, monkeypatch):
        net = make_sacn(monkeypatch, "sacn_client")
        net.control_stream = FakeRecvSocket([])
        assert net.receive_control_message() is None


class TestEndToEnd:
    def test_show_frame_reaches_client_relays_and_leds(self, monkeypatch):
        cm = SimpleNamespace(hardware=SimpleNamespace(gpio_len=7, physical_gpio_len=7,
                                                      gpio_pins=list(range(7))))
        monkeypatch.setattr(hardware_controller, "cm", cm)

        server_net = make_sacn(monkeypatch, "sacn_server", address="10.0.0.2")
        server = hardware_controller.Hardware.__new__(hardware_controller.Hardware)
        server.network = server_net
        server.server = networking.is_server_mode(server_net.networking)
        server.broadcast = server_net.broadcast
        server.channels = [Mock() for _ in range(7)]
        server.outputs = []
        server.set_array(np.array([1.0, 0, 1.0, 0, 1.0, 0, 1.0], dtype="float32"), True)

        # Production client: 8 relays, the 8th mirrors server channel 6
        client_net = make_sacn(monkeypatch, "sacn_client", gpio_len=8,
                               channels=[0, 1, 2, 3, 4, 5, 6, 6])
        client_net.network_stream = FakeRecvSocket(packets_of(server_net.network_stream))
        client_net.control_stream = FakeRecvSocket([])
        client = hardware_controller.Hardware.__new__(hardware_controller.Hardware)
        client.network = client_net
        client.server = False
        client.channels = [Mock() for _ in range(8)]
        led = Mock()
        client.outputs = [LedOutput(led, "MIRROR", 8, 8, slice_levels=False)]
        client.receive_and_process()

        assert [c.set_action.call_args.args[1] for c in client.channels] == \
            pytest.approx([1, 0, 1, 0, 1, 0, 1, 1])
        np.testing.assert_allclose(led.write_all.call_args.args[0], [1, 0, 1, 0, 1, 0, 1])

    def test_client_skips_dropped_packet(self, monkeypatch):
        client_net = make_sacn(monkeypatch, "sacn_client")
        client_net.network_stream = FakeRecvSocket([b"junk"])
        client_net.control_stream = FakeRecvSocket([])
        client = hardware_controller.Hardware.__new__(hardware_controller.Hardware)
        client.network = client_net
        client.server = False
        client.channels = [Mock() for _ in range(7)]
        client.outputs = []
        client.receive_and_process()  # used to TypeError on None[0]
        for c in client.channels:
            c.set_action.assert_not_called()


class TestOverridesEndToEnd:
    """Server set_custom_overrides -> control plane -> client channels (bench crash 2026-11-19)."""

    def _cm(self, monkeypatch, gpio_len):
        cm = SimpleNamespace(
            hardware=SimpleNamespace(gpio_len=gpio_len, physical_gpio_len=gpio_len,
                                     gpio_pins=list(range(gpio_len))),
            lightshow=SimpleNamespace(always_off_channels=[], always_on_channels=[],
                                      invert_channels=[]))
        monkeypatch.setattr(hardware_controller, "cm", cm)

    def _hardware(self, net, n, server):
        hw = hardware_controller.Hardware.__new__(hardware_controller.Hardware)
        hw.network = net
        hw.server = server
        hw.broadcast = net.broadcast
        hw.channels = [Mock() for _ in range(n)]
        hw.outputs = []
        return hw

    def _send_and_receive(self, monkeypatch, get_overrides):
        self._cm(monkeypatch, 7)
        server_net = make_sacn(monkeypatch, "sacn_server", address="10.0.0.2")
        server = self._hardware(server_net, 7, server=True)
        server.set_custom_overrides(always_off=[2], always_on=[3], inverted=[4])

        client_net = make_sacn(monkeypatch, "sacn_client")
        client_net.get_overrides = get_overrides
        client_net.network_stream = FakeRecvSocket([])
        client_net.control_stream = FakeRecvSocket(
            [data for data, _ in server_net.control_stream.sent])
        client = self._hardware(client_net, 7, server=False)
        client.receive_and_process()  # used to raise AttributeError: get_overrides
        return client

    def test_client_reads_get_overrides_from_config(self, monkeypatch):
        assert make_sacn(monkeypatch, "sacn_client").get_overrides is True

    def test_overrides_applied_on_client(self, monkeypatch):
        client = self._send_and_receive(monkeypatch, get_overrides=True)
        # Channel numbers in overrides are 1-based
        assert client.channels[1].set_always_off.call_args_list[-1].args == (True,)
        assert client.channels[2].set_always_on.call_args_list[-1].args == (True,)
        assert client.channels[3].set_inverted.call_args_list[-1].args == (True,)
        assert client.channels[0].set_always_off.call_args_list[-1].args == (False,)

    def test_overrides_ignored_when_get_overrides_false(self, monkeypatch):
        client = self._send_and_receive(monkeypatch, get_overrides=False)
        # Only the client's own config overrides (set_overrides: all False) are applied
        for channel in client.channels:
            assert all(c.args == (False,) for c in channel.set_always_off.call_args_list)
