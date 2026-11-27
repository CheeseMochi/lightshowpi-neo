"""Song details on the public page: tags, light pattern and play counts."""

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import event_log
from api.routers import public
from api.services import song_tags

NOW = 1_700_000_000.0

PLAYING = {
    "now_playing": "1",
    "song_title": "Carol of the Bells by EvilWezil",
    "song_path": "/home/pi/lightshowpi/music/secret/carol.mp3",
    "song_duration": "200",
    "song_started_at": str(NOW - 50),
    "song_sequence": "onset_bands",
}

TAGS = {"title": "Carol of the Bells", "artist": "EvilWezil",
        "album": "XMAS Compilation 2010", "year": "2013"}


@pytest.fixture(autouse=True)
def fresh():
    public._cache.clear()
    public.public_limiter.reset()
    public._stats_memo.update(key=None, value=None)
    song_tags.clear_cache()
    yield
    public._cache.clear()
    public.public_limiter.reset()
    public._stats_memo.update(key=None, value=None)
    song_tags.clear_cache()


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

class FakeAudio:
    def __init__(self, tags):
        self.tags = tags


@pytest.mark.unit
class TestSongTags:

    def test_reads_easy_tags(self, tmp_path, monkeypatch):
        song = tmp_path / "carol.mp3"
        song.write_bytes(b"x")
        import mutagen
        monkeypatch.setattr(mutagen, "File", lambda path, easy: FakeAudio({
            "title": ["Carol of the Bells"], "artist": ["EvilWezil"],
            "album": ["XMAS  Compilation\n2010"], "date": ["2013-12-20 15:00:17"]}))
        assert song_tags.read_tags(str(song)) == {
            "title": "Carol of the Bells", "artist": "EvilWezil",
            "album": "XMAS Compilation 2010", "year": "2013"}

    def test_album_artist_and_original_date_fallbacks(self, tmp_path, monkeypatch):
        song = tmp_path / "a.m4a"
        song.write_bytes(b"x")
        import mutagen
        monkeypatch.setattr(mutagen, "File", lambda path, easy: FakeAudio({
            "albumartist": ["Trans-Siberian Orchestra"], "originaldate": ["1996"]}))
        tags = song_tags.read_tags(str(song))
        assert tags["artist"] == "Trans-Siberian Orchestra"
        assert tags["year"] == "1996"
        assert tags["title"] is None

    def test_cached_until_the_file_changes(self, tmp_path, monkeypatch):
        song = tmp_path / "s.mp3"
        song.write_bytes(b"x")
        calls = []
        import mutagen

        def fake(path, easy):
            calls.append(path)
            return FakeAudio({"title": [f"v{len(calls)}"]})
        monkeypatch.setattr(mutagen, "File", fake)
        assert song_tags.read_tags(str(song))["title"] == "v1"
        assert song_tags.read_tags(str(song))["title"] == "v1"
        assert len(calls) == 1
        stat = song.stat()
        import os
        os.utime(song, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))
        assert song_tags.read_tags(str(song))["title"] == "v2"

    def test_missing_untagged_and_broken_files_are_empty(self, tmp_path, monkeypatch):
        assert song_tags.read_tags("") == song_tags.EMPTY
        assert song_tags.read_tags(str(tmp_path / "nope.mp3")) == song_tags.EMPTY
        song = tmp_path / "s.mp3"
        song.write_bytes(b"x")
        import mutagen
        monkeypatch.setattr(mutagen, "File", lambda path, easy: None)
        assert song_tags.read_tags(str(song)) == song_tags.EMPTY
        song_tags.clear_cache()

        def boom(path, easy):
            raise ValueError("corrupt")
        monkeypatch.setattr(mutagen, "File", boom)
        assert song_tags.read_tags(str(song)) == song_tags.EMPTY

    def test_long_values_are_capped(self, tmp_path, monkeypatch):
        song = tmp_path / "s.mp3"
        song.write_bytes(b"x")
        import mutagen
        monkeypatch.setattr(mutagen, "File", lambda path, easy: FakeAudio({"artist": ["a" * 500]}))
        assert len(song_tags.read_tags(str(song))["artist"]) == song_tags.MAX_FIELD

    def test_real_wav_without_tags(self):
        import os
        wav = os.path.join(os.path.dirname(__file__), "fixtures", "test_tone.wav")
        assert song_tags.read_tags(wav)["artist"] is None

    @pytest.mark.parametrize("raw,year", [
        ("2013-12-20 15:00:17", "2013"), ("1996", "1996"), ("Dec 1984", "1984"),
        ("", None), (None, None), ("unknown", None), ("3000", None)])
    def test_year_from(self, raw, year):
        assert song_tags.year_from(raw) == year


# ---------------------------------------------------------------------------
# Pattern label
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("name,label", [
    ("fft", "Live audio-reactive"), ("FFT", "Live audio-reactive"),
    ("csv", "Custom sequence"), ("onset_bands", "Onset bands"),
    ("beat-chase.v2", "Beat chase v2"), ("", None), (None, None),
    ("/home/pi/music/x.csv", None), ("../etc", None), ("a b", None)])
def test_public_pattern(name, label):
    assert public.public_pattern(name) == label


# ---------------------------------------------------------------------------
# Now playing
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestNowPlayingDetails:

    def test_tags_replace_the_title_by_artist(self):
        stats = public.PublicSongStats(plays=12, completions=10, skips=2)
        res = public.build_now_playing(PLAYING, now=NOW, tags=TAGS, stats=stats)
        assert res.title == "Carol of the Bells"
        assert (res.artist, res.album, res.year) == ("EvilWezil", "XMAS Compilation 2010", "2013")
        assert res.pattern == "Onset bands"
        assert res.stats.plays == 12 and res.stats.skips == 2

    def test_without_tags_falls_back_to_the_state_title(self):
        res = public.build_now_playing({**PLAYING, "song_sequence": ""}, now=NOW)
        assert res.title == "Carol of the Bells by EvilWezil"
        assert res.artist is None and res.pattern is None and res.stats is None

    def test_snapshot_reads_tags_by_the_state_path(self):
        seen = []

        def read_tags(path):
            seen.append(path)
            return TAGS
        state = {**PLAYING, "song_path": "$SYNCHRONIZED_LIGHTS_HOME/music/carol.mp3",
                 "song_started_at": str(time.time() - 5)}
        res = public.now_playing_snapshot(lambda: state, "/home/pi/lsp", read_tags=read_tags)
        assert seen == ["/home/pi/lsp/music/carol.mp3"]
        assert res.artist == "EvilWezil"
        assert res.stats is None  # stats off by default

    def test_snapshot_adds_stats_only_when_enabled(self):
        state = {**PLAYING, "song_started_at": str(time.time() - 5)}
        stats = public.PublicSongStats(plays=3, completions=2, skips=1)
        on = public.now_playing_snapshot(lambda: state, read_tags=None, with_stats=True,
                                         stats=lambda s: stats)
        off = public.now_playing_snapshot(lambda: state, read_tags=None, with_stats=False,
                                          stats=lambda s: stats)
        assert on.stats == stats
        assert off.stats is None

    def test_off_and_ambient_have_no_details(self):
        calls = []
        for state in ({"now_playing": "0"}, {**PLAYING, "song_title": "Ambient",
                                             "song_started_at": str(time.time())}):
            res = public.now_playing_snapshot(lambda: state, read_tags=calls.append,
                                              with_stats=True, stats=calls.append)
            assert res.artist is None and res.stats is None and res.pattern is None
        assert calls == []

    def test_broken_tag_reader_still_shows_the_song(self):
        def boom(path):
            raise OSError("disk")
        state = {**PLAYING, "song_started_at": str(time.time() - 5)}
        res = public.now_playing_snapshot(lambda: state, read_tags=boom)
        assert res.state == "playing"
        assert res.title == "Carol of the Bells by EvilWezil"


@pytest.mark.unit
class TestSongStats:

    def _play(self, song, reason, ts):
        event_log.record("song_start", source="show", song=song, ts=ts)
        event_log.record("song_end", source="show", song=song, reason=reason,
                         elapsed_s=100.0, duration_s=200.0, ts=ts + 100)

    def test_counts_from_the_event_log(self, events_db):
        song = PLAYING["song_title"]
        self._play(song, "completed", 1000)
        self._play(song, "completed", 2000)
        self._play(song, "skipped", 3000)
        self._play("Other song", "skipped", 4000)
        event_log.record("song_start", source="show", song=song, ts=5000)  # playing now
        stats, last = public.song_stats(song)
        assert stats == public.PublicSongStats(plays=4, completions=2, skips=1)
        assert last == 5000

    def test_none_when_disabled_or_never_played(self, events_db):
        assert public.song_stats("Never played") == (None, None)
        assert public.song_stats("") == (None, None)
        event_log.configure(enabled=False)
        assert public.song_stats(PLAYING["song_title"]) == (None, None)

    def test_computed_once_per_song(self, events_db):
        calls = []

        def compute(song):
            calls.append(song)
            return public.PublicSongStats(plays=len(calls)), NOW
        assert public.current_song_stats(PLAYING, compute).plays == 1
        assert public.current_song_stats(PLAYING, compute).plays == 1
        assert public.current_song_stats({**PLAYING, "song_started_at": "9"}, compute).plays == 2
        assert calls == [PLAYING["song_title"]] * 2

    def test_reads_again_until_this_play_is_logged(self, events_db):
        """state.cfg gets song_started_at just before the show logs song_start."""
        song = PLAYING["song_title"]
        started = float(PLAYING["song_started_at"])
        event_log.record("song_start", source="show", song=song, ts=started - 3600)
        assert public.current_song_stats(PLAYING).plays == 1   # this play not logged yet
        event_log.record("song_start", source="show", song=song, ts=started + 0.01)
        assert public.current_song_stats(PLAYING).plays == 2   # read again, now kept
        event_log.record("song_end", source="show", song=song, reason="skipped", ts=started + 9)
        assert public.current_song_stats(PLAYING).skips == 0   # memoized for this play

    def test_first_ever_play_not_logged_yet_is_not_kept(self, events_db):
        assert public.current_song_stats(PLAYING) is None
        event_log.record("song_start", source="show", song=PLAYING["song_title"],
                         ts=float(PLAYING["song_started_at"]))
        assert public.current_song_stats(PLAYING).plays == 1

    def test_errors_give_none(self):
        def boom(song):
            raise RuntimeError("db locked")
        assert public.current_song_stats(PLAYING, boom) is None


def make_client(state, with_stats=True, entries=None, home_dir="/home/pi/lightshowpi"):
    app = FastAPI()
    app.include_router(public.router)
    app.dependency_overrides[public.get_state_reader] = lambda: (lambda: dict(state))
    app.dependency_overrides[public.get_home_dir] = lambda: home_dir
    app.dependency_overrides[public.get_song_stats_enabled] = lambda: with_stats
    app.dependency_overrides[public.get_playlist_reader] = (
        lambda: (lambda: [list(e) for e in entries]) if entries is not None else None)
    return TestClient(app)


@pytest.mark.unit
class TestEndpoints:

    def test_now_playing_with_details_and_stats(self, events_db, monkeypatch):
        monkeypatch.setattr(song_tags, "read_tags", lambda path: TAGS)
        song = PLAYING["song_title"]
        event_log.record("song_end", source="show", song=song, reason="skipped", ts=1)
        state = {**PLAYING, "song_started_at": str(time.time() - 5)}
        body = make_client(state).get("/api/public/now-playing").json()
        assert body["title"] == "Carol of the Bells"
        assert body["artist"] == "EvilWezil"
        assert body["year"] == "2013"
        assert body["pattern"] == "Onset bands"
        assert body["stats"] == {"plays": 1, "completions": 0, "skips": 1}
        assert "/home" not in str(body) and "secret" not in str(body)

    def test_stats_can_be_turned_off(self, events_db):
        event_log.record("song_end", source="show", song=PLAYING["song_title"],
                         reason="skipped", ts=1)
        state = {**PLAYING, "song_started_at": str(time.time() - 5)}
        body = make_client(state, with_stats=False).get("/api/public/now-playing").json()
        assert body["stats"] is None

    def test_playlist_details(self, tmp_path):
        song = tmp_path / "carol.mp3"
        song.write_bytes(b"x")
        entries = [["Carol", str(song), set()], ["Deck", "/m/deck.mp3", set()]]

        def read_tags(path):
            return TAGS if path == str(song) else song_tags.EMPTY
        res = public.build_playlist(entries, read_tags=read_tags)
        assert res.songs == ["Carol", "Deck"]
        assert [d.model_dump() for d in res.details] == [
            {"artist": "EvilWezil", "year": "2013"}, {"artist": None, "year": None}]

    def test_playlist_tag_errors_leave_blanks(self):
        def boom(path):
            raise OSError("gone")
        res = public.build_playlist([["Carol", "/m/c.mp3", set()]], read_tags=boom)
        assert res.details[0].artist is None


@pytest.mark.unit
def test_setting_defaults_on_and_reads_false(tmp_path, monkeypatch):
    from types import SimpleNamespace
    assert public.config_song_stats(SimpleNamespace()) is True
    assert public.config_song_stats(SimpleNamespace(public_song_stats=False)) is False
