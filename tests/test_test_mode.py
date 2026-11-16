"""Tests for hardware test mode.

Covers the `hardware_controller.serve()` JSON-lines loop, TestModeManager
(driving a real serve process backed by mock hardware), the /api/test router,
and the exclusivity guards between test mode, the lightshow and the scheduler.
"""

import io
import json
import os
import sys
import time
from unittest.mock import MagicMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.core.auth import require_admin
from api.core.database import Database
from api.models.schemas import LightshowState
from api.routers import lightshow, test_mode
from api.routers.schedules import get_optional_scheduler
from api.services.lightshow_manager import LightshowManager
from api.services.scheduler import SchedulerService
from api.services.test_mode_manager import (
    TestModeCommandError,
    TestModeConflict,
    TestModeError,
    TestModeManager,
    TestModeNotActive,
    set_test_mode_manager,
)

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PY_DIR = os.path.join(REPO_ROOT, "py")

# Channel 0 and 2 are PWM, 1 and 3 are on/off
PWM = [True, False, True, False]


def _mock_hardware(is_pwm=PWM):
    hw = MagicMock()
    hw.cm.hardware.gpio_len = len(is_pwm)
    hw.cm.hardware.is_pin_pwm = list(is_pwm)
    return hw


# --------------------------------------------------------------------------
# hardware_controller.serve()
# --------------------------------------------------------------------------

def _run_serve(commands):
    import hardware_controller

    hw = _mock_hardware()
    patterns = MagicMock()
    stdin = io.StringIO("".join(json.dumps(c) + "\n" if isinstance(c, dict) else c
                                for c in commands))
    stdout = io.StringIO()
    hardware_controller.serve(hw, stdin=stdin, stdout=stdout, patterns=patterns)
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    return hw, patterns, replies


@pytest.mark.unit
class TestServe:

    def test_ready_line_then_eof_cleans_up(self):
        hw, _, replies = _run_serve([])
        assert replies[0]["ok"] and replies[0]["ready"]
        assert replies[0]["channel_count"] == 4
        assert replies[0]["channels"] == [0.0] * 4
        hw.initialize.assert_called_once()
        hw.turn_off_lights.assert_called()
        hw.clean_up.assert_called_once()

    def test_set_pwm_channel_keeps_brightness(self):
        hw, _, replies = _run_serve([{"cmd": "set", "channel": 0, "on": True, "brightness": 0.5}])
        assert replies[1]["ok"]
        assert replies[1]["channels"][0] == 0.5
        hw.set_light.assert_called_with(0, False, 0.5)

    def test_set_onoff_channel_goes_full_on(self):
        _, _, replies = _run_serve([{"cmd": "set", "channel": 1, "on": True, "brightness": 0.3}])
        assert replies[1]["channels"][1] == 1.0

    def test_set_off(self):
        _, _, replies = _run_serve([
            {"cmd": "set", "channel": 2, "on": True},
            {"cmd": "set", "channel": 2, "on": False},
        ])
        assert replies[1]["channels"][2] == 1.0
        assert replies[2]["channels"][2] == 0.0

    def test_brightness_is_clamped(self):
        _, _, replies = _run_serve([{"cmd": "set", "channel": 0, "on": True, "brightness": 7}])
        assert replies[1]["channels"][0] == 1.0

    def test_channel_out_of_range(self):
        _, _, replies = _run_serve([{"cmd": "set", "channel": 9, "on": True}])
        assert not replies[1]["ok"]
        assert "out of range" in replies[1]["error"]

    def test_all_on_and_off(self):
        hw, _, replies = _run_serve([{"cmd": "all", "on": True}, {"cmd": "all", "on": False}])
        assert replies[1]["channels"] == [1.0] * 4
        assert replies[2]["channels"] == [0.0] * 4
        hw.turn_on_lights.assert_called_once()

    def test_pattern_runs_once(self):
        _, patterns, replies = _run_serve([{"cmd": "pattern", "name": "flash"}])
        assert replies[1]["ok"] and replies[1]["pattern"] == "flash"
        patterns.run.assert_called_once_with("flash", repeat=False)

    def test_unknown_pattern(self):
        _, patterns, replies = _run_serve([{"cmd": "pattern", "name": "disco"}])
        assert not replies[1]["ok"]
        patterns.run.assert_not_called()

    def test_invalid_json_and_unknown_command(self):
        _, _, replies = _run_serve(["not json\n", {"cmd": "explode"}, "[1, 2]\n"])
        assert [r["ok"] for r in replies[1:]] == [False, False, False]
        assert "invalid JSON" in replies[1]["error"]
        assert "unknown command" in replies[2]["error"]

    def test_quit_stops_reading(self):
        _, _, replies = _run_serve([{"cmd": "quit"}, {"cmd": "status"}])
        assert len(replies) == 2  # ready + quit, status never processed

    def test_cleanup_runs_even_if_hardware_raises(self):
        import hardware_controller

        hw = _mock_hardware()
        hw.set_light.side_effect = RuntimeError("gpio exploded")
        stdin = io.StringIO(json.dumps({"cmd": "set", "channel": 0, "on": True}) + "\n")
        with pytest.raises(RuntimeError):
            hardware_controller.serve(hw, stdin=stdin, stdout=io.StringIO(), patterns=MagicMock())
        hw.clean_up.assert_called_once()


# --------------------------------------------------------------------------
# TestModeManager against a real serve process with mock hardware
# --------------------------------------------------------------------------

FAKE_SERVE = """
import sys
sys.path.insert(0, {py_dir!r})
from unittest.mock import MagicMock
import hardware_controller

hw = MagicMock()
hw.cm.hardware.gpio_len = 4
hw.cm.hardware.is_pin_pwm = {pwm!r}
hardware_controller.serve(hw, patterns=MagicMock())
"""


@pytest.fixture
def serve_command(tmp_path):
    script = tmp_path / "fake_serve.py"
    script.write_text(FAKE_SERVE.format(py_dir=PY_DIR, pwm=PWM))
    return [sys.executable, str(script)]


@pytest.fixture
def show():
    mgr = Mock(spec=LightshowManager)
    mgr.is_running.return_value = False
    return mgr


@pytest.fixture
def tm(serve_command, show):
    manager = TestModeManager(timeout_minutes=10, lightshow_manager=show,
                              command=serve_command, start_timeout=30)
    yield manager
    manager.shutdown()


@pytest.mark.integration
class TestManager:

    def test_inactive_by_default(self, tm):
        status = tm.status()
        assert status["active"] is False
        assert status["channels"] == []
        assert tm.seconds_remaining() is None

    def test_enter_and_exit(self, tm):
        status = tm.enter(by="tester")
        assert status["active"] is True
        assert status["channel_count"] == 4
        assert status["channels"] == [0.0] * 4
        assert status["entered_by"] == "tester"
        assert 0 < status["seconds_remaining"] <= 600

        status = tm.exit(reason="done")
        assert status["active"] is False
        assert status["exit_reason"] == "done"

    def test_enter_is_idempotent(self, tm):
        pid = tm.enter()["pid"]
        assert tm.enter()["pid"] == pid

    def test_commands_update_channels(self, tm):
        tm.enter()
        tm.set_channel(0, True, 0.25)
        tm.set_channel(1, True, 0.25)
        assert tm.status()["channels"] == [0.25, 1.0, 0.0, 0.0]
        tm.set_all(True)
        assert tm.status()["channels"] == [1.0] * 4
        tm.run_pattern("cylon")
        assert tm.status()["channels"] == [0.0] * 4

    def test_rejected_command_keeps_test_mode_active(self, tm):
        tm.enter()
        with pytest.raises(TestModeCommandError):
            tm.set_channel(99, True)
        assert tm.is_active()

    def test_commands_require_active(self, tm):
        with pytest.raises(TestModeNotActive):
            tm.set_all(True)

    def test_refuses_while_show_running(self, tm, show):
        show.is_running.return_value = True
        with pytest.raises(TestModeConflict):
            tm.enter()
        assert not tm.is_active()

    def test_process_that_never_gets_ready(self, show):
        manager = TestModeManager(lightshow_manager=show, start_timeout=5,
                                  command=[sys.executable, "-c", "import sys; sys.exit(3)"])
        with pytest.raises(TestModeError):
            manager.enter()
        assert not manager.is_active()

    def test_process_death_is_noticed(self, tm):
        tm.enter()
        tm.process.kill()
        tm.process.wait(timeout=5)
        status = tm.status()
        assert status["active"] is False
        assert status["exit_reason"]

    def test_watchdog_exits_after_idle_timeout(self, serve_command, show):
        now = [1000.0]
        manager = TestModeManager(timeout_minutes=1, lightshow_manager=show,
                                  command=serve_command, start_timeout=30,
                                  watchdog_interval=0.05, clock=lambda: now[0])
        try:
            manager.enter()
            assert manager.seconds_remaining() == 60
            now[0] += 61  # jump past the idle timeout
            deadline = time.time() + 10
            while manager.is_active() and time.time() < deadline:
                time.sleep(0.05)
            assert not manager.is_active()
            assert manager.status()["exit_reason"] == "timeout"
        finally:
            manager.shutdown()

    def test_default_command_uses_absolute_path(self, monkeypatch):
        monkeypatch.delenv("SYNCHRONIZED_LIGHTS_HOME", raising=False)
        manager = TestModeManager()
        assert os.path.isabs(manager.command[1])
        assert manager.command[1].endswith(os.path.join("py", "hardware_controller.py"))


# --------------------------------------------------------------------------
# /api/test router
# --------------------------------------------------------------------------

def _status(active=True, channels=None):
    return {
        "active": active,
        "channel_count": 4,
        "channels": channels if channels is not None else ([0.0] * 4 if active else []),
        "timeout_seconds": 600.0,
        "seconds_remaining": 600.0 if active else None,
        "entered_by": "tester" if active else None,
        "exit_reason": None,
        "pid": 99 if active else None,
    }


@pytest.fixture
def mock_tm():
    manager = Mock(spec=TestModeManager)
    manager.channel_count = 4
    manager.is_active.return_value = True
    manager.status.return_value = _status()
    manager.enter.return_value = _status()
    manager.exit.return_value = _status(active=False)
    return manager


@pytest.fixture
def api(mock_tm):
    app = FastAPI()
    app.include_router(test_mode.router)
    app.dependency_overrides[require_admin] = lambda: {"username": "tester", "id": 1}
    set_test_mode_manager(mock_tm)
    yield TestClient(app)
    set_test_mode_manager(None)


@pytest.mark.unit
class TestRouter:

    def test_503_when_not_initialized(self, api):
        set_test_mode_manager(None)
        assert api.get("/api/test/status").status_code == 503

    def test_requires_auth(self, mock_tm):
        app = FastAPI()
        app.include_router(test_mode.router)
        set_test_mode_manager(mock_tm)
        try:
            assert TestClient(app).get("/api/test/status").status_code in (401, 403)
        finally:
            set_test_mode_manager(None)

    def test_status(self, api):
        res = api.get("/api/test/status")
        assert res.status_code == 200
        assert res.json()["active"] is True

    def test_enter_passes_username(self, api, mock_tm):
        assert api.post("/api/test/enter").status_code == 200
        mock_tm.enter.assert_called_once_with(by="tester")

    def test_enter_conflict_is_409(self, api, mock_tm):
        mock_tm.enter.side_effect = TestModeConflict("Lightshow is running")
        res = api.post("/api/test/enter")
        assert res.status_code == 409
        assert "running" in res.json()["detail"]

    def test_enter_failure_is_500(self, api, mock_tm):
        mock_tm.enter.side_effect = TestModeError("boom")
        assert api.post("/api/test/enter").status_code == 500

    def test_exit(self, api, mock_tm):
        res = api.post("/api/test/exit")
        assert res.status_code == 200
        assert res.json()["active"] is False
        mock_tm.exit.assert_called_once()

    def test_channel(self, api, mock_tm):
        res = api.post("/api/test/channel", json={"channel": 2, "state": True, "brightness": 0.5})
        assert res.status_code == 200
        mock_tm.set_channel.assert_called_once_with(2, True, 0.5)

    def test_channel_out_of_range(self, api, mock_tm):
        res = api.post("/api/test/channel", json={"channel": 4, "state": True})
        assert res.status_code == 400
        mock_tm.set_channel.assert_not_called()

    def test_channel_validation(self, api):
        assert api.post("/api/test/channel", json={"channel": -1, "state": True}).status_code == 422
        assert api.post("/api/test/channel",
                        json={"channel": 0, "state": True, "brightness": 2}).status_code == 422

    def test_channel_client_not_supported(self, api):
        res = api.post("/api/test/channel", json={"channel": 0, "state": True, "client_id": "x"})
        assert res.status_code == 400

    def test_channel_when_inactive(self, api, mock_tm):
        mock_tm.is_active.return_value = False
        assert api.post("/api/test/channel", json={"channel": 0, "state": True}).status_code == 409

    def test_command_rejected_is_400(self, api, mock_tm):
        mock_tm.set_all.side_effect = TestModeCommandError("nope")
        assert api.post("/api/test/all", json={"on": True}).status_code == 400

    def test_not_active_is_409(self, api, mock_tm):
        mock_tm.set_all.side_effect = TestModeNotActive("Test mode is not active")
        assert api.post("/api/test/all", json={"on": True}).status_code == 409

    def test_pattern(self, api, mock_tm):
        assert api.post("/api/test/pattern", json={"name": "cylon"}).status_code == 200
        mock_tm.run_pattern.assert_called_once_with("cylon")

    def test_unknown_pattern_is_422(self, api, mock_tm):
        assert api.post("/api/test/pattern", json={"name": "disco"}).status_code == 422
        mock_tm.run_pattern.assert_not_called()


# --------------------------------------------------------------------------
# Exclusivity guards
# --------------------------------------------------------------------------

class FakeConfig:
    """Minimal APIConfig stand-in for LightshowManager."""

    def reload_state(self):
        pass

    def get_state(self, key, default=""):
        return default


def _active_tm(active=True):
    manager = Mock(spec=TestModeManager)
    manager.is_active.return_value = active
    return manager


@pytest.mark.unit
class TestExclusivity:

    def test_lightshow_manager_refuses_start_in_test_mode(self):
        mgr = LightshowManager(FakeConfig())
        mgr.set_test_mode_manager(_active_tm())
        assert mgr.start() is False
        assert "Test mode" in mgr.error_message

    def test_status_reports_test_mode(self):
        mgr = LightshowManager(FakeConfig())
        mgr.set_test_mode_manager(_active_tm())
        assert mgr.get_status()["state"] == LightshowState.TEST_MODE.value

    def test_status_idle_when_test_mode_inactive(self):
        mgr = LightshowManager(FakeConfig())
        mgr.set_test_mode_manager(_active_tm(active=False))
        assert mgr.get_status()["state"] != LightshowState.TEST_MODE.value

    def test_start_endpoint_409_in_test_mode(self, tmp_path):
        show = Mock(spec=LightshowManager)
        app = FastAPI()
        app.include_router(lightshow.router)
        app.dependency_overrides[lightshow.get_manager] = lambda: show
        app.dependency_overrides[get_optional_scheduler] = lambda: None
        app.dependency_overrides[lightshow.get_optional_test_mode] = lambda: _active_tm()
        app.dependency_overrides[require_admin] = lambda: {"username": "tester", "id": 1}
        res = TestClient(app).post("/api/lightshow/start", json={})
        assert res.status_code == 409
        show.start.assert_not_called()

    def test_scheduled_start_skipped_in_test_mode(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        db.initialize_schema()
        db.migrate_schema()
        show = Mock(spec=LightshowManager)
        scheduler = SchedulerService(db, show, test_mode_manager=_active_tm())
        try:
            scheduler._start_lightshow("playlist")
            show.start.assert_not_called()
        finally:
            scheduler.stop()

    def test_scheduled_start_runs_without_test_mode(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        db.initialize_schema()
        db.migrate_schema()
        show = Mock(spec=LightshowManager)
        show.start.return_value = True
        scheduler = SchedulerService(db, show, test_mode_manager=_active_tm(active=False))
        try:
            scheduler._start_lightshow("playlist")
            show.start.assert_called_once()
        finally:
            scheduler.stop()
