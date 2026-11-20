"""Serving the built web UI from the API (api/core/frontend.py)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.core import frontend
from api.core.config import APIConfig

INDEX_HTML = "<!doctype html><title>LightShowPi Neo</title>"


@pytest.fixture
def dist(tmp_path):
    """A minimal Vite build: index.html, a hashed asset and a root file."""
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text(INDEX_HTML)
    (d / "assets" / "index-abc123.js").write_text("console.log('hi')")
    (d / "favicon.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("outside the build")
    return d


def make_app(dist_dir):
    """An app with one API route, then the frontend (as api/main.py orders them)."""
    app = FastAPI()

    @app.get("/api/thing")
    async def thing():
        return {"ok": True}

    index = frontend.add_frontend_routes(app, dist_dir)
    return app, index


@pytest.mark.unit
class TestFrontendRoutes:

    def test_no_build_registers_nothing(self, tmp_path):
        app, index = make_app(tmp_path / "missing")
        assert index is None
        client = TestClient(app)
        assert client.get("/show").status_code == 404
        assert client.get("/api/thing").json() == {"ok": True}

    def test_client_routes_get_index_uncached(self, dist):
        client = TestClient(make_app(dist)[0])
        for path in ("/show", "/login", "/some/deep/route", "/index.html"):
            res = client.get(path)
            assert res.status_code == 200, path
            assert res.text == INDEX_HTML
            assert res.headers["cache-control"] == "no-cache"
            assert res.headers["content-type"].startswith("text/html")

    def test_assets_and_root_files_are_served(self, dist):
        client = TestClient(make_app(dist)[0])
        assert client.get("/assets/index-abc123.js").text == "console.log('hi')"
        assert client.get("/favicon.svg").text == "<svg/>"

    def test_missing_asset_is_404_not_index(self, dist):
        client = TestClient(make_app(dist)[0])
        assert client.get("/assets/gone.js").status_code == 404

    def test_api_routes_still_win(self, dist):
        client = TestClient(make_app(dist)[0])
        assert client.get("/api/thing").json() == {"ok": True}

    @pytest.mark.parametrize("path", ["/api", "/api/nope", "/api/public/nope",
                                      "/docs/x", "/redoc/x", "/openapi.json/x"])
    def test_unknown_api_paths_are_json_404(self, dist, path):
        res = TestClient(make_app(dist)[0]).get(path)
        assert res.status_code == 404
        assert res.json() == {"detail": "Not Found"}

    @pytest.mark.parametrize("path", ["/../secret.txt", "/%2e%2e/secret.txt",
                                      "/assets/../../secret.txt"])
    def test_files_outside_the_build_are_never_served(self, dist, path):
        res = TestClient(make_app(dist)[0]).get(path)
        assert "outside the build" not in res.text

    def test_fallback_is_hidden_from_openapi(self, dist):
        app, _ = make_app(dist)
        assert set(app.openapi()["paths"]) == {"/api/thing"}

    def test_only_get(self, dist):
        res = TestClient(make_app(dist)[0]).post("/show")
        assert res.status_code == 405


@pytest.mark.unit
class TestDistDirConfig:

    def test_default_is_repo_web_dist(self):
        assert frontend.resolve_dist_dir("") == frontend.REPO_ROOT / "web" / "dist"

    def test_configured_path_expands_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LSP_TEST_DIST", str(tmp_path))
        assert frontend.resolve_dist_dir("$LSP_TEST_DIST/dist") == (tmp_path / "dist").resolve()

    def test_api_config_reads_web_dist(self):
        assert APIConfig().web_dist == ""


@pytest.mark.unit
class TestMainRoot:

    def test_root_serves_index_when_built(self, monkeypatch, dist):
        from api import main
        monkeypatch.setattr(main, "FRONTEND_INDEX", dist / "index.html")
        res = TestClient(main.app).get("/")
        assert res.text == INDEX_HTML

    def test_root_is_api_info_without_build(self, monkeypatch):
        from api import main
        monkeypatch.setattr(main, "FRONTEND_INDEX", None)
        res = TestClient(main.app).get("/")
        assert res.json()["docs"] == "/docs"
