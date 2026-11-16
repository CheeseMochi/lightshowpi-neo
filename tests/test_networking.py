"""Tests for the legacy Pi-to-Pi networking transport (py/networking.py)."""

import pickle
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import networking


def _cm(gpio_len=4):
    network = SimpleNamespace(networking="client", ip_clients=[], port=8888, buffer=1024,
                              channels={}, get_overrides=False)
    return SimpleNamespace(network=network, hardware=SimpleNamespace(gpio_len=gpio_len))


@pytest.mark.unit
class TestReceive:

    def test_valid_packet_is_unpickled(self):
        net = networking.Networking.__new__(networking.Networking)
        net.cm = _cm()
        net.network_buffer = 1024
        net.network_stream = Mock()
        net.network_stream.recvfrom.return_value = (pickle.dumps((1, 2)), ("10.0.0.1", 8888))
        assert net.receive() == (1, 2)

    def test_bad_packet_falls_back_to_all_off(self):
        """Regression: the fallback referenced an undefined global `cm`."""
        net = networking.Networking.__new__(networking.Networking)
        net.cm = _cm(gpio_len=4)
        net.network_buffer = 1024
        net.network_stream = Mock()
        net.network_stream.recvfrom.return_value = (b"not a pickle", ("10.0.0.1", 8888))
        assert list(net.receive()) == [0, 0, 0, 0]
