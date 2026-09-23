"""Tests for schedule suspend/resume in SchedulerService.

Uses a real temporary SQLite database and a mocked LightshowManager.
"""

from unittest.mock import Mock

import pytest

from api.core.database import Database
from api.services.lightshow_manager import LightshowManager
from api.services.scheduler import SchedulerService


@pytest.fixture
def database(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize_schema()
    db.migrate_schema()
    return db


@pytest.fixture
def manager():
    mgr = Mock(spec=LightshowManager)
    mgr.start.return_value = True
    mgr.stop.return_value = True
    return mgr


@pytest.fixture
def scheduler(database, manager):
    service = SchedulerService(database, manager)
    yield service
    service.stop()


@pytest.mark.unit
class TestSuspendResume:

    def test_not_suspended_by_default(self, scheduler):
        assert scheduler.is_suspended() is False

    def test_suspend_and_resume(self, scheduler):
        scheduler.suspend(by="admin")
        assert scheduler.is_suspended() is True

        scheduler.resume(by="admin")
        assert scheduler.is_suspended() is False

    def test_suspend_is_idempotent_upsert(self, scheduler, database):
        scheduler.suspend()
        scheduler.suspend()
        with database.get_connection() as conn:
            rows = conn.execute(
                "SELECT value FROM settings WHERE key = 'schedule_suspended'"
            ).fetchall()
        assert [r[0] for r in rows] == ["1"]

    def test_suspend_persists_across_instances(self, scheduler, database, manager):
        scheduler.suspend()
        other = SchedulerService(database, manager)
        assert other.is_suspended() is True

    def test_suspend_does_not_touch_schedule_enabled_flags(self, scheduler):
        schedule_id = scheduler.create_schedule("18:00", "22:00", [0, 1, 2, 3, 4, 5, 6])
        scheduler.suspend()
        assert scheduler.get_schedule(schedule_id)["enabled"] == 1


@pytest.mark.unit
class TestScheduledJobsWhileSuspended:

    def test_start_job_skipped_when_suspended(self, scheduler, manager):
        scheduler.suspend()
        scheduler._start_lightshow("playlist")
        manager.start.assert_not_called()

    def test_stop_job_skipped_when_suspended(self, scheduler, manager):
        scheduler.suspend()
        scheduler._stop_lightshow()
        manager.stop.assert_not_called()

    def test_jobs_run_when_not_suspended(self, scheduler, manager):
        scheduler._start_lightshow("ambient")
        manager.start.assert_called_once_with(mode="ambient")

        scheduler._stop_lightshow()
        manager.stop.assert_called_once_with(graceful=True)

    def test_jobs_run_again_after_resume(self, scheduler, manager):
        scheduler.suspend()
        scheduler._start_lightshow("playlist")
        scheduler.resume()
        scheduler._start_lightshow("playlist")
        manager.start.assert_called_once_with(mode="playlist")


@pytest.mark.unit
class TestNextEvent:

    def test_next_event_none_without_schedules(self, scheduler):
        scheduler.start()
        assert scheduler.get_next_event() is None

    def test_next_event_returns_first_upcoming(self, scheduler):
        scheduler.create_schedule("18:00", "22:00", [0, 1, 2, 3, 4, 5, 6])
        scheduler.start()

        event = scheduler.get_next_event()
        assert event is not None
        assert event["action"] in ("start", "stop")
        assert event["time"] == scheduler.get_next_scheduled_events(limit=1)[0]["time"]

    def test_next_event_none_while_suspended(self, scheduler):
        scheduler.create_schedule("18:00", "22:00", [0, 1, 2, 3, 4, 5, 6])
        scheduler.start()
        scheduler.suspend()
        assert scheduler.get_next_event() is None
