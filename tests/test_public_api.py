"""Tests for the public, read-only API (/api/public/*)."""

import json
from datetime import datetime
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import main
from api.routers import public
from api.routers.schedules import get_optional_scheduler

NOW = 1_700_000_000.0


@pytest.fixture(autouse=True)
def fresh_state():
    public._cache.clear()
    public.public_limiter.reset()
    yield
    public._cache.clear()
    public.public_limiter.reset()


def make_client(state=None, schedules=None, note=None):
    app = FastAPI()
    app.include_router(public.router)
    app.dependency_overrides[public.get_state_reader] = (
        lambda: (lambda: dict(state)) if state is not None else None)
    scheduler = None
    if schedules is not None:
        scheduler = Mock()
        scheduler.get_schedules.return_value = schedules
    app.dependency_overrides[get_optional_scheduler] = lambda: scheduler
    app.dependency_overrides[public.get_schedule_note] = lambda: note
    return TestClient(app)


PLAYING = {
    "now_playing": "1",
    "song_title": "Carol_of_the_Bells.mp3",
    "song_path": "/home/scroce/lightshowpi/music/secret/Carol_of_the_Bells.mp3",
    "song_duration": "200",
    "song_started_at": str(NOW - 50),
}


# ---------------------------------------------------------------------------
# now-playing
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestBuildNowPlaying:

    def test_off(self):
        assert public.build_now_playing({"now_playing": "0"}).state == "off"
        assert public.build_now_playing(None).state == "off"

    def test_playing_song(self):
        res = public.build_now_playing(PLAYING, now=NOW)
        assert res.state == "playing"
        assert res.title == "Carol of the Bells"
        assert res.elapsed_s == 50
        assert res.duration_s == 200

    def test_stale_state_after_crash_is_off(self):
        assert public.build_now_playing(PLAYING, now=NOW + 1000).state == "off"

    def test_song_loading_has_no_elapsed(self):
        res = public.build_now_playing({**PLAYING, "song_started_at": ""}, now=NOW)
        assert res.state == "playing" and res.elapsed_s is None

    @pytest.mark.parametrize("mode", ["audio-in", "stream-in"])
    def test_live_modes_have_no_title(self, mode):
        res = public.build_now_playing({"now_playing": "1", "song_title": mode,
                                        "song_started_at": str(NOW)}, now=NOW)
        assert res.state == "playing" and res.title is None

    def test_ambient(self):
        res = public.build_now_playing({"now_playing": "1", "song_title": "Ambient"}, now=NOW)
        assert res.state == "ambient"

    @pytest.mark.parametrize("raw, expected", [
        ("/home/pi/music/Jingle.mp3", "Jingle"),
        ("Silent Night", "Silent Night"),
        ("x" * 300, "x" * 120),
    ])
    def test_public_title(self, raw, expected):
        assert public.public_title(raw) == expected


@pytest.mark.unit
class TestNowPlayingEndpoint:

    def test_exact_keys_and_no_paths(self):
        res = make_client(state=PLAYING).get("/api/public/now-playing")
        assert res.status_code == 200
        assert set(res.json()) == {"state", "title", "elapsed_s", "duration_s"}
        assert "/home" not in res.text and "scroce" not in res.text
        assert "max-age" in res.headers["cache-control"]

    def test_no_config_is_off(self):
        assert make_client().get("/api/public/now-playing").json()["state"] == "off"

    def test_unreadable_state_is_off(self):
        app_client = make_client()
        app_client.app.dependency_overrides[public.get_state_reader] = (
            lambda: (lambda: (_ for _ in ()).throw(OSError("gone"))))
        assert app_client.get("/api/public/now-playing").json()["state"] == "off"

    def test_cached_between_requests(self):
        calls = []

        def reader():
            calls.append(1)
            return {"now_playing": "0"}
        client = make_client()
        client.app.dependency_overrides[public.get_state_reader] = lambda: reader
        for _ in range(5):
            client.get("/api/public/now-playing")
        assert len(calls) == 1

    def test_rate_limited(self):
        client = make_client(state={"now_playing": "0"})
        for _ in range(public.PUBLIC_REQUESTS_PER_MINUTE):
            assert client.get("/api/public/now-playing").status_code == 200
        res = client.get("/api/public/schedule")
        assert res.status_code == 429
        assert "retry-after" in res.headers


# ---------------------------------------------------------------------------
# schedule
# ---------------------------------------------------------------------------

SCHEDULES = [
    {"id": 1, "start_time": "17:30", "stop_time": "22:30", "mode": "playlist",
     "enabled": True, "days_of_week": [0, 1, 2, 3, 4, 5, 6],
     "updated_by": "admin", "updated_at": "2026-11-18"},
    {"id": 2, "start_time": "16:00", "stop_time": "23:00", "mode": "playlist",
     "enabled": True, "days_of_week": [5, 6], "updated_by": "admin", "updated_at": "x"},
]


@pytest.mark.unit
class TestSchedule:

    def test_build_picks_tonight(self):
        friday = datetime(2026, 11, 20)  # a Friday
        res = public.build_schedule(SCHEDULES, "Nightly", today=friday)
        assert res.note == "Nightly"
        assert res.tonight.start == "17:30"
        assert res.shows[1].days == ["Fri", "Sat"]

    def test_disabled_and_malformed_are_skipped(self):
        res = public.build_schedule([{**SCHEDULES[0], "enabled": False},
                                     {"id": 3, "days_of_week": [1]}], None)
        assert res.shows == [] and res.tonight is None

    def test_endpoint_hides_internal_fields(self):
        res = make_client(schedules=SCHEDULES, note="Nightly 5:30-10:30 PM").get(
            "/api/public/schedule")
        assert res.status_code == 200
        body = res.json()
        assert set(body) == {"note", "tonight", "shows"}
        for show in body["shows"]:
            assert set(show) == {"days", "start", "stop"}
        assert "admin" not in json.dumps(body)

    def test_cron_only_show_uses_note(self):
        body = make_client(schedules=[], note="Nightly 5:30-10:30 PM").get(
            "/api/public/schedule").json()
        assert body == {"note": "Nightly 5:30-10:30 PM", "tonight": None, "shows": []}

    def test_no_scheduler(self):
        body = make_client().get("/api/public/schedule").json()
        assert body["shows"] == []


@pytest.mark.unit
def test_public_routes_are_mounted_without_auth():
    main.app.openapi_schema = None
    paths = main.app.openapi()["paths"]
    for path in ("/api/public/now-playing", "/api/public/schedule"):
        assert not paths[path]["get"].get("security")


@pytest.mark.unit
def test_public_routes_work_on_the_real_app():
    public.set_config(None)
    res = TestClient(main.app).get("/api/public/now-playing")
    assert res.status_code == 200 and res.json()["state"] == "off"
