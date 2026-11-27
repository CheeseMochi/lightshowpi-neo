"""Tests for the public, read-only API (/api/public/*)."""

import json
import time
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
    public._stats_memo.update(key=None, value=None)
    yield
    public._cache.clear()
    public.public_limiter.reset()


def make_client(state=None, schedules=None, note=None, show_times=None,
                entries=None, home_dir="/home/pi/lightshowpi"):
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
    app.dependency_overrides[public.get_show_times] = lambda: show_times
    app.dependency_overrides[public.get_playlist_reader] = (
        lambda: (lambda: [list(e) for e in entries]) if entries is not None else None)
    app.dependency_overrides[public.get_home_dir] = lambda: home_dir
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
        assert set(res.json()) == set(public.PublicNowPlaying.model_fields)
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
class TestShowTimesFallback:
    """[api] public_show_* gives the window when the show runs from cron."""

    def test_parse_defaults_to_every_day(self):
        show = public.parse_show_times("17:30", "22:30", "")
        assert show.days == public.DAY_NAMES
        assert (show.start, show.stop) == ("17:30", "22:30")

    def test_parse_days_and_pads_hours(self):
        show = public.parse_show_times("5:30", "22:00", "sat, Friday,sun")
        assert show.days == ["Sun", "Fri", "Sat"]
        assert show.start == "05:30"

    @pytest.mark.parametrize("start, stop, days", [
        ("", "22:30", ""), ("17:30", "", ""),           # unset: today's behaviour
        ("5pm", "22:30", ""), ("24:00", "22:30", ""),   # invalid time
        ("17:30", "22:30", "Mon,Funday"),               # invalid day
    ])
    def test_blank_or_invalid_is_none(self, start, stop, days):
        assert public.parse_show_times(start, stop, days) is None

    def test_fallback_used_when_no_db_schedules(self):
        fallback = public.parse_show_times("17:30", "22:30")
        res = public.build_schedule([], None, today=datetime(2026, 11, 20), fallback=fallback)
        assert res.tonight.start == "17:30" and len(res.shows) == 1

    def test_db_schedules_win_over_fallback(self):
        fallback = public.parse_show_times("18:00", "21:00")
        res = public.build_schedule(SCHEDULES, None, today=datetime(2026, 11, 20),
                                    fallback=fallback)
        assert [s.start for s in res.shows] == ["17:30", "16:00"]

    def test_fallback_not_tonight(self):
        fallback = public.parse_show_times("17:30", "22:30", "Sat")
        res = public.build_schedule([], None, today=datetime(2026, 11, 20), fallback=fallback)
        assert res.tonight is None and res.shows[0].days == ["Sat"]

    def test_endpoint_uses_fallback(self):
        body = make_client(schedules=[], show_times=public.parse_show_times("17:30", "22:30")
                           ).get("/api/public/schedule").json()
        assert body["shows"] == [{"days": public.DAY_NAMES, "start": "17:30", "stop": "22:30"}]
        assert body["tonight"]["start"] == "17:30"

    def test_config_getters(self):
        config = Mock(public_show_start="17:30", public_show_stop="22:30",
                      public_show_days="Fri,Sat")
        assert public.config_show_times(config).days == ["Fri", "Sat"]


# ---------------------------------------------------------------------------
# playlist
# ---------------------------------------------------------------------------

ENTRIES = [
    ["Carol Of The Bells", "$SYNCHRONIZED_LIGHTS_HOME/music/secret/Carol_of_the_Bells.mp3", set()],
    ["Deck_The_Halls.mp3", "/abs/music/deck.mp3", {"1.2.3.4"}, "playing!"],
    ["", "/abs/music/untitled.mp3", set()],
]


@pytest.mark.unit
class TestPlaylist:

    def test_titles_only(self):
        res = public.build_playlist(ENTRIES)
        assert res.songs == ["Carol Of The Bells", "Deck The Halls"]
        assert res.current is None

    def test_current_found_by_path(self):
        state = {**PLAYING, "song_path": "/home/scroce/lightshowpi/music/secret/Carol_of_the_Bells.mp3"}
        res = public.build_playlist(ENTRIES, state, home_dir="/home/scroce/lightshowpi", now=NOW)
        assert res.current == 0

    def test_no_current_when_off_or_stale(self):
        state = {**PLAYING, "song_path": "/abs/music/deck.mp3"}
        assert public.build_playlist(ENTRIES, state, now=NOW).current == 1
        assert public.build_playlist(ENTRIES, {**state, "now_playing": "0"}, now=NOW).current is None
        assert public.build_playlist(ENTRIES, state, now=NOW + 1000).current is None

    def test_capped(self):
        many = [[f"Song {i}", f"/m/{i}.mp3", set()] for i in range(public.MAX_PUBLIC_SONGS + 5)]
        assert len(public.build_playlist(many).songs) == public.MAX_PUBLIC_SONGS

    def test_endpoint_hides_paths_and_extras(self):
        state = {**PLAYING, "song_path": "/abs/music/deck.mp3",
                 "song_started_at": str(time.time() - 10)}
        res = make_client(state=state, entries=ENTRIES).get("/api/public/playlist")
        assert res.status_code == 200
        body = res.json()
        assert body == {"songs": ["Carol Of The Bells", "Deck The Halls"], "current": 1,
                        "details": [{"artist": None, "year": None}] * 2}
        for leak in ("/abs", "SYNCHRONIZED", "1.2.3.4", "playing!", ".mp3"):
            assert leak not in res.text
        assert "max-age" in res.headers["cache-control"]

    def test_no_config_is_empty(self):
        assert make_client().get("/api/public/playlist").json() == {
            "songs": [], "current": None, "details": []}

    def test_unreadable_playlist_is_empty(self):
        client = make_client()
        client.app.dependency_overrides[public.get_playlist_reader] = (
            lambda: (lambda: (_ for _ in ()).throw(OSError("locked"))))
        assert client.get("/api/public/playlist").json()["songs"] == []

    def test_missing_file_is_empty_and_never_exits(self, tmp_path):
        config = Mock()
        config.lightshow.playlist_path = str(tmp_path / "missing.playlist")
        assert public.read_playlist_entries(config) == []
        assert public.playlist_snapshot(lambda: public.read_playlist_entries(config)).songs == []

    def test_reads_real_file(self, tmp_path):
        playlist = tmp_path / ".playlist"
        playlist.write_text("Jingle Bells\t/m/jingle.mp3\nbroken line\nSilent Night\t/m/s.mp3\t\t\n")
        config = Mock()
        config.lightshow.playlist_path = str(playlist)
        res = public.playlist_snapshot(lambda: public.read_playlist_entries(config))
        assert res.songs == ["Jingle Bells", "Silent Night"]

    def test_rate_limit_shared(self):
        client = make_client(state={"now_playing": "0"}, entries=ENTRIES)
        for _ in range(public.PUBLIC_REQUESTS_PER_MINUTE):
            client.get("/api/public/playlist")
        assert client.get("/api/public/playlist").status_code == 429


@pytest.mark.unit
def test_read_state_returns_only_public_keys():
    config = Mock()
    config.get_state.side_effect = lambda key, default="": {"now_playing": "1"}.get(key, default)
    state = public.read_state(config)
    config.reload_state.assert_called_once()
    assert set(state) == set(public.STATE_KEYS)


@pytest.mark.unit
def test_public_routes_are_mounted_without_auth():
    main.app.openapi_schema = None
    paths = main.app.openapi()["paths"]
    for path in ("/api/public/now-playing", "/api/public/schedule", "/api/public/playlist"):
        assert not paths[path]["get"].get("security")


@pytest.mark.unit
def test_public_routes_work_on_the_real_app():
    public.set_config(None)
    res = TestClient(main.app).get("/api/public/now-playing")
    assert res.status_code == 200 and res.json()["state"] == "off"
