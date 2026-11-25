"""Tests for api/services/cloud_publisher.py (pushes public data to the cloud page)."""

import json
import logging
import time
import urllib.error
from unittest.mock import MagicMock, Mock, patch

import pytest

from api.routers import public
from api.services import cloud_publisher as cp


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def payload(title="Jingle Bells", elapsed=10.0, started=5000.0, songs=("Jingle Bells",)):
    return {
        "now_playing": {"state": "playing", "title": title, "elapsed_s": elapsed, "duration_s": 120.0},
        "song_started_at": started,
        "schedule": {"note": None, "tonight": None, "shows": []},
        "playlist": {"songs": list(songs), "current": 0},
        "sent_at": 5010.0,
    }


def make(collect=None, post=None, clock=None):
    clock = clock or FakeClock()
    post = post or Mock(return_value=200)
    data = {"p": payload()}
    collect = collect or (lambda: data["p"])
    pub = cp.CloudPublisher("https://show.example.com/", "secret-key", collect,
                            post=post, clock=clock)
    return pub, post, clock, data


@pytest.mark.unit
class TestPushOnChange:

    def test_first_tick_pushes_to_ingest(self):
        pub, post, _, _ = make()
        assert pub.tick() is True
        url, key, body = post.call_args.args
        assert url == "https://show.example.com/ingest"
        assert key == "secret-key"
        assert body["now_playing"]["title"] == "Jingle Bells"

    def test_unchanged_is_not_pushed(self):
        pub, post, clock, _ = make()
        pub.tick()
        clock.t += 10
        assert pub.tick() is False
        assert post.call_count == 1

    def test_elapsed_alone_is_not_a_change(self):
        pub, post, clock, data = make()
        pub.tick()
        data["p"] = {**payload(elapsed=40.0), "sent_at": 9999.0}
        clock.t += 30
        assert pub.tick() is False

    @pytest.mark.parametrize("changed", [
        payload(title="Silent Night"),
        payload(started=6000.0),
        payload(songs=("Jingle Bells", "Silent Night")),
        {**payload(), "now_playing": {"state": "off", "title": None, "elapsed_s": None,
                                      "duration_s": None}, "song_started_at": None},
    ])
    def test_changes_are_pushed(self, changed):
        pub, post, clock, data = make()
        pub.tick()
        data["p"] = changed
        clock.t += 3
        assert pub.tick() is True
        assert post.call_count == 2

    def test_heartbeat(self):
        pub, post, clock, _ = make()
        pub.tick()
        clock.t += cp.HEARTBEAT_SECONDS - 1
        assert pub.tick() is False
        clock.t += 1
        assert pub.tick() is True
        assert post.call_count == 2


@pytest.mark.unit
class TestNeverRaises:

    def test_post_error_backs_off_then_retries(self):
        post = Mock(side_effect=urllib.error.URLError("no route"))
        pub, _, clock, _ = make(post=post)
        assert pub.tick() is False
        clock.t += 1
        assert pub.tick() is False          # inside the backoff: no request
        assert post.call_count == 1
        clock.t += cp.BACKOFF_START_SECONDS
        post.side_effect = None
        post.return_value = 200
        assert pub.tick() is True
        assert pub.failures == 1 and pub.pushes == 1

    def test_backoff_grows_and_caps(self):
        post = Mock(side_effect=OSError("down"))
        pub, _, clock, _ = make(post=post)
        waits = []
        for _ in range(10):
            pub.tick()
            waits.append(pub._backoff)
            clock.t = pub._retry_at
        assert waits[:3] == [5.0, 10.0, 20.0]
        assert max(waits) == cp.BACKOFF_MAX_SECONDS

    def test_non_2xx_is_a_failure(self):
        pub, post, _, _ = make(post=Mock(return_value=500))
        assert pub.tick() is False
        assert pub.failures == 1

    def test_collect_error_is_swallowed(self):
        pub, post, _, _ = make(collect=Mock(side_effect=RuntimeError("state file gone")))
        assert pub.tick() is False
        post.assert_not_called()

    def test_failure_warnings_are_rate_limited(self, caplog):
        post = Mock(side_effect=OSError("down"))
        pub, _, clock, _ = make(post=post)
        with caplog.at_level(logging.WARNING, logger=cp.__name__):
            for _ in range(5):
                pub.tick()
                clock.t = pub._retry_at
        assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1

    def test_unauthorized_hints_at_key(self, caplog):
        err = urllib.error.HTTPError("https://x/ingest", 401, "Unauthorized", {}, None)
        pub, _, _, _ = make(post=Mock(side_effect=err))
        with caplog.at_level(logging.WARNING, logger=cp.__name__):
            pub.tick()
        assert "cloud_key" in caplog.text
        assert "secret-key" not in caplog.text


@pytest.mark.unit
class TestStart:

    def config(self, url="https://show.example.com", key="k" * 32):
        return Mock(cloud_url=url, cloud_key=key)

    def test_blank_url_is_off(self):
        assert cp.start_cloud_publisher(self.config(url="")) is None

    @pytest.mark.parametrize("url, key", [("https://show.example.com", ""),
                                          ("ftp://show.example.com", "k"),
                                          ("not a url", "k")])
    def test_bad_settings_are_off(self, url, key):
        assert cp.start_cloud_publisher(self.config(url=url, key=key)) is None

    def test_starts_and_stops_thread(self):
        with patch.object(cp.CloudPublisher, "_run", lambda self: self._stop.wait(5)):
            pub = cp.start_cloud_publisher(self.config())
            try:
                assert pub is not None and pub._thread.is_alive()
                assert pub._thread.daemon
            finally:
                started = time.monotonic()
                pub.stop()
            assert time.monotonic() - started < 2


@pytest.mark.unit
class TestPayload:

    def api_config(self, tmp_path, state):
        playlist = tmp_path / ".playlist"
        playlist.write_text("Carol\t/m/carol.mp3\nDeck\t/m/deck.mp3\n")
        config = MagicMock()
        config.get_state.side_effect = lambda key, default="": state.get(key, default)
        config.lightshow.playlist_path = str(playlist)
        config.cm.home_dir = str(tmp_path)
        config._get_str.return_value = "Nightly"
        config.public_show_start = "17:30"
        config.public_show_stop = "22:30"
        config.public_show_days = ""
        return config

    def test_payload_is_the_public_views(self, tmp_path):
        now = time.time()
        state = {"now_playing": "1", "song_title": "Deck_the_Halls.mp3",
                 "song_path": "/m/deck.mp3", "song_duration": "200",
                 "song_started_at": str(now - 20)}
        body = cp.build_payload(self.api_config(tmp_path, state), None, now=now)
        assert set(body) == {"now_playing", "song_started_at", "schedule", "playlist", "sent_at"}
        assert set(body["now_playing"]) == set(public.PublicNowPlaying.model_fields)
        assert body["now_playing"]["title"] == "Deck the Halls"
        assert body["song_started_at"] == pytest.approx(now - 20)
        assert body["playlist"] == {"songs": ["Carol", "Deck"], "current": 1}
        assert body["schedule"]["note"] == "Nightly"
        assert body["schedule"]["shows"][0]["start"] == "17:30"
        text = json.dumps(body)
        assert "/m/" not in text and "song_path" not in text

    def test_off_has_no_start(self, tmp_path):
        body = cp.build_payload(self.api_config(tmp_path, {"now_playing": "0"}), None)
        assert body["now_playing"]["state"] == "off"
        assert body["song_started_at"] is None
        assert body["playlist"]["current"] is None

    def test_unreadable_state_is_off(self, tmp_path):
        config = self.api_config(tmp_path, {})
        config.reload_state.side_effect = OSError("locked")
        body = cp.build_payload(config, None)
        assert body["now_playing"]["state"] == "off"
        assert body["playlist"]["songs"] == ["Carol", "Deck"]

    def test_scheduler_schedules_used(self, tmp_path):
        scheduler = Mock()
        scheduler.get_schedules.return_value = [
            {"start_time": "18:00", "stop_time": "21:00", "days_of_week": [5], "enabled": True}]
        body = cp.build_payload(self.api_config(tmp_path, {"now_playing": "0"}), scheduler)
        assert [s["start"] for s in body["schedule"]["shows"]] == ["18:00"]


@pytest.mark.unit
def test_http_post_sends_bearer_json():
    response = MagicMock()
    response.__enter__.return_value.status = 204
    with patch.object(cp.urllib.request, "urlopen", return_value=response) as urlopen:
        assert cp.http_post("https://show.example.com/ingest", "abc", {"a": 1}) == 204
    request = urlopen.call_args.args[0]
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer abc"
    assert request.get_header("Content-type") == "application/json"
    assert json.loads(request.data) == {"a": 1}
    assert urlopen.call_args.kwargs["timeout"] == cp.TIMEOUT_SECONDS
