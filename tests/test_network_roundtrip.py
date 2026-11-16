"""Server -> client round trip over the legacy pickle protocol, with fake sockets.

Regressions covered:
- clients only wrote LEDs for raw ndarrays, but the server sends BcastArray
- BcastPin/int handling indexed the client channel map with the server pin
- receive() referenced an undefined global cm on a bad packet
- socket errors were indexed like tuples (TypeError on Python 3)
- serverjson tried to round() the BcastArray namedtuple
"""
import errno
import json
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

import hardware_controller
import networking
from outputs.led import LedOutput

pytestmark = pytest.mark.unit


class FakeSocket:
    def __init__(self, fail_with=None):
        self.sent = []
        self.fail_with = fail_with

    def sendto(self, data, addr):
        if self.fail_with is not None:
            raise OSError(self.fail_with, "boom")
        self.sent.append((data, addr))


class FakeRecvSocket:
    def __init__(self, packets):
        self.packets = list(packets)

    def recvfrom(self, bufsize):
        return self.packets.pop(0), ("10.0.0.1", 8888)


def make_net(mode, sock, gpio_len=4, ip_clients=("10.0.0.2",)):
    net = networking.Networking.__new__(networking.Networking)
    net.cm = SimpleNamespace(hardware=SimpleNamespace(gpio_len=gpio_len))
    net.networking = mode
    net.ip_clients = list(ip_clients)
    net.port = 8888
    net.network_buffer = 65536
    net.network_stream = sock
    net.playing = True
    net.ready = True
    return net


def make_server(monkeypatch, gpio_len=4):
    cm = SimpleNamespace(hardware=SimpleNamespace(gpio_len=gpio_len, physical_gpio_len=gpio_len,
                                                  gpio_pins=list(range(gpio_len))))
    monkeypatch.setattr(hardware_controller, "cm", cm)
    sock = FakeSocket()
    hc = hardware_controller.Hardware.__new__(hardware_controller.Hardware)
    hc.network = make_net("server", sock, gpio_len)
    hc.server = True
    hc.broadcast = hc.network.broadcast
    hc.channels = [Mock() for _ in range(gpio_len)]
    hc.led = None
    return hc, sock


def make_client(packets, channels, with_led=True):
    hc = hardware_controller.Hardware.__new__(hardware_controller.Hardware)
    hc.network = make_net("client", FakeRecvSocket(packets))
    hc.network.channels = channels
    hc.network.get_overrides = False
    hc.server = False
    hc.channels = [Mock() for _ in channels]
    hc.led = [Mock()] if with_led else None
    hc.outputs = [LedOutput(hc.led[0], "EXTEND", 2, 4, slice_levels=False)] if with_led else []
    return hc


def levels_set(hc):
    return [c.set_action.call_args.args[1] for c in hc.channels]


class TestArrayRoundTrip:
    def test_bcastarray_reaches_client_leds_and_channels(self, monkeypatch):
        server, sock = make_server(monkeypatch)
        brightness = np.array([0.1, 0.2, 0.3, 0.4], dtype="float32")
        server.set_array(brightness, use_overrides=True)
        assert len(sock.sent) == 1

        client = make_client([sock.sent[0][0]], channels=[3, 2, 1, 0])
        client.receive_and_process()

        written = client.led[0].write_all.call_args.args[0]
        np.testing.assert_allclose(written, brightness)
        assert levels_set(client) == pytest.approx([0.4, 0.3, 0.2, 0.1])
        assert all(c.set_action.call_args.args[0] is True for c in client.channels)

    def test_raw_ndarray_still_supported(self):
        import pickle
        arr = np.array([1.0, 0.0], dtype="float32")
        client = make_client([pickle.dumps((arr,))], channels=[0, 1])
        client.receive_and_process()
        np.testing.assert_allclose(client.led[0].write_all.call_args.args[0], arr)
        assert levels_set(client) == [1.0, 0.0]

    def test_negative_and_out_of_range_channels_are_off(self, monkeypatch):
        server, sock = make_server(monkeypatch, gpio_len=2)
        server.set_array(np.array([1.0, 1.0], dtype="float32"))
        client = make_client([sock.sent[0][0]], channels=[-1, 0, 7], with_led=False)
        client.receive_and_process()
        assert levels_set(client) == [0, 1.0, 0]


class TestPinRoundTrip:
    def test_bcastpin_to_client_with_fewer_channels(self, monkeypatch):
        server, sock = make_server(monkeypatch, gpio_len=8)
        server.set_light(6, use_overrides=False, brightness=1.0)

        # client maps only two channels; server pin 6 is its channel 1
        client = make_client([sock.sent[0][0]], channels=[5, 6], with_led=False)
        client.receive_and_process()  # used to IndexError on channels[6]

        client.channels[0].set_action.assert_not_called()
        assert client.channels[1].set_action.call_args.args == (False, 1.0)


class TestBadPackets:
    def test_garbled_packet_turns_lights_off_for_a_frame(self):
        client = make_client([b"not a pickle"], channels=[0, 1, 2, 3], with_led=False)
        client.receive_and_process()
        assert levels_set(client) == [0.0, 0.0, 0.0, 0.0]


class TestSendErrors:
    @pytest.mark.parametrize("err", [errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EBADF])
    def test_send_error_does_not_raise(self, err):
        net = make_net("server", FakeSocket(fail_with=err))
        net.broadcast(networking.BcastArray(array=np.zeros(2), use_overrides=False))

    def test_serverjson_unwraps_bcastarray(self):
        sock = FakeSocket()
        net = make_net("serverjson", sock)
        net.broadcast(networking.BcastArray(array=np.array([0.12345, 1.0]), use_overrides=True))
        payload = json.loads(sock.sent[0][0].decode())
        assert payload == {"data": ["0.123", "1.0"]}

    def test_serverjson_skips_pin_messages(self):
        sock = FakeSocket()
        net = make_net("serverjson", sock)
        net.broadcast(networking.BcastPin(pin=1, brightness=1.0, use_overrides=False))
        assert sock.sent == []


class TestUnsupportedSacnModes:
    @pytest.mark.parametrize("mode", ["sacn_server", "sacn_client"])
    def test_sacn_mode_is_loud_but_not_fatal(self, mode, caplog, capsys):
        net = make_net(mode, sock=None)
        with caplog.at_level("ERROR"):
            net.setup()
        assert net.ready is True
        assert net.network_stream is None
        assert "not supported yet" in caplog.text
        assert "running standalone" in capsys.readouterr().err


class TestAmbientKeepalive:
    def test_plain_list_broadcast_reaches_client(self, caplog):
        import pickle
        # synchronized_lights.ambient_mode: hc.network.broadcast(brightness_array)
        client = make_client([pickle.dumps(([1.0, 0.0, 0.5],))], channels=[0, 1, 2])
        with caplog.at_level("WARNING"):
            client.receive_and_process()
        assert "unrecognized" not in caplog.text
        assert levels_set(client) == [1.0, 0.0, 0.5]
        np.testing.assert_allclose(client.led[0].write_all.call_args.args[0], [1.0, 0.0, 0.5])
