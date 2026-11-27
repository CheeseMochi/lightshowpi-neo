"""Tests for the Songs API (api/routers/songs.py, api/services/songs.py).

Everything runs on a temp playlist, music folder and sequences folder.
"""

import configparser
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import sequence_select
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.core.auth import get_current_user, require_admin
from api.routers import songs as songs_router
from api.services import songs as songs_service
from api.services.lightshow_manager import LightshowManager
from api.services.songs import SongLibrary
from tests.test_get_song_sequence import sl  # noqa: F401  (fixture: the show module)

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
def manager(library):
    mgr = Mock(spec=LightshowManager)
    mgr.is_running.return_value = True
    mgr.current_playlist = library.playlist_path
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

    def test_permission_error_is_403(self, client, home, monkeypatch):
        def deny(*args, **kwargs):
            raise PermissionError("denied")
        monkeypatch.setattr(songs_service, "_atomic_copy_text", deny)
        before = playlist_lines(home)
        assert client.put("/api/songs/order", json={"order": [2, 1, 0]}).status_code == 403
        assert playlist_lines(home) == before

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

    def test_script_must_be_py_inside_home(self, client, home, tmp_path_factory):
        script = home / "scripts" / "pre.py"
        script.parent.mkdir()
        script.write_text("def main(exit_event):\n    pass\n")
        outside = tmp_path_factory.mktemp("elsewhere") / "evil.py"
        outside.write_text("")
        for bad in [str(outside), "scripts/pre.py", str(home / "scripts" / "missing.py"),
                    str(home / "music" / "a.mp3"), str(home / "scripts" / ".." / ".." / outside.name)]:
            res = client.put("/api/songs/1/settings", json={"preshow_script": bad})
            assert res.status_code == 422, bad
        res = client.put("/api/songs/1/settings", json={"preshow_script": str(script)})
        assert res.status_code == 200
        assert res.json()["settings"]["preshow_script"] == str(script)

    def test_only_changed_keys_are_validated(self, client, home):
        """A legacy value the validator would reject doesn't block other edits."""
        self.cfg(home).write_text("[custom_lightshow]\npreshow_script = /old/place/pre.py\n")
        res = client.put("/api/songs/1/settings", json={"attenuate_pct": "5"})
        assert res.status_code == 200
        config = read_cfg(self.cfg(home))
        assert config.get("custom_lightshow", "preshow_script") == "/old/place/pre.py"
        assert config.get("custom_lightshow", "preshow_configuration") == ""

    def test_unparsable_cfg_is_409(self, client, home):
        self.cfg(home).write_text("garbage without a section\n")
        assert client.put("/api/songs/1/settings", json={"sequence": "fft"}).status_code == 409
        assert self.cfg(home).read_text() == "garbage without a section\n"

    def test_permission_error_is_403(self, client, home, monkeypatch):
        def deny(*args, **kwargs):
            raise PermissionError("denied")
        monkeypatch.setattr(songs_service, "_atomic_copy_text", deny)
        assert client.put("/api/songs/1/settings", json={"sequence": "fft"}).status_code == 403


class TestShowLoadsWrittenSettings:
    """Whatever the API writes, the show's load_custom_config() must accept."""

    @pytest.fixture
    def show(self, sl, monkeypatch):
        cm = SimpleNamespace(lightshow=SimpleNamespace(preshow=None, postshow=None),
                             audio_processing=SimpleNamespace())
        monkeypatch.setattr(sl, "cm", cm)
        monkeypatch.setattr(sl, "hc", Mock())
        show = sl.Lightshow.__new__(sl.Lightshow)
        show.attenuate_pct = 0.0
        return SimpleNamespace(show=show, cm=cm, hc=sl.hc)

    @pytest.fixture
    def script(self, home):
        path = home / "scripts" / "post.py"
        path.parent.mkdir(exist_ok=True)
        path.write_text("def main(exit_event):\n    pass\n")
        return str(path)

    def load(self, show, home):
        show.show.config_filename = str(home / "music" / ".b.mp3.cfg")
        show.show.load_custom_config()

    @pytest.mark.parametrize("body", [
        {"postshow_configuration": '{"transitions": []}'},
        {"preshow_configuration": '{"transitions": []}'},
        {"always_on_channels": "1, 2", "always_off_channels": "3", "invert_channels": "-1"},
        {"network_always_on": "1", "network_always_off": "2", "network_invert": "3"},
        {"attenuate_pct": "12.5"},
        {"sequence": "rotate:stems,fft"},
    ])
    def test_loads(self, client, home, show, body):
        assert client.put("/api/songs/1/settings", json=body).status_code == 200
        self.load(show, home)

    def test_postshow_script_only(self, client, home, show, script):
        assert client.put("/api/songs/1/settings", json={"postshow_script": script}).status_code == 200
        self.load(show, home)
        assert show.cm.lightshow.postshow == script

    def test_values_reach_the_show(self, client, home, show):
        client.put("/api/songs/1/settings", json={
            "always_off_channels": "2,5", "attenuate_pct": "20",
            "postshow_configuration": '{"transitions": [{"type": "off"}]}'})
        self.load(show, home)
        assert show.cm.lightshow.always_off_channels == [2, 5]
        assert show.show.attenuate_pct == 20.0
        assert show.cm.lightshow.postshow == {"transitions": [{"type": "off"}]}

    def test_cleared_settings_load(self, client, home, show, script):
        client.put("/api/songs/1/settings", json={"postshow_script": script, "always_on_channels": "1"})
        client.put("/api/songs/1/settings", json={"postshow_script": "", "always_on_channels": ""})
        self.load(show, home)


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

    def test_not_a_playlist_show_is_409(self, client, manager):
        manager.current_playlist = None  # ambient, audio-in or --file
        assert client.post("/api/songs/0/play").status_code == 409
        manager.config.update_state.assert_not_called()

    def test_unknown_index_is_404(self, client):
        assert client.post("/api/songs/7/play").status_code == 404

    def test_no_body_with_json_content_type(self, client, manager):
        """What the web UI's plain Play button sends."""
        res = client.post("/api/songs/2/play", headers={"Content-Type": "application/json"})
        assert res.status_code == 200
        manager.config.update_state.assert_called_once_with("play_now", "3")

    def test_empty_body_plays_without_variant(self, client, manager):
        res = client.post("/api/songs/1/play", json={})
        assert res.status_code == 200 and res.json()["variant"] is None
        manager.config.update_state.assert_called_once_with("play_now", "2")

    def test_variant_sets_sequence_once_before_play_now(self, client, manager):
        res = client.post("/api/songs/1/play", json={"variant": "stems"})
        assert res.status_code == 200 and res.json()["variant"] == "stems"
        assert manager.config.update_state.call_args_list == [
            call("sequence_once", "b|stems"), call("play_now", "2")]

    def test_written_value_is_what_the_show_takes(self, client, manager, home):
        """The API's state value round-trips through sequence_select.take_once."""
        client.post("/api/songs/1/play", json={"variant": "onset_bands"})
        state = {k: v for (k, v), _ in manager.config.update_state.call_args_list}
        song = str(home / "music" / "b.mp3")
        assert sequence_select.take_once(song, state.get, state.__setitem__) == "onset_bands"
        assert state["sequence_once"] == ""

    def test_missing_variant_is_404(self, client, manager):
        assert client.post("/api/songs/1/play", json={"variant": "nope"}).status_code == 404
        manager.config.update_state.assert_not_called()

    @pytest.mark.parametrize("variant", ["fft", "FFT", "../x", "a b"])
    def test_bad_variant_is_422(self, client, manager, variant):
        assert client.post("/api/songs/1/play", json={"variant": variant}).status_code == 422
        manager.config.update_state.assert_not_called()

    def test_unusable_variant_is_422(self, client, manager, home):
        (home / "sequences" / "b" / "wide.csv").write_text("1,2,3,4\n")
        res = client.post("/api/songs/1/play", json={"variant": "wide"})
        assert res.status_code == 422 and "columns" in res.json()["detail"]
        manager.config.update_state.assert_not_called()

    def test_auditions_off_is_409(self, client, manager, library):
        library.allow_audition = False
        assert client.post("/api/songs/1/play", json={"variant": "stems"}).status_code == 409
        manager.config.update_state.assert_not_called()

    def test_variant_with_show_stopped_is_409(self, client, manager):
        manager.is_running.return_value = False
        assert client.post("/api/songs/1/play", json={"variant": "stems"}).status_code == 409
        manager.config.update_state.assert_not_called()


def upload(client, index, name, data, replace=None, filename="x.csv"):
    params = {"replace": replace} if replace is not None else None
    return client.put(f"/api/songs/{index}/variants/{name}", params=params,
                      files={"file": (filename, data, "text/csv")})


class TestVariants:

    def test_list(self, client, home):
        res = client.get("/api/songs/1/variants")
        assert res.status_code == 200
        data = res.json()
        assert data["gpio_len"] == 3 and data["folder"] == "sequences/b"
        names = [v["name"] for v in data["variants"]]
        assert names == ["onset_bands", "stems"]
        first = data["variants"][0]
        assert first["rows"] == 1 and first["columns"] == 3 and first["size"] == 8
        assert first["problem"] is None and first["mtime"] > 0

    def test_list_flags_problem(self, client, home):
        (home / "sequences" / "b" / "wide.csv").write_text("1,2,3,4\n")
        wide = [v for v in client.get("/api/songs/1/variants").json()["variants"]
                if v["name"] == "wide"][0]
        assert "columns" in wide["problem"]

    def test_list_empty(self, client):
        assert client.get("/api/songs/0/variants").json()["variants"] == []

    def test_upload_creates_folder(self, client, home):
        res = upload(client, 0, "new_one", b"0,1,2\n3,4,5\n")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["name"] == "new_one" and body["rows"] == 2 and body["replaced"] is False
        path = home / "sequences" / "a" / "new_one.csv"
        assert path.read_bytes() == b"0,1,2\n3,4,5\n"
        assert oct(path.stat().st_mode & 0o777) == oct(0o644)
        assert [p.name for p in path.parent.iterdir()] == ["new_one.csv"]  # no temp left

    def test_uploaded_variant_is_selectable(self, client, home):
        upload(client, 0, "mine", b"0,1,2\n")
        res = client.put("/api/songs/0/settings", json={"sequence": "mine"})
        assert res.status_code == 200

    @pytest.mark.parametrize("name", ["fft", "CSV", "rotate", "random", "-x", "a.b", "a" * 65])
    def test_bad_or_reserved_name_is_422(self, client, home, name):
        res = upload(client, 0, name, b"0,1,2\n")
        assert res.status_code == 422
        assert not (home / "sequences" / "a").exists()

    def test_wrong_columns_is_422(self, client, home):
        res = upload(client, 0, "wide", b"0,1,2\n0,1,2,3\n")
        assert res.status_code == 422
        assert "row 2" in res.json()["detail"]
        assert list((home / "sequences" / "a").iterdir()) == []

    @pytest.mark.parametrize("data", [b"", b"a,b,c\n", b"\xff\xfe\x00\n"])
    def test_not_csv_is_422(self, client, home, data):
        assert upload(client, 0, "bad", data).status_code == 422
        assert list((home / "sequences" / "a").iterdir()) == []

    def test_upload_over_one_mb_at_real_cap(self, client, home):
        data = b"255,128,0\n" * 300_000  # ~3 MB, above starlette's 1 MB part default
        res = upload(client, 0, "large", data)
        assert res.status_code == 200, res.text
        assert res.json()["rows"] == 300_000
        assert (home / "sequences" / "a" / "large.csv").stat().st_size == len(data)

    def test_too_big_is_422(self, client, home, monkeypatch):
        monkeypatch.setattr(songs_service, "MAX_VARIANT_BYTES", 100)
        res = upload(client, 0, "big", b"0,1,2\n" * 50)
        assert res.status_code == 422 and "larger" in res.json()["detail"]
        assert list((home / "sequences" / "a").iterdir()) == []

    def test_too_big_content_length_refused_before_parsing(self, client, monkeypatch):
        monkeypatch.setattr(songs_router, "MAX_VARIANT_BYTES", 10)
        monkeypatch.setattr(songs_router, "_MULTIPART_OVERHEAD", 10)
        assert upload(client, 0, "big", b"0,1,2\n" * 50).status_code == 422

    def test_missing_file_field_is_422(self, client):
        res = client.put("/api/songs/0/variants/x", data={"other": "1"})
        assert res.status_code == 422

    def test_existing_is_409_unless_replace(self, client, home):
        path = home / "sequences" / "b" / "stems.csv"
        res = upload(client, 1, "stems", b"1,1,1\n")
        assert res.status_code == 409
        assert path.read_text() == "255,0,255\n"
        res = upload(client, 1, "stems", b"1,1,1\n", replace="true")
        assert res.status_code == 200 and res.json()["replaced"] is True
        assert path.read_text() == "1,1,1\n"

    def test_unknown_song_is_404(self, client):
        assert upload(client, 9, "x", b"0,1,2\n").status_code == 404

    def test_upload_and_delete_are_logged(self, client, events_db):
        import event_log
        upload(client, 0, "logged", b"0,1,2\n")
        client.delete("/api/songs/0/variants/logged")
        rows, _total = event_log.fetch_events(types=["sequence_upload", "sequence_delete"])
        by_type = {e["type"]: e for e in rows}
        assert by_type["sequence_upload"]["variant"] == "logged"
        assert by_type["sequence_upload"]["song"] == "Song A"
        assert by_type["sequence_upload"]["details"]["user"] == "tester"
        assert by_type["sequence_delete"]["variant"] == "logged"

    def test_delete(self, client, home):
        res = client.delete("/api/songs/1/variants/stems")
        assert res.status_code == 200
        assert not (home / "sequences" / "b" / "stems.csv").exists()

    def test_delete_missing_is_404(self, client):
        assert client.delete("/api/songs/1/variants/nope").status_code == 404

    @pytest.mark.parametrize("setting", ["stems", "rotate:onset_bands,stems", "random:fft,stems"])
    def test_delete_in_use_is_409(self, client, home, setting):
        assert client.put("/api/songs/1/settings", json={"sequence": setting}).status_code == 200
        res = client.delete("/api/songs/1/variants/stems")
        assert res.status_code == 409 and "sequence setting" in res.json()["detail"]
        assert (home / "sequences" / "b" / "stems.csv").exists()

    def test_delete_with_other_variant_in_use(self, client, home):
        client.put("/api/songs/1/settings", json={"sequence": "onset_bands"})
        assert client.delete("/api/songs/1/variants/stems").status_code == 200

    def test_delete_with_unparsable_setting(self, client, home):
        (home / "music" / ".b.mp3.cfg").write_text("[custom_lightshow]\nsequence = bad:x\n")
        assert client.delete("/api/songs/1/variants/stems").status_code == 200


class TestExpectedChannels:

    def cm(self, tmp_path, leds, gpio_len=8):
        config_dir = tmp_path / "config"
        config_dir.mkdir(exist_ok=True)
        names = []
        for i, (conn, mode, count) in enumerate(leds):
            name = f"led{i}.cfg"
            (config_dir / name).write_text(
                f"[led]\nled_configuration = STRIP\nled_connection = {conn}\n"
                f"led_channel_configuration = {mode}\nled_channel_count = {count}\n")
            names.append(name)
        return SimpleNamespace(hardware=SimpleNamespace(gpio_len=gpio_len),
                               configs=SimpleNamespace(led=names),
                               config_dir=str(config_dir) + "/")

    def test_no_leds(self, tmp_path):
        assert songs_service.expected_channels(self.cm(tmp_path, [])) == 8

    def test_extend_adds(self, tmp_path):
        cm = self.cm(tmp_path, [("SACN", "EXTEND", 10), ("SPI", "EXTEND", 2)])
        assert songs_service.expected_channels(cm) == 20

    def test_ledonly_replaces_and_mirror_keeps(self, tmp_path):
        assert songs_service.expected_channels(self.cm(tmp_path, [("SACN", "LEDONLY", 5)])) == 5
        assert songs_service.expected_channels(self.cm(tmp_path, [("SACN", "MIRROR", 5)])) == 8

    def test_other_connection_ignored(self, tmp_path):
        assert songs_service.expected_channels(self.cm(tmp_path, [("NONE", "EXTEND", 5)])) == 8

    def test_unreadable_led_config_falls_back(self, tmp_path):
        cm = self.cm(tmp_path, [])
        cm.configs.led = ["missing.cfg"]
        assert songs_service.expected_channels(cm) == 8

    @staticmethod
    def with_virtual(cm, value):
        import configparser
        cm.config = configparser.RawConfigParser()
        cm.config.read_string(f"[hardware]\nvirtual_channels = {value}\n")
        return cm

    def test_virtual_channels_are_added_after_everything(self, tmp_path):
        """With Nanoleaf on 6 virtual columns, uploads need gpio_len + 6 columns."""
        assert songs_service.expected_channels(self.with_virtual(self.cm(tmp_path, []), 6)) == 14
        cm = self.with_virtual(self.cm(tmp_path, [("SACN", "EXTEND", 10)]), 3)
        assert songs_service.expected_channels(cm) == 21

    @pytest.mark.parametrize("value", ["", "x", "-2", "0"])
    def test_virtual_channels_off_or_invalid_add_nothing(self, tmp_path, value):
        assert songs_service.expected_channels(self.with_virtual(self.cm(tmp_path, []), value)) == 8


@pytest.mark.unit
def test_routes_require_auth():
    """Without overrides every songs route needs a token."""
    app = FastAPI()
    app.include_router(songs_router.router)
    c = TestClient(app)
    for method, path in [("get", "/api/songs"), ("put", "/api/songs/order"),
                         ("patch", "/api/songs/0"), ("get", "/api/songs/0/settings"),
                         ("put", "/api/songs/0/settings"), ("post", "/api/songs/0/play"),
                         ("patch", "/api/songs/disabled/0"), ("get", "/api/songs/0/variants"),
                         ("put", "/api/songs/0/variants/x"),
                         ("delete", "/api/songs/0/variants/x")]:
        assert getattr(c, method)(path).status_code in (401, 403), (method, path)
