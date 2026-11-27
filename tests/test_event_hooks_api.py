"""Event log hooks in the API: show start/stop/skip/error, scheduler, button
actions, test mode and the admin api_action audit."""
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import event_log
from api.core.auth import get_current_user, require_admin
from api.models.schemas import LightshowState
from api.services.button_manager import ButtonAction, ButtonManagerService
from api.services.lightshow_manager import LightshowManager
from api.services.test_mode_manager import TestModeManager

SONG = """import sys, time, os
if os.environ.get("SONG_PRINT"):
    print(os.environ["SONG_PRINT"], flush=True)
time.sleep(float(os.environ.get("SONG_SECONDS", "0.3")))
sys.exit(int(os.environ.get("SONG_EXIT", "0")))
"""


def events(types=None):
    return event_log.fetch_events(types=types, newest_first=False)[0]


class FakeConfig:
    def __init__(self, playlist):
        self.lightshow = SimpleNamespace(playlist_path=str(playlist))
        self.state = {"song_title": "Carol"}

    def reload_state(self):
        pass

    def get_state(self, key, default=""):
        return self.state.get(key, default)

    def update_state(self, key, value):
        self.state[key] = value


@pytest.fixture
def mgr(tmp_path, monkeypatch, events_db):
    script = tmp_path / "fake_show.py"
    script.write_text(SONG)
    playlist = tmp_path / "xmas.playlist"
    playlist.write_text("Song\t/x.m4a\n")
    monkeypatch.setenv("SYNCHRONIZED_LIGHTS_HOME", str(tmp_path))
    m = LightshowManager(FakeConfig(playlist))
    m.synchronized_lights_script = str(script)
    m.startup_check_seconds = 0.05
    m.min_run_seconds = 0.2
    m.fast_failure_pause = 0.05
    yield m
    m.stop()


@pytest.mark.integration
class TestLightshowManagerEvents:

    def test_start_and_stop(self, mgr):
        assert mgr.start(source="api", user="steve") is True
        assert mgr.stop(source="schedule") is True
        evs = events()
        assert [(e["type"], e["source"]) for e in evs] == [("show_start", "api"),
                                                           ("show_stop", "schedule")]
        assert evs[0]["details"]["user"] == "steve"
        assert evs[0]["details"]["mode"] == "playlist"
        assert evs[0]["details"]["playlist"].endswith("xmas.playlist")
        assert "user" not in evs[1]["details"]

    def test_stop_when_idle_records_nothing(self, mgr):
        assert mgr.stop() is True
        assert events() == []

    def test_skip_records_song_and_user(self, mgr):
        assert mgr.skip(source="button", user="admin") is True
        (ev,) = events()
        assert ev["type"] == "skip"
        assert ev["source"] == "button"
        assert ev["song"] == "Carol"
        assert ev["details"] == {"user": "admin"}

    def test_failed_start_records_error(self, mgr, tmp_path):
        assert mgr.start(playlist=str(tmp_path / "missing.playlist"), user="steve") is False
        (ev,) = events()
        assert ev["type"] == "error"
        assert ev["details"]["action"] == "start"
        assert "not found" in ev["details"]["message"]

    def test_fast_failures_record_error(self, mgr, monkeypatch):
        monkeypatch.setenv("SONG_SECONDS", "0.1")
        monkeypatch.setenv("SONG_EXIT", "1")
        mgr.startup_check_seconds = 0.0
        # Wide margin: Python startup on a Pi alone can exceed the fixture's
        # 0.2s threshold, so a 0.1s song would never count as a fast failure.
        mgr.min_run_seconds = 5.0
        mgr.start()
        deadline = time.time() + 10
        while mgr.state != LightshowState.ERROR and time.time() < deadline:
            time.sleep(0.05)
        assert mgr.state == LightshowState.ERROR
        errors = events(["error"])
        assert errors and errors[-1]["details"]["action"] == "playlist_loop"
        assert errors[-1]["details"]["exit_code"] == 1
        assert "exit code 1" in errors[-1]["details"]["message"]

    def test_fast_failure_event_keeps_show_output(self, mgr, monkeypatch):
        monkeypatch.setenv("SONG_SECONDS", "0.1")
        monkeypatch.setenv("SONG_EXIT", "1")
        monkeypatch.setenv("SONG_PRINT", "RuntimeError: audio device busy")
        mgr.startup_check_seconds = 0.0
        mgr.min_run_seconds = 5.0
        mgr.start()
        deadline = time.time() + 10
        while mgr.state != LightshowState.ERROR and time.time() < deadline:
            time.sleep(0.05)
        (err,) = [e for e in events(["error"]) if e["details"]["action"] == "playlist_loop"]
        assert err["details"]["output"][-1] == "RuntimeError: audio device busy"
        assert "RuntimeError: audio device busy" in err["details"]["message"]

    def test_event_log_failure_does_not_break_start(self, mgr, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("x")
        event_log.configure(enabled=True, db_path=str(blocker / "events.db"))
        assert mgr.start(user="steve") is True
        assert mgr.skip() is True
        assert mgr.stop() is True


@pytest.mark.unit
class TestButtonServiceEvents:

    def test_api_press_is_logged_once_with_user(self, events_db):
        show = Mock(spec=LightshowManager)
        show.is_running.return_value = True
        svc = ButtonManagerService({"button_manager": {"enabled": True}}, show)
        svc.handle_button_action(ButtonAction.SKIP, user="buttons")
        (ev,) = events(["button_press"])
        assert ev["source"] == "api"
        assert ev["details"] == {"name": "skip", "mode": "api", "user": "buttons"}
        show.skip.assert_called_once_with(source="button", user="buttons")

    def test_repeat_replay_is_not_a_press(self, events_db):
        from api.routers import buttons as buttons_router
        show = Mock(spec=LightshowManager)
        show.is_running.return_value = True
        svc = ButtonManagerService({"button_manager": {"enabled": True}}, show)
        app = FastAPI()
        app.include_router(buttons_router.router)
        app.dependency_overrides[buttons_router.get_button_manager] = lambda: svc
        app.dependency_overrides[get_current_user] = lambda: {"username": "buttons"}
        client = TestClient(app)
        assert client.post("/api/buttons/skip?repeat=true").status_code == 200
        assert events(["button_press"]) == []
        assert client.post("/api/buttons/skip").status_code == 200
        assert len(events(["button_press"])) == 1
        assert show.skip.call_count == 2

    def test_start_from_button_has_button_source(self, events_db):
        show = Mock(spec=LightshowManager)
        show.is_running.return_value = False
        svc = ButtonManagerService({"button_manager": {"enabled": True}}, show)
        svc.handle_button_action(ButtonAction.REPEAT_TOGGLE)
        show.start.assert_called_once_with(source="button", user=None)


@pytest.mark.unit
class TestTestModeEvents:

    def test_reap_records_exit_with_user(self, events_db):
        tm = TestModeManager(lightshow_manager=Mock(spec=LightshowManager))
        tm.entered_by = "steve"
        tm._reap(reason="timeout")
        (ev,) = events(["test_mode"])
        assert ev["details"] == {"action": "exit", "reason": "timeout", "user": "steve"}

    @pytest.mark.integration
    def test_enter_and_exit(self, events_db, tmp_path):
        script = tmp_path / "fake_serve.py"
        py_dir = str(__import__("pathlib").Path(__file__).parent.parent / "py")
        script.write_text(
            "import sys\nsys.path.insert(0, %r)\n"
            "from unittest.mock import MagicMock\nimport hardware_controller\n"
            "hw = MagicMock()\nhw.cm.hardware.gpio_len = 4\nhw.cm.hardware.is_pin_pwm = [False]*4\n"
            "hardware_controller.serve(hw, patterns=MagicMock())\n" % py_dir)
        show = Mock(spec=LightshowManager)
        show.is_running.return_value = False
        tm = TestModeManager(lightshow_manager=show, command=[sys.executable, str(script)],
                             start_timeout=30)
        try:
            tm.enter(by="steve")
            tm.exit()
        finally:
            tm.shutdown()
        actions = [(e["details"]["action"], e["details"].get("user")) for e in events(["test_mode"])]
        assert actions == [("enter", "steve"), ("exit", "steve")]


@pytest.mark.unit
class TestApiActionAudit:

    @pytest.fixture
    def client(self, events_db):
        app = FastAPI()

        @app.post("/api/things/{thing_id}")
        def change(thing_id: int, user: dict = Depends(require_admin)):
            return {"ok": True}

        @app.get("/api/things")
        def read(user: dict = Depends(require_admin)):
            return []

        @app.post("/api/test/channel")
        def channel(user: dict = Depends(require_admin)):
            return {}

        app.dependency_overrides[get_current_user] = lambda: {"username": "steve", "id": 1}
        return TestClient(app)

    def test_admin_change_is_recorded(self, client):
        assert client.post("/api/things/7").status_code == 200
        (ev,) = events(["api_action"])
        assert ev["details"]["user"] == "steve"
        assert ev["details"]["method"] == "POST"
        assert ev["details"]["route"] == "/api/things/{thing_id}"
        assert ev["details"]["path"] == "/api/things/7"

    def test_reads_and_test_commands_are_not_recorded(self, client):
        client.get("/api/things")
        client.post("/api/test/channel")
        assert events(["api_action"]) == []
