"""Tests for py/event_log.py: recording never raises or blocks the show."""

import configparser
import logging
import os
import sqlite3
import subprocess
import sys
import time

import pytest

import event_log

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.unit
class TestRecord:

    def test_record_and_fetch(self, events_db):
        assert event_log.record("show_start", source="cron", user="pi")
        assert event_log.record("song_start", source="show", song="Jingle", variant="v2",
                                duration_s=120.0)
        events, total = event_log.fetch_events()
        assert total == 2
        newest = events[0]
        assert newest["type"] == "song_start"
        assert newest["song"] == "Jingle"
        assert newest["variant"] == "v2"
        assert newest["details"] == {"duration_s": 120.0}
        assert events[1]["details"] == {"user": "pi"}

    def test_database_is_wal(self, events_db):
        event_log.record("show_start")
        conn = sqlite3.connect(events_db)
        try:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        finally:
            conn.close()

    def test_creates_missing_data_dir(self, tmp_path):
        path = str(tmp_path / "new" / "dir" / "events.db")
        event_log.configure(enabled=True, db_path=path)
        assert event_log.record("show_start")
        assert os.path.exists(path)

    def test_recreated_after_delete(self, events_db):
        assert event_log.record("show_start")
        os.remove(events_db)
        assert event_log.record("show_stop")
        assert event_log.fetch_events()[1] == 1

    def test_non_json_details_are_stringified(self, events_db):
        assert event_log.record("api_action", obj=object(), items=(1, 2))
        details = event_log.fetch_events()[0][0]["details"]
        assert details["items"] == [1, 2]
        assert isinstance(details["obj"], str)

    def test_disabled_is_a_no_op(self, tmp_path):
        path = str(tmp_path / "events.db")
        event_log.configure(enabled=False, db_path=path)
        assert event_log.record("show_start") is False
        assert not os.path.exists(path)
        assert event_log.is_enabled() is False

    def test_unwritable_path_does_not_raise(self, tmp_path):
        blocker = tmp_path / "file"
        blocker.write_text("not a directory")
        event_log.configure(enabled=True, db_path=str(blocker / "events.db"))
        assert event_log.record("show_start") is False

    def test_locked_database_gives_up_quickly(self, events_db):
        event_log.record("show_start")
        holder = sqlite3.connect(events_db, isolation_level=None)
        holder.execute("BEGIN EXCLUSIVE")
        try:
            t0 = time.monotonic()
            assert event_log.record("song_start") is False
            assert time.monotonic() - t0 < 1.5
        finally:
            holder.execute("ROLLBACK")
            holder.close()
        assert event_log.record("song_end")

    def test_corrupt_database_does_not_raise(self, events_db):
        with open(events_db, "wb") as f:
            f.write(b"this is not sqlite" * 100)
        event_log._ready_paths.clear()
        assert event_log.record("show_start") is False

    def test_failure_warnings_are_rate_limited(self, tmp_path, caplog, monkeypatch):
        blocker = tmp_path / "file"
        blocker.write_text("x")
        event_log.configure(enabled=True, db_path=str(blocker / "events.db"))
        monkeypatch.setitem(event_log._last_warning, "t", 0.0)
        with caplog.at_level(logging.WARNING, logger="event_log"):
            for _ in range(5):
                event_log.record("show_start")
        assert len([r for r in caplog.records if "event log" in r.getMessage()]) == 1


@pytest.mark.unit
class TestConfigure:

    def _config(self, text):
        parser = configparser.RawConfigParser(allow_no_value=True)
        parser.read_string(text)
        return parser

    def test_disabled_in_config(self, tmp_path):
        event_log.configure(self._config("[events]\nenabled = false\n"))
        assert event_log.is_enabled() is False

    def test_db_path_expands_home(self, tmp_path):
        event_log.configure(self._config(
            "[events]\nenabled = true\ndb_path = $SYNCHRONIZED_LIGHTS_HOME/x/e.db\n"),
            home=str(tmp_path))
        assert event_log.get_db_path() == str(tmp_path / "x" / "e.db")
        assert event_log.is_enabled() is True

    def test_defaults_without_section(self, tmp_path):
        event_log.configure(self._config("[api]\nport = 1\n"), home=str(tmp_path))
        assert event_log.is_enabled() is True
        assert event_log.get_db_path() == str(tmp_path / "data" / "events.db")

    def test_repo_defaults_cfg_documents_events(self):
        parser = configparser.RawConfigParser(allow_no_value=True, strict=False)
        parser.read(os.path.join(REPO_ROOT, "config", "defaults.cfg"))
        assert parser.getboolean("events", "enabled") is True
        assert parser.get("events", "db_path") == ""


@pytest.mark.unit
class TestFetch:

    def test_missing_database_is_empty(self, tmp_path):
        assert event_log.fetch_events(db_path=str(tmp_path / "none.db")) == ([], 0)

    def test_filters_and_paging(self, events_db):
        for i in range(10):
            event_log.record("song_start" if i % 2 else "show_start", ts=1000 + i)
        events, total = event_log.fetch_events(types=["song_start"])
        assert total == 5
        assert [e["ts"] for e in events] == [1009, 1007, 1005, 1003, 1001]

        events, total = event_log.fetch_events(since=1002, until=1006, newest_first=False)
        assert total == 4
        assert [e["ts"] for e in events] == [1002, 1003, 1004, 1005]

        events, total = event_log.fetch_events(limit=3, offset=3)
        assert total == 10
        assert [e["ts"] for e in events] == [1006, 1005, 1004]


@pytest.mark.unit
class TestSongTracker:

    def test_start_and_end_once(self, events_db):
        now = [100.0]
        tracker = event_log.SongTracker(clock=lambda: now[0])
        tracker.start("Carol", path="/m/carol.mp3", duration="180.5", mode="playlist",
                      variant="")
        assert tracker.active
        now[0] = 160.04
        assert tracker.end("skipped") is True
        assert tracker.end("stopped") is False  # only once
        events, _ = event_log.fetch_events(newest_first=False)
        assert [e["type"] for e in events] == ["song_start", "song_end"]
        start, end = events
        assert start["details"]["duration_s"] == 180.5
        assert start["details"]["mode"] == "playlist"
        assert start["variant"] is None
        assert end["details"]["reason"] == "skipped"
        assert end["details"]["elapsed_s"] == 60.0
        assert end["details"]["duration_s"] == 180.5

    def test_end_without_start(self, events_db):
        assert event_log.SongTracker().end("stopped") is False
        assert event_log.fetch_events()[1] == 0

    def test_blank_duration_and_variant(self, events_db):
        tracker = event_log.SongTracker()
        tracker.start("X", duration="", variant="v1")
        tracker.end("completed")
        end = event_log.fetch_events()[0][0]
        assert end["variant"] == "v1"
        assert end["details"]["duration_s"] is None

    def test_disabled_tracker_is_silent(self, tmp_path):
        event_log.configure(enabled=False, db_path=str(tmp_path / "e.db"))
        tracker = event_log.SongTracker()
        tracker.start("X")
        assert tracker.end("completed") is False
        assert not os.path.exists(tmp_path / "e.db")


@pytest.mark.unit
class TestCli:

    def test_main_records(self, events_db):
        assert event_log.main(["show_start", "--source", "cron", "--detail", "reason=x"]) == 0
        event = event_log.fetch_events()[0][0]
        assert event["type"] == "show_start"
        assert event["source"] == "cron"
        assert event["details"]["reason"] == "x"

    def test_bad_arguments_still_exit_zero(self, events_db):
        assert event_log.main(["--no-such-flag"]) == 0

    @pytest.mark.integration
    def test_script_exits_zero_when_db_unwritable(self, tmp_path):
        blocker = tmp_path / "file"
        blocker.write_text("x")
        cfg_dir = tmp_path / "home" / "config"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "defaults.cfg").write_text(
            "[events]\nenabled = true\ndb_path = %s\n" % (blocker / "events.db"))
        env = dict(os.environ, SYNCHRONIZED_LIGHTS_HOME=str(tmp_path / "home"))
        result = subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, "py", "event_log.py"), "show_stop",
             "--source", "cron"], env=env, capture_output=True, timeout=30)
        assert result.returncode == 0

    @pytest.mark.integration
    def test_script_writes_to_configured_db(self, tmp_path):
        cfg_dir = tmp_path / "home" / "config"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "defaults.cfg").write_text("[events]\nenabled = true\ndb_path =\n")
        env = dict(os.environ, SYNCHRONIZED_LIGHTS_HOME=str(tmp_path / "home"))
        result = subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, "py", "event_log.py"), "show_start",
             "--source", "cron"], env=env, capture_output=True, timeout=30)
        assert result.returncode == 0
        db = tmp_path / "home" / "data" / "events.db"
        events, total = event_log.fetch_events(db_path=str(db))
        assert total == 1 and events[0]["source"] == "cron"
