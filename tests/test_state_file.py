"""
Tests for the shared state file (config/state.cfg) handling in configuration_manager.

The state file is written by several processes (synchronized_lights, the button
manager, the API). update_state/update_states must re-read the file under an
exclusive lock so that writes from one process don't drop keys written by another.
"""
import configparser

import pytest


@pytest.fixture
def make_config(basic_state_config, tmp_path):
    """Factory returning Configuration instances that share a temp state file."""
    import configuration_manager as cm

    state_file = tmp_path / "state.cfg"

    def _make():
        config = cm.Configuration(basic_state_config)
        config.state_file = str(state_file)
        config.state = configparser.RawConfigParser()
        if state_file.exists():
            config.load_state()
        return config

    _make.state_file = state_file
    return _make


@pytest.fixture
def basic_state_config(temp_config_file):
    return temp_config_file


def _read_state(path):
    parser = configparser.RawConfigParser()
    parser.read(str(path))
    return dict(parser.items('do_not_modify')) if parser.has_section('do_not_modify') else {}


@pytest.mark.unit
class TestStateFile:

    def test_update_state_creates_missing_file(self, make_config):
        config = make_config()
        assert not make_config.state_file.exists()

        config.update_state('play_now', 3)

        assert _read_state(make_config.state_file) == {'play_now': '3'}
        assert config.get_state('play_now') == '3'

    def test_two_instances_do_not_lose_updates(self, make_config):
        """Simulates two processes each holding a stale in-memory copy."""
        first = make_config()
        second = make_config()

        first.update_state('now_playing', '1')
        # second never reloaded, so its in-memory copy lacks now_playing
        second.update_state('play_now', '-1')
        first.update_state('current_song', '4')

        state = _read_state(make_config.state_file)
        assert state == {'now_playing': '1', 'play_now': '-1', 'current_song': '4'}

    def test_update_refreshes_in_memory_state(self, make_config):
        first = make_config()
        second = make_config()

        first.update_state('song_title', 'Carol of the Bells')
        second.update_state('play_now', '0')

        # second picked up first's key as part of its locked re-read
        assert second.get_state('song_title') == 'Carol of the Bells'

    def test_update_states_writes_multiple_keys(self, make_config):
        config = make_config()
        config.update_state('now_playing', '1')

        config.update_states({
            'song_title': 'Jingle Bells',
            'song_path': '/music/jingle.mp3',
            'song_duration': 123.5,
        })

        assert _read_state(make_config.state_file) == {
            'now_playing': '1',
            'song_title': 'Jingle Bells',
            'song_path': '/music/jingle.mp3',
            'song_duration': '123.5',
        }

    def test_update_states_empty_is_noop(self, make_config):
        config = make_config()
        config.update_states({})
        assert not make_config.state_file.exists()

    def test_overwrites_existing_key_without_leftovers(self, make_config):
        config = make_config()
        config.update_state('song_title', 'A very very long song title')
        config.update_state('song_title', 'Short')

        text = make_config.state_file.read_text()
        assert 'long song title' not in text
        assert _read_state(make_config.state_file) == {'song_title': 'Short'}


class TestRefreshState:
    """refresh_state() re-parses state.cfg only when it changed or is stale."""

    def test_skips_unchanged_file(self, make_config, monkeypatch):
        writer = make_config()
        writer.update_states({'play_now': '0'})
        reader = make_config()
        reader.load_state()

        calls = []
        real_load = reader.load_state
        monkeypatch.setattr(reader, 'load_state', lambda: (calls.append(1), real_load()))
        for _ in range(20):
            assert reader.refresh_state() is False
        assert calls == []

    def test_reloads_when_another_process_writes(self, make_config):
        writer = make_config()
        writer.update_states({'play_now': '0'})
        reader = make_config()
        reader.load_state()
        assert reader.get_state('play_now') == '0'

        writer.update_states({'play_now': '1', 'extra': 'x'})
        assert reader.refresh_state() is True
        assert reader.get_state('play_now') == '1'

    def test_same_size_same_tick_write_caught_by_max_age(self, make_config, monkeypatch):
        import configuration_manager as cm_mod

        writer = make_config()
        writer.update_states({'play_now': '0'})
        reader = make_config()
        reader.load_state()

        # Simulate a write the stat signature can't see (same mtime tick, same size)
        writer.update_states({'play_now': '1'})
        monkeypatch.setattr(reader, '_stat_state_file', lambda: reader._state_signature)

        clock = [cm_mod.time.monotonic()]
        monkeypatch.setattr(cm_mod.time, 'monotonic', lambda: clock[0])
        assert reader.refresh_state() is False
        assert reader.get_state('play_now') == '0'

        clock[0] += reader.STATE_MAX_AGE_S + 0.01
        assert reader.refresh_state() is True
        assert reader.get_state('play_now') == '1'

    def test_missing_file_reload_does_not_raise_on_stat(self, make_config, tmp_path):
        config = make_config()
        config.state_file = str(tmp_path / 'nope' / 'state.cfg')
        assert config._stat_state_file() is None
