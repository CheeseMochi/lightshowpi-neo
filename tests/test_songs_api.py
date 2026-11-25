"""Tests for the Songs API (api/routers/songs.py, api/services/songs.py).

Everything runs on a temp playlist, music folder and sequences folder.
"""

import configparser
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.core.auth import get_current_user, require_admin
from api.routers import songs as songs_router
from api.services import songs as songs_service
from api.services.lightshow_manager import LightshowManager
from api.services.songs import SongLibrary

pytestmark = pytest.mark.integration


@pytest.fixture
def home(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    for name in ("a.mp3", "b.mp3", "c.mp3"):
        (music / name).write_bytes(b"")
    (tmp_path / "config").mkdir()
    playlist = music / ".playlist"
    playlist.write_text(
        "Song A\t$SYNCHRONIZED_LIGHTS_HOME/music/a.mp3\n"
        "Song B\tmusic/b.mp3\tvoter1,voter2\n"
        "Song C\t" + str(music / "c.mp3") + "\n")
    seq = tmp_path / "sequences" / "b"
    seq.mkdir(parents=True)
    (seq / "onset_bands.csv").write_text("0,255,0\n")
    (seq / "stems.csv").write_text("255,0,255\n")
    (music / "a.csv").write_text("1,2,3\n")
    return tmp_path


@pytest.fixture
def library(home):
    return SongLibrary(playlist_path=str(home / "music" / ".playlist"),
                       home_dir=str(home),
                       sequences_dir=str(home / "sequences"),
                       gpio_len=3)


@pytest.fixture
def manager():
    mgr = Mock(spec=LightshowManager)
    mgr.is_running.return_value = True
    mgr.current_playlist = None
    mgr.config = Mock()
    return mgr


@pytest.fixture
def client(library, manager):
    app = FastAPI()
    app.include_router(songs_router.router)
    app.dependency_overrides[songs_router.get_library] = lambda: library
    app.dependency_overrides[songs_router.get_manager] = lambda: manager
    app.dependency_overrides[require_admin] = lambda: {"username": "tester", "id": 1}
    app.dependency_overrides[get_current_user] = lambda: {"username": "tester", "id": 1}
    return TestClient(app)


def playlist_lines(home):
    return (home / "music" / ".playlist").read_text().splitlines()


def read_cfg(path):
    config = configparser.RawConfigParser(allow_no_value=True)
    config.read(path)
    return config


class TestList:

    def test_lists_songs(self, client, home):
        res = client.get("/api/songs")
        assert res.status_code == 200
        data = res.json()
        assert [s["name"] for s in data["songs"]] == ["Song A", "Song B", "Song C"]
        a, b, c = data["songs"]
        assert a["path"] == "music/a.mp3" and a["exists"] and a["legacy_csv"]
        assert b["variants"] == ["onset_bands", "stems"] and b["votes"] == 2
        assert c["path"] == "music/c.mp3"
        assert a["sequence"] is None and a["has_config"] is False
        assert data["disabled"] == []

    def test_missing_playlist_is_404_not_exit(self, client, home):
        (home / "music" / ".playlist").unlink()
        assert client.get("/api/songs").status_code == 404


class TestOrder:

    def test_reorder_keeps_votes(self, client, home):
        res = client.put("/api/songs/order", json={"order": [2, 0, 1]})
        assert res.status_code == 200
        assert [s["name"] for s in res.json()["songs"]] == ["Song C", "Song A", "Song B"]
        lines = playlist_lines(home)
        assert lines[2] == "Song B\tmusic/b.mp3\tvoter1,voter2"
        assert (home / "music" / ".playlist.bak").exists()

    @pytest.mark.parametrize("order", [[0, 1], [0, 0, 1], [0, 1, 3]])
    def test_bad_order_is_409(self, client, home, order):
        before = playlist_lines(home)
        assert client.put("/api/songs/order", json={"order": order}).status_code == 409
        assert playlist_lines(home) == before


class TestUpdate:

    def test_rename(self, client, home):
        res = client.patch("/api/songs/1", json={"name": "  Bee  "})
        assert res.status_code == 200
        assert playlist_lines(home)[1].startswith("Bee\tmusic/b.mp3")

    @pytest.mark.parametrize("name", ["", "a\tb", "x" * 201])
    def test_bad_name(self, client, name):
        assert client.patch("/api/songs/0", json={"name": name}).status_code == 422

    def test_disable_and_enable(self, client, home):
        res = client.patch("/api/songs/0", json={"enabled": False})
        data = res.json()
        assert [s["name"] for s in data["songs"]] == ["Song B", "Song C"]
        assert [s["name"] for s in data["disabled"]] == ["Song A"]
        assert not data["disabled"][0]["enabled"]
        assert "Song A" not in (home / "music" / ".playlist").read_text()
        assert "Song A" in (home / "music" / ".playlist.disabled").read_text()

        res = client.patch("/api/songs/disabled/0", json={"enabled": True})
        assert [s["name"] for s in res.json()["songs"]] == ["Song B", "Song C", "Song A"]
        assert res.json()["disabled"] == []

    def test_cannot_disable_last_song(self, client):
        client.patch("/api/songs/0", json={"enabled": False})
        client.patch("/api/songs/0", json={"enabled": False})
        assert client.patch("/api/songs/0", json={"enabled": False}).status_code == 409

    def test_unknown_index(self, client):
        assert client.patch("/api/songs/9", json={"name": "x"}).status_code == 404
        assert client.patch("/api/songs/disabled/0", json={"enabled": True}).status_code == 404

    def test_extra_fields_rejected(self, client):
        assert client.patch("/api/songs/0", json={"path": "/etc/passwd"}).status_code == 422

    def test_show_parser_reads_result(self, client, home):
        """The rewritten playlist still parses with the show's get_playlist()."""
        import configuration_manager
        client.put("/api/songs/order", json={"order": [1, 2, 0]})
        cfg = configuration_manager.Configuration.__new__(configuration_manager.Configuration)
        cfg.playlist_path = str(home / "music" / ".playlist")
        songs = cfg.get_playlist()
        assert [s[0] for s in songs] == ["Song B", "Song C", "Song A"]
        assert songs[0][2] == {"voter1", "voter2"}


class TestSettings:

    def cfg(self, home, song="b.mp3"):
        return home / "music" / f".{song}.cfg"

    def test_get_empty(self, client):
        data = client.get("/api/songs/1/settings").json()
        assert data["exists"] is False
        assert data["settings"]["sequence"] == ""
        assert data["variants"] == ["onset_bands", "stems"]
        assert data["config_path"] == "music/.b.mp3.cfg"

    def test_put_creates_cfg(self, client, home):
        res = client.put("/api/songs/1/settings", json={
            "sequence": " rotate: onset_bands , stems,fft ",
            "always_off_channels": "2, 5",
            "attenuate_pct": "10",
        })
        assert res.status_code == 200, res.text
        assert res.json()["settings"]["sequence"] == "rotate:onset_bands,stems,fft"
        config = read_cfg(self.cfg(home))
        assert config.get("custom_lightshow", "sequence") == "rotate:onset_bands,stems,fft"
        assert config.get("custom_lightshow", "always_off_channels") == "2,5"
        assert client.get("/api/songs").json()["songs"][1]["sequence"] == \
            "rotate:onset_bands,stems,fft"

    def test_put_keeps_other_sections_and_backs_up(self, client, home):
        path = self.cfg(home)
        path.write_text("[custom_lightshow]\ninvert_channels = 1\n\n[fft]\n"
                        "# do not edit this section\nchunk_size = 2048\n")
        res = client.put("/api/songs/1/settings", json={"sequence": "stems"})
        assert res.status_code == 200
        config = read_cfg(path)
        assert config.get("fft", "chunk_size") == "2048"
        assert config.get("custom_lightshow", "invert_channels") == "1"
        assert (home / "music" / ".b.mp3.cfg.bak").exists()

    def test_blank_removes_key(self, client, home):
        client.put("/api/songs/1/settings", json={"always_on_channels": "1", "sequence": "fft"})
        client.put("/api/songs/1/settings", json={"always_on_channels": ""})
        config = read_cfg(self.cfg(home))
        assert not config.has_option("custom_lightshow", "always_on_channels")
        assert config.get("custom_lightshow", "sequence") == "fft"

    def test_postshow_pair_written_together(self, client, home):
        client.put("/api/songs/1/settings",
                   json={"postshow_configuration": '{"transitions": []}'})
        config = read_cfg(self.cfg(home))
        assert config.get("custom_lightshow", "postshow_script") == ""
        client.put("/api/songs/1/settings", json={"postshow_configuration": ""})
        config = read_cfg(self.cfg(home))
        assert not config.has_option("custom_lightshow", "postshow_script")
        assert not config.has_option("custom_lightshow", "postshow_configuration")

    @pytest.mark.parametrize("body", [
        {"sequence": "rotate:"},
        {"sequence": "nope"},
        {"sequence": "../../x"},
        {"always_on_channels": "1,a"},
        {"always_on_channels": ","},
        {"attenuate_pct": "abc"},
        {"attenuate_pct": "150"},
        {"preshow_configuration": "{not json"},
        {"preshow_script": "a\nb"},
        {"bogus": "1"},
    ])
    def test_invalid_rejected(self, client, home, body):
        assert client.put("/api/songs/1/settings", json=body).status_code == 422
        assert not self.cfg(home).exists()

    def test_written_cfg_loads_in_show_sequence_reader(self, client, home):
        import sequence_select
        client.put("/api/songs/1/settings", json={"sequence": "random:stems,onset_bands"})
        assert sequence_select.read_sequence_setting(str(self.cfg(home))) == \
            "random:stems,onset_bands"

    def test_permission_error_is_403(self, client, home, monkeypatch):
        def deny(*args, **kwargs):
            raise PermissionError("denied")
        monkeypatch.setattr(songs_service, "_atomic_copy_text", deny)
        assert client.put("/api/songs/1/settings", json={"sequence": "fft"}).status_code == 403


class TestPlay:

    def test_play_sets_one_based_play_now(self, client, manager):
        res = client.post("/api/songs/2/play")
        assert res.status_code == 200
        assert res.json()["name"] == "Song C"
        manager.config.update_state.assert_called_once_with("play_now", "3")

    def test_not_running_is_409(self, client, manager):
        manager.is_running.return_value = False
        assert client.post("/api/songs/0/play").status_code == 409
        manager.config.update_state.assert_not_called()

    def test_other_playlist_is_409(self, client, manager, home):
        manager.current_playlist = str(home / "other.playlist")
        assert client.post("/api/songs/0/play").status_code == 409

    def test_same_playlist_ok(self, client, manager, library):
        manager.current_playlist = library.playlist_path
        assert client.post("/api/songs/0/play").status_code == 200

    def test_unknown_index_is_404(self, client):
        assert client.post("/api/songs/7/play").status_code == 404


@pytest.mark.unit
def test_routes_require_auth():
    """Without overrides every songs route needs a token."""
    app = FastAPI()
    app.include_router(songs_router.router)
    c = TestClient(app)
    for method, path in [("get", "/api/songs"), ("put", "/api/songs/order"),
                         ("patch", "/api/songs/0"), ("get", "/api/songs/0/settings"),
                         ("put", "/api/songs/0/settings"), ("post", "/api/songs/0/play"),
                         ("patch", "/api/songs/disabled/0")]:
        assert getattr(c, method)(path).status_code in (401, 403), (method, path)
