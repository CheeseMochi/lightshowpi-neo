"""Tests for the pattern lab (research/lab) and data-driven patterns/mixes (research/lspcompose).

Everything runs without librosa or a Pi: the Pi is a small FastAPI app that
follows the same contract as the real songs API, and the lab's urllib client
is routed to it in-process.
"""

import io
import json
import os
import stat
import sys
from types import SimpleNamespace
from urllib import error as urlerror

import numpy as np
import pytest
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.testclient import TestClient

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (os.path.join(REPO, "research"), REPO):
    if p not in sys.path:
        sys.path.insert(0, p)

from lspcompose import layout as layout_mod  # noqa: E402
from lspcompose import metrics, strategies, timing, variants  # noqa: E402
from research.lab import pi as pi_mod  # noqa: E402
from research.lab import server, store  # noqa: E402
from research.lab.cli import main as lab_main  # noqa: E402

pytestmark = pytest.mark.unit
SR = 44100


# ---------------------------------------------------------------------------
# Patterns as data
# ---------------------------------------------------------------------------

class TestLibrary:

    def test_builtins_are_the_known_variants(self):
        lib = variants.load_library(variants.BUILTIN_FILE, None)
        assert list(lib.patterns)[:3] == ["fft", "fft_safe", "onset_bands"]
        assert "test_alternate" in lib.patterns and lib.mixes == {}
        assert [v.name for v in variants.VARIANTS] == list(lib.patterns)

    def test_local_file_overrides_and_adds(self, tmp_path):
        local = tmp_path / "local.toml"
        local.write_text('[patterns.onset_dynamic]\nbase = "onset_bands"\ndynamic = true\ndrop_db = 4.0\n'
                         '[patterns.my_hits]\ntitle = "Mine"\nbase = "onset_bands"\nhold_beats = 0.75\n')
        lib = variants.load_library(variants.BUILTIN_FILE, str(local))
        assert lib.patterns["onset_dynamic"]["drop_db"] == 4.0
        assert lib.sources["onset_dynamic"] == "local" and lib.sources["fft"] == "builtin"
        assert lib.patterns["my_hits"]["hold_beats"] == 0.75

    @pytest.mark.parametrize("body, message", [
        ('[patterns.x]\nbase = "onset_bands"\nholdbeats = 1\n', "no option 'holdbeats'"),
        ('[patterns.x]\nbase = "nope"\n', "unknown base"),
        ('[patterns.x]\ntitle = "no base"\n', "needs base"),
        ('[patterns.x]\nbase = "onset_bands"\ndynamic = "yes"\n', "wrong type"),
        ('[patterns.x]\nbase = "beat_chase"\nevery_beats = 1.5\n', "wrong type"),
        ('[patterns."bad name"]\nbase = "fft"\n', "pattern name"),
        ('[mixes.m]\ngroups = [ {channels = "1-3", pattern = "nope"} ]\n', "unknown pattern"),
        ('[mixes.m]\ngroups = [ {channels = "1-3", pattern = "fft", relays = "ssd"} ]\n', "relays"),
        ('[mixes.fft]\ngroups = [ {channels = "1", pattern = "fft"} ]\n', "both a pattern and a mix"),
        ('[stuff]\na = 1\n', "unknown tables"),
    ])
    def test_bad_definitions_are_rejected_clearly(self, tmp_path, body, message):
        local = tmp_path / "local.toml"
        local.write_text(body)
        with pytest.raises(variants.PatternError, match=message):
            variants.load_library(variants.BUILTIN_FILE, str(local))

    def test_option_defaults_for_the_gui(self):
        d = variants.option_defaults("onset_bands")
        assert d["drop_db"] is None and d["hold_beats"] == 0.5
        assert variants.option_defaults("fft") == {}
        assert "every_beats" in variants.option_types("beat_chase")

    def test_parse_channels(self):
        assert variants.parse_channels("1-3,5", 7) == [0, 1, 2, 4]
        with pytest.raises(variants.PatternError):
            variants.parse_channels("6-8", 7)


# ---------------------------------------------------------------------------
# Mixes: a pattern per light source / channel group
# ---------------------------------------------------------------------------

def _ctx(width=7, seconds=10, min_on=0.2, min_off=0.15):
    rows = timing.row_count(SR * seconds, SR)
    flicker = (np.arange(rows)[:, None] % 2).astype(float) * np.ones((1, width))   # 40 Hz
    return variants.Context(feats=SimpleNamespace(), rows=rows, sample_rate=SR,
                            layout=strategies.Layout(width), timing_ms=25, min_on=min_on,
                            min_off=min_off, fft_levels=flicker)


class TestMixes:

    def lib(self, tmp_path, groups):
        local = tmp_path / "local.toml"
        local.write_text(store.dump_local({}, {"m": {"title": "M", "groups": groups}}))
        return variants.load_library(variants.BUILTIN_FILE, str(local))

    def test_each_group_renders_only_its_own_columns(self, tmp_path):
        lib = self.lib(tmp_path, [{"channels": "1-3", "pattern": "test_alternate"},
                                  {"channels": "4-5", "pattern": "fft", "label": "Nanoleaf"}])
        v = variants.mix_variant("m", lib.mixes["m"], lib.patterns)
        ctx = _ctx()
        out = v.build(ctx)
        alt = strategies.alternate(ctx.rows, SR, strategies.Layout(7, dead=[3, 4, 5, 6]))
        assert np.array_equal(out[:, :3], alt[:, :3])
        assert np.array_equal(out[:, 3:5], ctx.fft_levels[:, 3:5])
        assert out[:, 5:].max() == 0                                  # unnamed columns dark
        assert ctx.labels["m"][3] == "Nanoleaf" and ctx.labels["m"][6] == "(unused)"
        assert v.spec["mix"][1]["pattern"] == "fft"

    def test_relay_limits_hold_per_group(self, tmp_path):
        lib = self.lib(tmp_path, [{"channels": "1-3", "pattern": "fft_safe", "relays": "mechanical"},
                                  {"channels": "4-7", "pattern": "fft_safe", "relays": "ssr"}])
        ctx = _ctx()
        ctx.fft_levels = np.random.default_rng(0).random((ctx.rows, 7))
        out = variants.mix_variant("m", lib.mixes["m"], lib.patterns).build(ctx)
        for cols, preset in ((slice(0, 3), "mechanical"), (slice(3, 7), "ssr")):
            on, off = strategies.RELAY_PRESETS[preset]
            sub = np.zeros_like(out)
            sub[:, cols] = out[:, cols]
            m = metrics.summarize(sub, SR, np.array([]), np.array([0, 10.0]), np.array([0.0, 0.0]),
                                  min_on_s=on, min_off_s=off)
            assert m["short_on_violations"] == m["short_off_violations"] == 0, preset
        # mechanical limits make the first group switch less often than the ssr group
        toggles = [np.abs(np.diff(out[:, c])).sum() for c in range(7)]
        assert max(toggles[:3]) < min(toggles[3:])

    def test_mix_csv_passes_the_pi_check(self, tmp_path):
        import sequence_select
        from lspcompose import csvio
        lib = self.lib(tmp_path, [{"channels": "1-7", "pattern": "test_alternate"},
                                  {"channels": "8-13", "pattern": "fft"}])
        ctx = _ctx(width=13)
        out = variants.mix_variant("m", lib.mixes["m"], lib.patterns).build(ctx)
        path = str(tmp_path / "m.csv")
        csvio.write_csv(out, path)
        assert sequence_select.check_csv(path, 13, full=True) is None


# ---------------------------------------------------------------------------
# Pi layout
# ---------------------------------------------------------------------------

LAYOUT = {"gpio_len": 13, "physical": [0, 7], "led_extend": None, "virtual": [7, 13],
          "outputs": [{"name": "nanoleaf", "type": "nanoleaf", "channels": [7, 8, 9, 10, 11, 12]}]}


class TestLayout:

    def test_the_pis_real_layout_shape(self):
        """As /api/layout returns it on piserver-neo (output-channels branch)."""
        L = layout_mod.from_api({
            "gpio_len": 13, "physical": [0, 7], "led_extend": None, "virtual": [7, 13],
            "virtual_sources": [0, 1, 2, 3, 4, 5],
            "outputs": [{"name": "led:led2.cfg", "type": "led", "channels": [0, 1, 2, 3, 4, 5, 6]},
                        {"name": "nanoleaf", "type": "nanoleaf", "channels": [7, 8, 9, 10, 11, 12]}],
            "channel_labels": {"1": "Snowflake"}, "warnings": []})
        assert [(g.label, g.channels) for g in L.groups] == [
            ("GPIO relays + led:led2.cfg", list(range(7))), ("nanoleaf", list(range(7, 13)))]
        assert L.mirror_source(12) == 5

    def test_groups_from_the_api(self):
        L = layout_mod.from_api(LAYOUT)
        assert L.gpio_len == 13 and L.base_count == 7 and L.virtual == list(range(7, 13))
        assert [(g.label, g.kind) for g in L.groups] == [("GPIO relays", "relays"), ("nanoleaf", "output")]
        assert L.mirror_source(9) == 2                    # default: i mod base

    def test_explicit_mirror_source_and_unclaimed_virtual(self):
        L = layout_mod.from_api({"gpio_len": 9, "physical": [0, 7], "virtual": [7, 9],
                                 "virtual_channel_source": [6, 0]})
        assert L.mirror_source(7) == 6 and L.mirror_source(8) == 0
        assert L.groups[-1].label == "Virtual channels"

    def test_simple(self):
        L = layout_mod.PiLayout.simple(7)
        assert L.base_count == 7 and L.virtual == [] and L.groups[0].channels == list(range(7))


# ---------------------------------------------------------------------------
# Ratings and the local patterns file
# ---------------------------------------------------------------------------

class TestStore:

    def test_latest_rating_wins_and_zero_clears(self, tmp_path):
        path = str(tmp_path / "r.jsonl")
        store.rate("song", "a", 3, path=path)
        store.rate("song", "a", 5, "love it", path=path)
        store.rate("song", "b", 4, path=path)
        store.rate("song", "b", 0, path=path)
        r = store.ratings(path=path)
        assert r == {"song": {"a": r["song"]["a"]}} and r["song"]["a"]["stars"] == 5
        with pytest.raises(ValueError):
            store.rate("song", "a", 6, path=path)

    def test_dump_local_round_trips(self, tmp_path):
        path = str(tmp_path / "local.toml")
        pats = {"mine": {"title": 'He said "hi"', "base": "onset_bands", "dynamic": True,
                         "drop_db": 8.5, "max_per_beat": 3, "scheduler": "smart"}}
        mixes = {"mx": {"title": "Mix", "groups": [{"channels": "1-7", "pattern": "mine"},
                                                   {"channels": "8-13", "pattern": "fft", "relays": "ssr"}]}}
        store.save_local(None, pats, mixes, path)
        lib = variants.load_library(variants.BUILTIN_FILE, path)
        assert lib.patterns["mine"]["drop_db"] == 8.5 and lib.patterns["mine"]["title"] == 'He said "hi"'
        assert lib.mixes["mx"]["groups"][1]["relays"] == "ssr"

    def test_save_local_refuses_invalid_and_keeps_the_old_file(self, tmp_path):
        path = str(tmp_path / "local.toml")
        store.save_local(None, {"ok": {"base": "fft"}}, {}, path)
        with pytest.raises(variants.PatternError):
            store.save_local(None, {"bad": {"base": "onset_bands", "nope": 1}}, {}, path)
        assert variants.load_library(variants.BUILTIN_FILE, path).patterns["ok"]["base"] == "fft"
        assert not os.path.exists(path + ".tmp")


# ---------------------------------------------------------------------------
# A fake Pi with the real songs API contract
# ---------------------------------------------------------------------------

def fake_pi():
    app = FastAPI()
    app.state.variants = {0: {}, 1: {}}
    app.state.calls = []
    songs = [{"index": 0, "name": "Wizards In Winter", "path": "music/WizardsInWinter.mp3"},
             {"index": 1, "name": "Linus and Lucy", "path": "music/LinusLucy.mp3"}]

    def auth(authorization):
        if authorization != "Bearer tok":
            raise HTTPException(401, "Could not validate credentials")

    @app.post("/api/auth/login")
    def login(body: dict):
        if body.get("password") != "pw":
            raise HTTPException(401, "Incorrect username or password")
        return {"access_token": "tok", "token_type": "bearer"}

    @app.get("/api/songs")
    def list_songs(authorization: str = Header(None)):
        auth(authorization)
        return {"songs": [{**s, "sequence": None, "variants": sorted(app.state.variants[s["index"]])}
                          for s in songs]}

    @app.get("/api/layout")
    def layout(authorization: str = Header(None)):
        auth(authorization)
        return LAYOUT

    @app.put("/api/songs/{index}/variants/{name}")
    async def upload(index: int, name: str, file: UploadFile = File(...), replace: bool = False,
                     authorization: str = Header(None)):
        auth(authorization)
        if name in app.state.variants[index] and not replace:
            raise HTTPException(409, f"variant {name} exists")
        data = await file.read()
        app.state.variants[index][name] = data
        return {"name": name, "size": len(data)}

    @app.post("/api/songs/{index}/play")
    def play(index: int, body: dict = None, authorization: str = Header(None)):
        auth(authorization)
        app.state.calls.append(("play", index, (body or {}).get("variant")))
        return {"success": True}

    @app.put("/api/songs/{index}/settings")
    def settings(index: int, body: dict, authorization: str = Header(None)):
        auth(authorization)
        app.state.calls.append(("settings", index, body))
        return body

    return app


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def opener_for(app):
    tc = TestClient(app)

    def opener(req, timeout=None):
        path = req.full_url.split("://", 1)[1].split("/", 1)[1]
        r = tc.request(req.get_method(), "/" + path, content=req.data, headers=dict(req.header_items()))
        if r.status_code >= 400:
            raise urlerror.HTTPError(req.full_url, r.status_code, "err", {}, io.BytesIO(r.content))
        return _Resp(r.content)
    return opener


@pytest.fixture
def pi(tmp_path):
    app = fake_pi()
    client = pi_mod.PiClient("http://pi.test:8000", token_file=str(tmp_path / "tok" / "t.json"),
                             opener=opener_for(app))
    return app, client


def make_bundle(out_dir, song="WizardsInWinter", names=("onset_dynamic", "fft")):
    base = os.path.join(out_dir, song)
    for n in names:
        os.makedirs(os.path.join(base, "full", n), exist_ok=True)
        with open(os.path.join(base, "full", n, "full.csv"), "w") as f:
            f.write("0,255,0,0,0,0,0\n" * 4)
    with open(os.path.join(base, "manifest.json"), "w") as f:
        json.dump({"song": song + ".mp3", "channels": 7, "variants": [
            {"name": n, "title": n.title(), "description": ""} for n in names]}, f)


class TestPiClient:

    def test_login_caches_a_private_token(self, pi):
        app, client = pi
        with pytest.raises(pi_mod.NeedLogin):
            client.songs()
        client.login("admin", "pw")
        assert client.token == "tok"
        assert stat.S_IMODE(os.stat(client.token_file).st_mode) == 0o600

    def test_rejected_token_is_dropped(self, pi):
        app, client = pi
        client._save_token("stale")
        with pytest.raises(pi_mod.NeedLogin):
            client.songs()
        assert client.token is None

    def test_song_matching_by_audio_stem(self, pi):
        app, client = pi
        client.login("admin", "pw")
        assert [s["index"] for s in client.find_song("WizardsInWinter.mp3")] == [0]
        assert [s["index"] for s in client.find_song("linus and lucy")] == [1]
        assert client.find_song("Silent Night") == []

    def test_push_uploads_every_variant_with_pi_names(self, pi, tmp_path):
        app, client = pi
        client.login("admin", "pw")
        out = str(tmp_path / "out")
        make_bundle(out)
        result = store.push(client, "WizardsInWinter", out_dir=out)
        assert result["song"]["index"] == 0
        assert sorted(app.state.variants[0]) == ["onset_dynamic", "research_fft"]   # reserved name
        assert app.state.variants[0]["onset_dynamic"].startswith(b"0,255,0")
        again = store.push(client, "WizardsInWinter", ["onset_dynamic"], out_dir=out)
        assert again["results"][0]["status"] == 409
        assert store.push(client, "WizardsInWinter", ["onset_dynamic"], replace=True,
                          out_dir=out)["results"][0]["ok"]

    def test_push_needs_a_match(self, pi, tmp_path):
        app, client = pi
        client.login("admin", "pw")
        out = str(tmp_path / "out")
        make_bundle(out, song="SilentNight")
        with pytest.raises(pi_mod.PiError, match="--as"):
            store.push(client, "SilentNight", out_dir=out)
        assert store.push(client, "SilentNight", as_song="Linus and Lucy", out_dir=out)["song"]["index"] == 1

    def test_play_once_and_set(self, pi):
        app, client = pi
        client.login("admin", "pw")
        client.play(1, "onset_dynamic")
        client.play(1)
        client.set_sequence(1, "rotate:a,b")
        assert app.state.calls == [("play", 1, "onset_dynamic"), ("play", 1, None),
                                   ("settings", 1, {"sequence": "rotate:a,b"})]


# ---------------------------------------------------------------------------
# The GUI's API (research/lab/server.py)
# ---------------------------------------------------------------------------

@pytest.fixture
def lab(tmp_path):
    app_pi = fake_pi()
    music = tmp_path / "music"
    (music / "sub").mkdir(parents=True)
    (music / "WizardsInWinter.mp3").write_bytes(b"x")
    (music / "sub" / "LinusLucy.m4a").write_bytes(b"x")
    (music / "sequences").mkdir()
    (music / "sequences" / "skip.mp3").write_bytes(b"x")
    out = str(tmp_path / "out")
    make_bundle(out)
    runs = []

    def runner(argv):
        runs.append(argv)
        print("built")
        return 0

    app = server.create_app(str(music), "http://pi.test:8000", out_dir=out,
                            local_patterns=str(tmp_path / "local.toml"),
                            ratings_file=str(tmp_path / "r.jsonl"),
                            layout_file=str(tmp_path / "layout.json"),
                            token_file=str(tmp_path / "tok.json"),
                            build_runner=runner, pi_opener=opener_for(app_pi),
                            settings_file=str(tmp_path / "lab.local.json"))
    return TestClient(app), app_pi, runs, tmp_path


class TestLabServer:

    def test_only_localhost_and_json_writes(self, lab):
        c, *_ = lab
        assert c.get("/api/state", headers={"host": "evil.example"}).status_code == 403
        assert c.post("/api/rate", content="stars=5",
                      headers={"content-type": "application/x-www-form-urlencoded"}).status_code == 415

    def test_state(self, lab):
        c, *_ = lab
        s = c.get("/api/state").json()
        assert s["songs"] == [os.path.join("sub", "LinusLucy.m4a"), "WizardsInWinter.mp3"]  # no sequences/
        assert "WizardsInWinter" in s["bundles"]
        assert any(p["name"] == "onset_dynamic" for p in s["patterns"])
        assert s["bases"]["onset_bands"]["defaults"]["hold_beats"] == 0.5
        assert s["pi"] == {"url": "http://pi.test:8000", "logged_in": False}

    def test_index_and_bundle_files(self, lab):
        c, *_ = lab
        assert "Pattern lab" in c.get("/").text
        assert c.get("/bundles/WizardsInWinter/manifest.json").json()["channels"] == 7

    def test_save_and_delete_a_pattern_and_a_mix(self, lab):
        c, _pi, _runs, tmp = lab
        r = c.put("/api/patterns/my_hits", json={
            "base": "onset_bands", "title": "Mine", "options": {"hold_beats": 0.75, "dynamic": True}})
        assert r.status_code == 200
        bad = c.put("/api/patterns/x", json={"base": "onset_bands", "options": {"nope": 1}})
        assert bad.status_code == 422 and "no option" in bad.json()["detail"]
        r = c.put("/api/mixes/mx", json={"title": "Mix", "groups": [
            {"channels": "1-7", "pattern": "my_hits"}, {"channels": "8-13", "pattern": "test_alternate",
                                                        "relays": "ssr", "label": ""}]})
        assert r.status_code == 200
        s = c.get("/api/state").json()
        assert [m["name"] for m in s["mixes"]] == ["mx"]
        assert "label" not in s["mixes"][0]["groups"][1]               # blanks dropped
        assert c.put("/api/mixes/fft", json={"groups": [{"channels": "1", "pattern": "fft"}]}).status_code == 409
        assert c.delete("/api/patterns/fft").status_code == 404          # built-ins stay
        assert c.delete("/api/mixes/mx").status_code == 200
        assert c.delete("/api/patterns/my_hits").status_code == 200
        assert c.get("/api/state").json()["mixes"] == []

    def test_build_runs_the_bundler_with_the_right_args(self, lab):
        c, _pi, runs, tmp = lab
        r = c.post("/api/build", json={"songs": ["WizardsInWinter.mp3"], "variants": ["onset_dynamic"],
                                       "relays": "ssr", "clip_start": 62})
        job = r.json()["job"]
        import time
        for _ in range(100):
            j = c.get(f"/api/jobs/{job}").json()
            if j["state"] != "running":
                break
            time.sleep(0.02)
        assert j["state"] == "done" and "built" in j["log"]
        argv = runs[0]
        assert argv[0].endswith("WizardsInWinter.mp3")
        assert argv[argv.index("--channels") + 1] == "7"                  # no saved layout yet
        assert argv[argv.index("--variants") + 1] == "onset_dynamic"
        assert argv[argv.index("--clip-start") + 1] == "62.0"
        assert c.post("/api/build", json={"songs": ["nope.mp3"]}).status_code == 404

    def test_rate(self, lab):
        c, *_ = lab
        assert c.post("/api/rate", json={"song": "WizardsInWinter", "variant": "fft", "stars": 4}).status_code == 200
        assert c.get("/api/state").json()["ratings"]["WizardsInWinter"]["fft"]["stars"] == 4
        assert c.post("/api/rate", json={"song": "s", "variant": "v", "stars": 9}).status_code == 422

    def test_pi_flow(self, lab):
        c, app_pi, _runs, tmp = lab
        assert c.get("/api/pi/songs").status_code == 401
        assert c.post("/api/pi/login", json={"password": "nope"}).status_code == 401
        assert c.post("/api/pi/login", json={"password": "pw"}).json()["logged_in"]
        assert [s["name"] for s in c.get("/api/pi/songs").json()][0] == "Wizards In Winter"
        saved = c.post("/api/pi/layout", json={}).json()
        assert saved["layout"]["gpio_len"] == 13 and os.path.exists(tmp / "layout.json")
        r = c.post("/api/pi/push", json={"song": "WizardsInWinter", "variants": ["onset_dynamic"]}).json()
        assert r["results"][0]["ok"] and "onset_dynamic" in app_pi.state.variants[0]
        assert c.post("/api/pi/play", json={"song": "WizardsInWinter", "variant": "fft"}).json()["variant"] == "fft"
        assert app_pi.state.calls[-1] == ("play", 0, "research_fft")
        assert c.post("/api/pi/set", json={"song": "WizardsInWinter", "sequence": "rotate:a,b"}).status_code == 200
        # with a saved layout, builds use it instead of a channel count
        c.post("/api/build", json={"songs": ["WizardsInWinter.mp3"]})
        assert "--layout" in _runs[-1]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_patterns_and_ratings(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "RATINGS_FILE", str(tmp_path / "r.jsonl"))
    monkeypatch.setattr(variants, "LOCAL_FILE", str(tmp_path / "none.toml"))
    assert lab_main(["patterns"]) == 0
    out = capsys.readouterr().out
    assert "onset_dynamic" in out and "builtin" in out
    rate_defaults = store.rate.__defaults__
    monkeypatch.setattr(store.rate, "__defaults__", (rate_defaults[0], str(tmp_path / "r.jsonl")))
    monkeypatch.setattr(store.ratings, "__defaults__", (None, str(tmp_path / "r.jsonl")))
    assert lab_main(["rate", "Wiz", "onset_dynamic", "5", "--note", "yes"]) == 0
    assert lab_main(["ratings"]) == 0
    assert "★★★★★ onset_dynamic  yes" in capsys.readouterr().out


def test_cli_reports_pi_errors_without_a_traceback(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("LSP_LAB_TOKEN_FILE", str(tmp_path / "t.json"))
    monkeypatch.setattr(pi_mod, "TOKEN_FILE", str(tmp_path / "t.json"))
    monkeypatch.setattr(pi_mod.PiClient.__init__, "__defaults__",
                        (pi_mod.DEFAULT_URL, str(tmp_path / "t.json"), 30.0, None))
    assert lab_main(["--pi", "http://pi.test:1", "pi", "songs"]) == 2
    assert "not logged in" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Integration: the lab client against the real songs API (once it has variants)
# ---------------------------------------------------------------------------

def _real_songs_router():
    pytest.importorskip("bcrypt", reason="the API's dependencies aren't in this environment")
    from api.routers import songs as songs_router
    if not any("/variants/" in getattr(r, "path", "") for r in songs_router.router.routes):
        pytest.skip("the songs API has no variant upload yet (sequence-upload not merged)")
    return songs_router


def test_lab_client_against_the_real_songs_api(tmp_path):
    songs_router = _real_songs_router()
    from unittest.mock import Mock
    from api.core.auth import get_current_user, require_admin
    from api.services.lightshow_manager import LightshowManager
    from api.services.songs import SongLibrary

    music = tmp_path / "music"
    music.mkdir()
    (music / "WizardsInWinter.mp3").write_bytes(b"")
    (music / ".playlist").write_text("Wizards In Winter\tmusic/WizardsInWinter.mp3\n")
    (tmp_path / "config").mkdir()
    library = SongLibrary(playlist_path=str(music / ".playlist"), home_dir=str(tmp_path),
                          sequences_dir=str(tmp_path / "sequences"), gpio_len=7)
    manager = Mock(spec=LightshowManager)
    manager.is_running.return_value = True
    manager.current_playlist = library.playlist_path
    manager.config = Mock()
    app = FastAPI()
    app.include_router(songs_router.router)
    app.dependency_overrides[songs_router.get_library] = lambda: library
    app.dependency_overrides[songs_router.get_manager] = lambda: manager
    app.dependency_overrides[require_admin] = lambda: {"username": "tester", "id": 1}
    app.dependency_overrides[get_current_user] = lambda: {"username": "tester", "id": 1}

    client = pi_mod.PiClient("http://pi.test:8000", token_file=str(tmp_path / "t.json"),
                             opener=opener_for(app))
    client._save_token("any")                       # auth is overridden above
    out = str(tmp_path / "out")
    make_bundle(out)                                # 7-column CSVs: onset_dynamic, fft

    result = store.push(client, "WizardsInWinter", out_dir=out)
    assert all(r["ok"] for r in result["results"]), result
    on_disk = sorted(os.listdir(tmp_path / "sequences" / "WizardsInWinter"))
    assert on_disk == ["onset_dynamic.csv", "research_fft.csv"]
    again = store.push(client, "WizardsInWinter", ["onset_dynamic"], out_dir=out)
    assert again["results"][0]["status"] == 409
    assert store.push(client, "WizardsInWinter", ["onset_dynamic"], replace=True,
                      out_dir=out)["results"][0]["ok"]

    client.play(0, "onset_dynamic")                 # the one-shot audition
    writes = [c.args for c in manager.config.update_state.call_args_list]
    assert ("sequence_once", "WizardsInWinter|onset_dynamic") in writes
    assert writes[-1] == ("play_now", "1")
    client.set_sequence(0, "rotate:onset_dynamic,research_fft")
    assert client.songs()[0]["sequence"] == "rotate:onset_dynamic,research_fft"

    bad = str(tmp_path / "bad")
    make_bundle(bad)
    with open(os.path.join(bad, "WizardsInWinter", "full", "fft", "full.csv"), "w") as f:
        f.write("0,255\n")                          # wrong column count: the Pi refuses it
    r = store.push(client, "WizardsInWinter", ["fft"], replace=True, out_dir=bad)["results"][0]
    assert not r["ok"] and r["status"] == 422


# ---------------------------------------------------------------------------
# Music folder: songs from anywhere, remembered between runs
# ---------------------------------------------------------------------------

class TestMusicFolder:

    def test_switch_folder_remember_and_build_from_it(self, lab):
        c, _pi, runs, tmp = lab
        mine = tmp / "My Christmas Music"
        (mine / "TSO").mkdir(parents=True)
        (mine / "TSO" / "Wizards In Winter.flac").write_bytes(b"x")
        (mine / "notes.txt").write_text("not audio")
        r = c.post("/api/music-dir", json={"path": str(mine)})
        assert r.status_code == 200 and r.json()["songs"] == 1
        s = c.get("/api/state").json()
        assert s["music_dir"] == str(mine) and s["songs"] == [os.path.join("TSO", "Wizards In Winter.flac")]
        assert s["recent_music_dirs"][0] == str(mine)
        c.post("/api/build", json={"songs": [os.path.join("TSO", "Wizards In Winter.flac")]})
        assert runs[-1][0] == str(mine / "TSO" / "Wizards In Winter.flac")
        settings = json.loads((tmp / "lab.local.json").read_text())
        assert settings["music_dir"] == str(mine)

    def test_missing_folder_and_escaping_paths_are_refused(self, lab):
        c, _pi, _runs, tmp = lab
        assert c.post("/api/music-dir", json={"path": str(tmp / "nope")}).status_code == 404
        (tmp / "secret.mp3").write_bytes(b"x")
        r = c.post("/api/build", json={"songs": ["../secret.mp3"]})
        assert r.status_code == 422 and "outside" in r.json()["detail"]

    def test_recent_folders_most_recent_first_without_duplicates(self, tmp_path):
        path = str(tmp_path / "s.json")
        for d in ("a", "b", "a", "c"):
            store.remember_music_dir(d, path)
        assert store.load_settings(path)["recent_music_dirs"] == ["c", "a", "b"]

    def test_scan_skips_libraries_and_stops_at_the_cap(self, tmp_path):
        (tmp_path / "Music.musiclibrary").mkdir()
        (tmp_path / "Music.musiclibrary" / "x.mp3").write_bytes(b"x")
        for i in range(5):
            (tmp_path / f"song{i}.mp3").write_bytes(b"x")
        songs, truncated = store.scan_music(str(tmp_path), limit=3)
        assert len(songs) == 3 and truncated
        songs, truncated = store.scan_music(str(tmp_path))
        assert len(songs) == 5 and not truncated

    def test_initial_folder_precedence(self, tmp_path, monkeypatch):
        a, b, c = (tmp_path / n for n in "abc")
        for d in (a, b, c):
            d.mkdir()
        settings = str(tmp_path / "s.json")
        store.remember_music_dir(str(c), settings)
        monkeypatch.delenv("LSP_MUSIC_DIR", raising=False)
        assert store.initial_music_dir(None, "music", settings) == str(c)        # last used
        monkeypatch.setenv("LSP_MUSIC_DIR", str(b))
        assert store.initial_music_dir(None, "music", settings) == str(b)        # env
        assert store.initial_music_dir(str(a), "music", settings) == str(a)      # --music
        monkeypatch.setenv("LSP_MUSIC_DIR", str(tmp_path / "gone"))
        assert store.initial_music_dir(None, "music", settings) == str(c)        # bad env skipped
