"""Event log hooks in py/: physical button presses and the show's song events.

gpiozero and the API are mocked. synchronized_lights.py itself is not run
(it needs audio hardware); its hook is SongTracker.start_from_state/end_reason.
"""
import sys
from unittest.mock import MagicMock

import pytest

if 'gpiozero' not in sys.modules:
    sys.modules['gpiozero'] = MagicMock()

import buttonmanager as bm  # noqa: E402
import event_log  # noqa: E402


def presses():
    events, _ = event_log.fetch_events(types=["button_press"], newest_first=False)
    return [(e["source"], e["details"].get("name"), e["details"].get("mode")) for e in events]


@pytest.fixture
def buttons(monkeypatch, events_db):
    cm = MagicMock()
    cm.get_state.return_value = "0"
    outlet = MagicMock()
    outlet.is_lit = True
    monkeypatch.setattr(bm, "cm", cm)
    monkeypatch.setattr(bm, "outlet", outlet)
    monkeypatch.setattr(bm, "API_ENABLED", False)
    monkeypatch.setattr(bm, "BUTTON_MODE", "direct")
    monkeypatch.setattr(bm, "LOG_BUTTON_PRESSES", True)
    monkeypatch.setattr(bm, "repeat_mode", False)
    monkeypatch.setattr(bm, "audio_cooldown", 0)
    monkeypatch.setattr(bm, "call_api", MagicMock(return_value=True))
    skip = MagicMock()
    skip.pin.number = bm.SKIP_PIN
    return skip


@pytest.mark.unit
class TestButtonPressEvents:

    def test_direct_mode_logs_each_press(self, buttons, monkeypatch):
        bm.songbutton(buttons)
        bm.toggleRepeat()
        monkeypatch.setattr(bm, "audio_cooldown", 0)  # playsong() started a cooldown
        bm.audiobutton()
        assert presses() == [("button", "skip", "direct"), ("button", "repeat", "direct"),
                             ("button", "audio", "direct")]

    def test_api_mode_success_is_left_to_the_api(self, buttons, monkeypatch):
        monkeypatch.setattr(bm, "API_ENABLED", True)
        monkeypatch.setattr(bm, "BUTTON_MODE", "api")
        bm.songbutton(buttons)
        bm.toggleRepeat()
        bm.audiobutton()
        assert presses() == []

    def test_api_failure_logs_locally(self, buttons, monkeypatch):
        monkeypatch.setattr(bm, "API_ENABLED", True)
        monkeypatch.setattr(bm, "BUTTON_MODE", "auto")
        monkeypatch.setattr(bm, "call_api", MagicMock(return_value=False))
        bm.songbutton(buttons)
        monkeypatch.setattr(bm, "audio_cooldown", 0)
        bm.audiobutton()
        assert presses() == [("button", "skip", "auto"), ("button", "audio", "auto")]

    def test_repeat_api_failure_in_api_mode_logs_once(self, buttons, monkeypatch):
        monkeypatch.setattr(bm, "API_ENABLED", True)
        monkeypatch.setattr(bm, "BUTTON_MODE", "api")
        monkeypatch.setattr(bm, "call_api", MagicMock(return_value=False))
        bm.toggleRepeat()
        assert presses() == [("button", "repeat", "api")]

    def test_log_button_presses_off(self, buttons, monkeypatch):
        monkeypatch.setattr(bm, "LOG_BUTTON_PRESSES", False)
        bm.songbutton(buttons)
        bm.audiobutton()
        assert presses() == []

    def test_repeat_loop_replay_is_not_a_press(self, buttons):
        bm.playsong(-1)
        assert presses() == []

    def test_repeat_replay_in_api_mode_tells_the_api(self, buttons, monkeypatch):
        monkeypatch.setattr(bm, "API_ENABLED", True)
        bm.playsong(-1, replay=True)
        bm.call_api.assert_called_once_with("/buttons/skip?repeat=true")
        bm.call_api.reset_mock()
        bm.playsong(-1)
        bm.call_api.assert_called_once_with("/buttons/skip")

    def test_cooldown_press_is_not_logged(self, buttons, monkeypatch):
        monkeypatch.setattr(bm, "audio_cooldown", 1e12)
        bm.audiobutton()
        assert presses() == []

    def test_event_log_failure_does_not_break_buttons(self, buttons, monkeypatch, tmp_path):
        blocker = tmp_path / "f"
        blocker.write_text("x")
        event_log.configure(enabled=True, db_path=str(blocker / "e.db"))
        bm.songbutton(buttons)  # must not raise
        bm.cm.update_state.assert_called_with('play_now', -1)


@pytest.mark.unit
class TestShowSongHook:

    def state(self, **values):
        return lambda name, default="": values.get(name, default)

    def test_start_from_state_reads_song_keys(self, events_db):
        tracker = event_log.SongTracker()
        tracker.start_from_state(self.state(song_title="Carol of the Bells",
                                            song_path="/m/carol.mp3", song_duration="181.2",
                                            song_sequence="onset_bands"), "playlist")
        tracker.end(tracker.end_reason(0))
        events, _ = event_log.fetch_events(newest_first=False)
        start, end = events
        assert start["song"] == "Carol of the Bells"
        assert start["variant"] == "onset_bands"
        assert start["details"] == {"path": "/m/carol.mp3", "duration_s": 181.2,
                                    "mode": "playlist"}
        assert end["details"]["reason"] == "completed"
        assert end["variant"] == "onset_bands"

    def test_title_falls_back_to_file_name(self, events_db):
        tracker = event_log.SongTracker()
        tracker.start_from_state(self.state(song_path="/m/carol.mp3"), "playlist")
        assert event_log.fetch_events()[0][0]["song"] == "carol.mp3"

    def test_end_reason(self):
        assert event_log.SongTracker.end_reason(-1) == "skipped"
        assert event_log.SongTracker.end_reason(3) == "skipped"
        assert event_log.SongTracker.end_reason(0) == "completed"

    def test_state_errors_do_not_raise(self, events_db):
        def broken(name, default=""):
            raise RuntimeError("state file gone")
        tracker = event_log.SongTracker()
        tracker.start_from_state(broken, "playlist")
        assert tracker.end("stopped") is False

    def test_stopped_after_completed_is_ignored(self, events_db):
        """exit_function calls end('stopped') after the play loop already ended."""
        tracker = event_log.SongTracker()
        tracker.start_from_state(self.state(song_title="X"), "playlist")
        tracker.end("completed")
        tracker.end("stopped")
        reasons = [e["details"]["reason"] for e in
                   event_log.fetch_events(types=["song_end"])[0]]
        assert reasons == ["completed"]
