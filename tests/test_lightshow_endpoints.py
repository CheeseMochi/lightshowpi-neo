"""Tests for the lightshow router (start/stop schedule handling, /status) and
LightshowManager status reporting.
"""

import time
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.core.auth import get_current_user, require_admin
from api.core.database import Database
from api.models.schemas import LightshowState
from api.routers import lightshow
from api.routers.schedules import get_optional_scheduler
from api.services.lightshow_manager import LightshowManager, compute_elapsed
from api.services.scheduler import SchedulerService


def _status(state="playing", **overrides):
    info = {
        "state": state,
        "is_running": state == "playing",
        "current_song": "Jingle Bells by Various",
        "song_path": "/music/jingle.mp3",
        "current_playlist": "/music/.playlist",
        "mode": "playlist",
        "elapsed_time": 12.5,
        "total_time": 180.0,
        "error_message": None,
        "pid": 1234,
    }
    info.update(overrides)
    return info


@pytest.fixture
def manager():
    mgr = Mock(spec=LightshowManager)
    mgr.start.return_value = True
    mgr.stop.return_value = True
    mgr.error_message = None
    mgr.get_status.return_value = _status()
    return mgr


@pytest.fixture
def scheduler():
    sched = Mock(spec=SchedulerService)
    sched.is_suspended.return_value = False
    sched.get_next_event.return_value = {
        "schedule_id": 1,
        "action": "stop",
        "time": "2025-12-20T22:15:00-06:00",
        "job_id": "schedule_1_stop",
    }
    return sched


@pytest.fixture
def database(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize_schema()
    return db


@pytest.fixture
def app(manager, scheduler, database):
    app = FastAPI()
    app.include_router(lightshow.router)
    app.dependency_overrides[lightshow.get_manager] = lambda: manager
    app.dependency_overrides[get_optional_scheduler] = lambda: scheduler
    app.dependency_overrides[lightshow.get_optional_database] = lambda: database
    app.dependency_overrides[require_admin] = lambda: {"username": "tester", "id": 1}
    app.dependency_overrides[get_current_user] = lambda: {"username": "tester", "id": 1}
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.mark.unit
class TestStopStartSchedule:

    def test_stop_mode_stop_suspends_schedule(self, client, manager, scheduler):
        response = client.post("/api/lightshow/stop", json={"mode": "stop"})
        assert response.status_code == 200
        manager.stop.assert_called_once_with(graceful=True, source="api", user="tester")
        scheduler.suspend.assert_called_once_with(by="tester")

    def test_stop_mode_pause_keeps_schedule(self, client, scheduler):
        response = client.post("/api/lightshow/stop", json={"mode": "pause"})
        assert response.status_code == 200
        scheduler.suspend.assert_not_called()

    def test_start_with_resume_schedule_resumes(self, client, scheduler):
        response = client.post("/api/lightshow/start", json={"resume_schedule": True})
        assert response.status_code == 200
        scheduler.resume.assert_called_once_with(by="tester")

    def test_start_without_resume_schedule(self, client, scheduler):
        response = client.post("/api/lightshow/start", json={"resume_schedule": False})
        assert response.status_code == 200
        scheduler.resume.assert_not_called()

    def test_failed_start_does_not_resume(self, client, manager, scheduler):
        manager.start.return_value = False
        manager.error_message = "boom"
        response = client.post("/api/lightshow/start", json={"resume_schedule": True})
        assert response.status_code == 500
        scheduler.resume.assert_not_called()

    def test_stop_without_scheduler(self, app, client, manager):
        app.dependency_overrides[get_optional_scheduler] = lambda: None
        response = client.post("/api/lightshow/stop", json={"mode": "stop"})
        assert response.status_code == 200


@pytest.mark.unit
class TestStatusEndpoint:

    def test_status_includes_song_and_schedule_fields(self, client):
        response = client.get("/api/lightshow/status")
        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "playing"
        assert data["mode"] == "playlist"
        assert data["current_song"] == "Jingle Bells by Various"
        assert data["elapsed_time"] == 12.5
        assert data["total_time"] == 180.0
        assert data["schedule_enabled"] is True
        assert data["next_scheduled_event"] == "2025-12-20T22:15:00-06:00"
        assert data["next_scheduled_action"] == "stop"
        assert data["current_song_id"] is None
        assert data["clients"] == []

    def test_status_schedule_suspended(self, client, scheduler):
        scheduler.is_suspended.return_value = True
        scheduler.get_next_event.return_value = None
        data = client.get("/api/lightshow/status").json()
        assert data["schedule_enabled"] is False
        assert data["next_scheduled_event"] is None

    @pytest.mark.parametrize("state", [s.value for s in LightshowState])
    def test_all_manager_states_serialize(self, client, manager, state):
        manager.get_status.return_value = _status(state=state)
        response = client.get("/api/lightshow/status")
        assert response.status_code == 200
        assert response.json()["state"] == state

    def test_status_song_id_lookup(self, client, database):
        with database.get_connection() as conn:
            conn.execute("INSERT INTO songs (file_path, title) VALUES (?, ?)",
                         ("/music/jingle.mp3", "Jingle Bells"))
        data = client.get("/api/lightshow/status").json()
        assert isinstance(data["current_song_id"], int)

    def test_status_lists_clients(self, client, database):
        with database.get_connection() as conn:
            conn.execute(
                "INSERT INTO clients (id, name, auth_key, ip_address, status) VALUES (?, ?, ?, ?, ?)",
                ("porch", "Porch", "key1", "10.0.0.5", "online"))
            conn.execute(
                "INSERT INTO clients (id, name, auth_key, status) VALUES (?, ?, ?, ?)",
                ("garage", "Garage", "key2", "weird"))
        clients = client.get("/api/lightshow/status").json()["clients"]
        by_id = {c["id"]: c for c in clients}
        assert by_id["porch"]["status"] == "online"
        assert by_id["porch"]["ip_address"] == "10.0.0.5"
        assert by_id["garage"]["status"] == "error"

    def test_status_without_scheduler_or_database(self, app, client):
        app.dependency_overrides[get_optional_scheduler] = lambda: None
        app.dependency_overrides[lightshow.get_optional_database] = lambda: None
        data = client.get("/api/lightshow/status").json()
        assert data["schedule_enabled"] is False
        assert data["clients"] == []


@pytest.mark.unit
class TestElapsedTime:

    def test_none_when_not_started(self):
        assert compute_elapsed(None, 100.0) is None

    def test_elapsed_since_start(self):
        assert compute_elapsed(1000.0, 200.0, now=1042.34) == 42.3

    def test_clamped_to_duration(self):
        assert compute_elapsed(1000.0, 180.0, now=2000.0) == 180.0

    def test_not_negative(self):
        assert compute_elapsed(1000.0, 180.0, now=990.0) == 0.0

    def test_unbounded_without_duration(self):
        assert compute_elapsed(1000.0, None, now=1500.0) == 500.0


class FakeConfig:
    """Minimal APIConfig stand-in backed by a dict."""

    def __init__(self, state):
        self.state = state

    def reload_state(self):
        pass

    def get_state(self, key, default=""):
        return self.state.get(key, default)


@pytest.mark.unit
class TestManagerStatus:

    def _manager(self, state, running=True):
        mgr = LightshowManager(FakeConfig(state))
        if running:
            mgr.process = Mock()
            mgr.process.poll.return_value = None
            mgr.process.pid = 42
            mgr.state = LightshowState.PLAYING
        return mgr

    def test_reads_song_metadata_not_now_playing_flag(self):
        started = time.time() - 30
        mgr = self._manager({
            "now_playing": "1",
            "song_title": "Carol of the Bells",
            "song_path": "/music/carol.mp3",
            "song_duration": "200.5",
            "song_started_at": str(started),
        })
        status = mgr.get_status()
        assert status["current_song"] == "Carol of the Bells"
        assert status["song_path"] == "/music/carol.mp3"
        assert status["total_time"] == 200.5
        assert 29 <= status["elapsed_time"] <= 32

    def test_blank_values_are_none(self):
        mgr = self._manager({"now_playing": "1", "song_title": "Ambient",
                             "song_duration": "", "song_started_at": ""})
        status = mgr.get_status()
        assert status["current_song"] == "Ambient"
        assert status["total_time"] is None
        assert status["elapsed_time"] is None

    def test_not_running_reports_no_song(self):
        mgr = self._manager({"now_playing": "1", "song_title": "Stale"}, running=False)
        status = mgr.get_status()
        assert status["current_song"] is None
        assert status["elapsed_time"] is None

    def test_restart_preserves_mode(self, monkeypatch):
        mgr = self._manager({})
        mgr.current_mode = "ambient"
        mgr.current_playlist = None
        monkeypatch.setattr(mgr, "stop", lambda graceful=True, **kw: True)
        monkeypatch.setattr(time, "sleep", lambda s: None)
        calls = {}

        def fake_start(playlist=None, song=None, mode=None, **kw):
            calls.update(playlist=playlist, mode=mode)
            return True

        monkeypatch.setattr(mgr, "start", fake_start)
        assert mgr.restart() is True
        assert calls == {"playlist": None, "mode": "ambient"}
