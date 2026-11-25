"""Analytics maths over seeded events, and the /api/events + /api/analytics endpoints."""
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import event_log
from api.core.auth import get_current_user
from api.routers import events as events_router
from api.services import event_analytics as ea


def ts(day, hour, minute=0):
    """Local-time epoch for 2026-12-<day> <hour>:<minute> (Dec 7 2026 is a Monday)."""
    return datetime(2026, 12, day, hour, minute).timestamp()


def start(song, t, variant=None):
    return {"type": "song_start", "ts": t, "song": song, "variant": variant,
            "details": {"duration_s": 200.0}}


def end(song, t, reason, elapsed, duration=200.0, variant=None):
    return {"type": "song_end", "ts": t, "song": song, "variant": variant,
            "details": {"reason": reason, "elapsed_s": elapsed, "duration_s": duration}}


def seeded():
    """Carol: 3 plays (2 completed, 1 skipped at 50 s). Jingle: 2 plays (1 skipped
    at 20 s, 1 stopped at 100 s). Silent: 1 start with no end (killed)."""
    return [
        start("Carol", ts(7, 18), "fft"), end("Carol", ts(7, 18, 4), "completed", 198, variant="fft"),
        start("Jingle", ts(7, 18, 5)), end("Jingle", ts(7, 18, 6), "skipped", 20),
        start("Carol", ts(8, 19), "onset"), end("Carol", ts(8, 19, 1), "skipped", 50, variant="onset"),
        start("Carol", ts(8, 19, 5), "onset"),
        end("Carol", ts(8, 19, 8), "completed", 199, variant="onset"),
        start("Jingle", ts(12, 20)), end("Jingle", ts(12, 20, 2), "stopped", 100),
        start("Silent", ts(12, 21)),
        {"type": "show_start", "ts": ts(7, 17, 59), "source": "cron", "details": {}},
        {"type": "button_press", "ts": ts(7, 18, 5), "details": {"name": "skip"}},
        {"type": "error", "ts": ts(8, 22), "details": {}},
    ]


@pytest.mark.unit
class TestSongStats:

    def test_per_song(self):
        rows = {r["song"]: r for r in ea.song_stats(seeded())}
        carol = rows["Carol"]
        assert (carol["plays"], carol["completions"], carol["skips"], carol["stops"]) == (3, 2, 1, 0)
        assert carol["completion_rate"] == pytest.approx(2 / 3, abs=1e-4)
        assert carol["skip_rate"] == pytest.approx(1 / 3, abs=1e-4)
        # completed counts as 100%, skipped at 50/200 = 25%
        assert carol["avg_listen_pct"] == pytest.approx((100 + 25 + 100) / 3, abs=0.1)

        jingle = rows["Jingle"]
        assert (jingle["plays"], jingle["skips"], jingle["stops"]) == (2, 1, 1)
        assert jingle["completion_rate"] == 0
        assert jingle["avg_listen_pct"] == pytest.approx((10 + 50) / 2)

        silent = rows["Silent"]
        assert silent["plays"] == 1
        assert silent["avg_listen_pct"] is None
        assert silent["completion_rate"] == 0

    def test_sorted_by_plays(self):
        assert [r["song"] for r in ea.song_stats(seeded())] == ["Carol", "Jingle", "Silent"]

    def test_by_variant(self):
        rows = {(r["song"], r["variant"]): r for r in ea.song_stats(seeded(), by_variant=True)}
        assert rows[("Carol", "onset")]["plays"] == 2
        assert rows[("Carol", "onset")]["skip_rate"] == 0.5
        assert rows[("Carol", "fft")]["completion_rate"] == 1.0
        assert rows[("Jingle", None)]["plays"] == 2

    def test_end_without_start_still_counts_as_play(self):
        rows = ea.song_stats([end("X", 1.0, "skipped", 10)])
        assert rows[0]["plays"] == 1
        assert rows[0]["skip_rate"] == 1.0

    def test_listen_fraction_edges(self):
        assert ea.listen_fraction(end("X", 0, "skipped", 500)) == 1.0      # capped
        assert ea.listen_fraction(end("X", 0, "skipped", 10, duration=None)) is None
        assert ea.listen_fraction(end("X", 0, "stopped", 10, duration=0)) is None
        assert ea.listen_fraction(end("X", 0, "completed", 5, duration=None)) == 1.0

    def test_empty(self):
        assert ea.song_stats([]) == []


@pytest.mark.unit
class TestHourStats:

    def test_hours_and_weekdays(self):
        result = ea.hour_stats(seeded())
        by_hour = {h["hour"]: h for h in result["by_hour"]}
        assert len(result["by_hour"]) == 24
        assert (by_hour[18]["plays"], by_hour[18]["skips"]) == (2, 1)
        assert (by_hour[19]["plays"], by_hour[19]["skips"]) == (2, 1)
        assert by_hour[20]["plays"] == 1 and by_hour[21]["plays"] == 1
        days = {d["name"]: d for d in result["by_weekday"]}
        assert days["Mon"]["plays"] == 2   # Dec 7
        assert days["Tue"]["plays"] == 2   # Dec 8
        assert days["Sat"]["plays"] == 2   # Dec 12
        assert result["heatmap"][0][18] == 2
        assert sum(map(sum, result["heatmap"])) == 6


@pytest.mark.unit
class TestSummary:

    def test_summary(self):
        s = ea.summary(seeded())
        assert s["plays"] == 6
        assert s["completions"] == 2
        assert s["skips"] == 2
        assert s["stops"] == 1
        assert s["skip_rate"] == pytest.approx(1 / 3, abs=1e-4)
        assert s["nights"] == 3
        assert s["show_starts"] == 1
        assert s["button_presses"] == 1
        assert s["errors"] == 1
        assert s["distinct_songs"] == 3
        assert s["top_song"] == "Carol"
        assert s["most_skipped_song"] == "Jingle"   # 50% beats Carol's 33%
        assert s["listened_hours"] == pytest.approx((198 + 20 + 50 + 199 + 100) / 3600, abs=0.01)
        assert s["first_event"] == ts(7, 17, 59)

    def test_empty(self):
        s = ea.summary([])
        assert s["plays"] == 0
        assert s["skip_rate"] is None
        assert s["top_song"] is None


@pytest.fixture
def client(events_db):
    for e in seeded():
        event_log.record(e["type"], source=e.get("source"), song=e.get("song"),
                         variant=e.get("variant"), ts=e["ts"], **e["details"])
    app = FastAPI()
    app.include_router(events_router.router)
    app.dependency_overrides[get_current_user] = lambda: {"username": "tester", "id": 1}
    return TestClient(app)


@pytest.mark.integration
class TestEndpoints:

    def test_events_paged_newest_first(self, client):
        res = client.get("/api/events", params={"limit": 3})
        assert res.status_code == 200
        body = res.json()
        assert body["total"] == 14
        assert body["enabled"] is True
        assert len(body["events"]) == 3
        assert body["events"][0]["song"] == "Silent"   # Dec 12 21:00 is the newest
        page2 = client.get("/api/events", params={"limit": 3, "offset": 3}).json()
        assert page2["events"][0]["ts"] <= body["events"][-1]["ts"]

    def test_events_filter_by_type_and_time(self, client):
        body = client.get("/api/events", params={"type": "song_start,show_start"}).json()
        assert body["total"] == 7
        body = client.get("/api/events", params=[("type", "song_end"), ("type", "error")]).json()
        assert body["total"] == 6
        body = client.get("/api/events", params={
            "since": datetime(2026, 12, 8).isoformat(),
            "until": datetime(2026, 12, 9).isoformat()}).json()
        assert body["total"] == 5
        assert {e["details"].get("reason") for e in body["events"]} >= {"skipped", "completed"}

    def test_events_limit_bounds(self, client):
        assert client.get("/api/events", params={"limit": 0}).status_code == 422
        assert client.get("/api/events", params={"limit": 501}).status_code == 422

    def test_analytics_songs(self, client):
        body = client.get("/api/analytics/songs").json()
        assert [r["song"] for r in body["songs"]] == ["Carol", "Jingle", "Silent"]
        body = client.get("/api/analytics/songs", params={"by_variant": "true"}).json()
        assert {(r["song"], r["variant"]) for r in body["songs"]} == {
            ("Carol", "fft"), ("Carol", "onset"), ("Jingle", None), ("Silent", None)}

    def test_analytics_songs_since(self, client):
        body = client.get("/api/analytics/songs",
                          params={"since": datetime(2026, 12, 12).isoformat()}).json()
        assert {r["song"] for r in body["songs"]} == {"Jingle", "Silent"}

    def test_analytics_hours(self, client):
        body = client.get("/api/analytics/hours").json()
        assert body["by_hour"][18]["plays"] == 2
        assert len(body["heatmap"]) == 7

    def test_analytics_summary(self, client):
        body = client.get("/api/analytics/summary").json()
        assert body["plays"] == 6
        assert body["nights"] == 3
        assert body["enabled"] is True

    def test_no_database_yet(self, tmp_path):
        event_log.configure(enabled=True, db_path=str(tmp_path / "none" / "e.db"))
        app = FastAPI()
        app.include_router(events_router.router)
        app.dependency_overrides[get_current_user] = lambda: {"username": "t"}
        c = TestClient(app)
        assert c.get("/api/events").json()["total"] == 0
        assert c.get("/api/analytics/summary").json()["plays"] == 0

    def test_corrupt_database_is_503(self, tmp_path):
        bad = tmp_path / "bad.db"
        bad.write_bytes(b"garbage" * 200)
        event_log.configure(enabled=True, db_path=str(bad))
        app = FastAPI()
        app.include_router(events_router.router)
        app.dependency_overrides[get_current_user] = lambda: {"username": "t"}
        assert TestClient(app).get("/api/events").status_code == 503
