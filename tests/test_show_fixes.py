"""Regression tests for show-critical bug fixes (Phase 2 of the Nov 2026 sprint)."""

import configparser
import os
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

import hardware_controller
import RunningStats
from api.core.config import APIConfig

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


@pytest.mark.unit
class TestClientOverrides:
    """set_custom_overrides on a client called Channel.inverted(...) - a bool attribute."""

    def test_client_applies_inverted_overrides(self, monkeypatch):
        cm = SimpleNamespace(
            hardware=SimpleNamespace(gpio_len=3),
            lightshow=SimpleNamespace(always_off_channels=[], always_on_channels=[],
                                      invert_channels=[]),
        )
        monkeypatch.setattr(hardware_controller, "cm", cm)

        hc = hardware_controller.Hardware.__new__(hardware_controller.Hardware)
        hc.server = False
        hc.network = SimpleNamespace(get_overrides=True)
        hc.channels = [Mock() for _ in range(3)]

        hc.set_custom_overrides(always_off=[1], always_on=[3], inverted=[2])

        assert hc.channels[1].set_inverted.call_args_list[-1].args == (True,)
        assert hc.channels[0].set_always_off.call_args_list[-1].args == (True,)
        assert hc.channels[2].set_always_on.call_args_list[-1].args == (True,)


@pytest.mark.unit
class TestRunningStats:
    """The first sample and variance() before two samples used an undefined `length`."""

    def test_first_sample(self):
        stats = RunningStats.Stats(4)
        stats.push(np.ones(4, dtype="float32"))
        assert stats.mean().tolist() == [1.0] * 4
        assert stats.variance().tolist() == [0.0] * 4
        assert stats.std().tolist() == [0.0] * 4

    def test_running_values(self):
        stats = RunningStats.Stats(2)
        for value in (1.0, 2.0, 3.0):
            stats.push(np.full(2, value, dtype="float32"))
        assert stats.mean().tolist() == pytest.approx([2.0, 2.0])
        assert stats.variance().tolist() == pytest.approx([1.0, 1.0])


def _api_config(networking):
    config = APIConfig.__new__(APIConfig)
    config.cm = SimpleNamespace(network=SimpleNamespace(networking=networking))
    return config


@pytest.mark.unit
class TestServerClientMode:
    """is_server/is_client read a nonexistent cm.network.mode."""

    @pytest.mark.parametrize("mode", ["off", "server", "serverjson", "sacn_server", ""])
    def test_server_or_standalone(self, mode):
        config = _api_config(mode)
        assert config.is_server() is True
        assert config.is_client() is False

    @pytest.mark.parametrize("mode", ["client", "sacn_client", "Client "])
    def test_client(self, mode):
        config = _api_config(mode)
        assert config.is_client() is True
        assert config.is_server() is False


@pytest.mark.unit
class TestDefaultsCfgValues:
    """Inline `key =  # comment` lines were parsed with the comment as the value."""

    @pytest.mark.parametrize("section,key", [
        ("api", "cloud_url"), ("api", "cloud_key"),
        ("api", "turnstile_site_key"), ("api", "turnstile_secret_key"),
        ("client", "client_id"), ("client", "client_key"),
    ])
    def test_blank_values_are_empty(self, section, key):
        parser = configparser.RawConfigParser(allow_no_value=True)
        parser.read(os.path.join(REPO_ROOT, "config", "defaults.cfg"))
        assert parser.get(section, key) == ""

    def test_no_inline_comments_after_values(self):
        with open(os.path.join(REPO_ROOT, "config", "defaults.cfg")) as fp:
            offenders = [line.rstrip() for line in fp
                         if "=" in line and not line.lstrip().startswith(("#", ";"))
                         and " # " in line.split("=", 1)[1]]
        assert offenders == []


@pytest.mark.unit
class TestPlaylistInvalidLines:
    """get_playlist() used an undefined `log`, so a blank or malformed line killed the show."""

    def test_blank_and_malformed_lines_are_skipped(self, tmp_path):
        import configuration_manager

        playlist = tmp_path / ".playlist"
        playlist.write_text("Song A\t/music/a.mp3\n\nbadline\nSong B\t/music/b.mp3\n\n")

        config = configuration_manager.Configuration.__new__(configuration_manager.Configuration)
        config.playlist_path = str(playlist)
        songs = config.get_playlist()

        assert [s[0] for s in songs] == ["Song A", "Song B"]
        assert songs[0][2] == set()
