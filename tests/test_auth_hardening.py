"""Tests for API auth hardening: JWT key, first admin, 401s, login rate limit,
password change, CORS and the route auth audit."""

import logging
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api import main
from api.core import auth
from api.core.auth import AuthManager
from api.core.database import Database
from api.core.rate_limit import RateLimiter, client_ip

GOOD_PASSWORD = "snowflake-2026"


@pytest.fixture
def database(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize_schema()
    return db


@pytest.fixture
def auth_manager(database):
    return AuthManager(database)


@pytest.fixture
def client(monkeypatch, database, auth_manager):
    """main.app without the lifespan, on a temp DB with one user."""
    auth_manager.create_user("admin", GOOD_PASSWORD)
    monkeypatch.setattr(main, "db", database)
    main.login_limiter.reset()
    yield TestClient(main.app)
    main.login_limiter.reset()


def login(client, password=GOOD_PASSWORD, username="admin"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def bearer(client, password=GOOD_PASSWORD):
    token = login(client, password).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# JWT signing key
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestSecretKey:

    @pytest.fixture(autouse=True)
    def restore_key(self, monkeypatch):
        monkeypatch.setattr(auth, "SECRET_KEY", auth.SECRET_KEY)
        monkeypatch.delenv("LSP_DEV", raising=False)

    def test_missing_key_refuses_to_start(self, monkeypatch):
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
            auth.configure_secret_key()

    def test_short_key_refuses_to_start(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "too-short")
        with pytest.raises(RuntimeError):
            auth.configure_secret_key()

    def test_key_from_environment_is_used(self, monkeypatch):
        key = "k" * 64
        monkeypatch.setenv("JWT_SECRET_KEY", key)
        assert auth.configure_secret_key() == key
        token = AuthManager.create_access_token({"sub": "admin"})
        assert AuthManager.decode_token(token)["sub"] == "admin"

    def test_dev_mode_allows_random_key(self, monkeypatch, caplog):
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        monkeypatch.setenv("LSP_DEV", "1")
        with caplog.at_level(logging.WARNING):
            key = auth.configure_secret_key()
        assert len(key) >= auth.MIN_SECRET_LENGTH
        assert "LSP_DEV" in caplog.text

    def test_token_signed_with_other_key_is_rejected(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 40)
        auth.configure_secret_key()
        token = AuthManager.create_access_token({"sub": "admin"})
        monkeypatch.setenv("JWT_SECRET_KEY", "b" * 40)
        auth.configure_secret_key()
        with pytest.raises(Exception) as exc:
            AuthManager.decode_token(token)
        assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# First admin user: no default password
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestFirstAdmin:

    def count_users(self, database):
        with database.get_connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def test_no_admin_password_creates_no_user(self, monkeypatch, database, auth_manager, caplog):
        monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
        with caplog.at_level(logging.ERROR):
            assert auth.ensure_admin_user(auth_manager, database) == "missing"
        assert self.count_users(database) == 0
        assert "ADMIN_PASSWORD" in caplog.text
        assert "admin123" not in caplog.text

    @pytest.mark.parametrize("password", ["admin123", "ADMIN123", "short"])
    def test_unacceptable_password_creates_no_user(self, monkeypatch, database, auth_manager, password):
        monkeypatch.setenv("ADMIN_PASSWORD", password)
        assert auth.ensure_admin_user(auth_manager, database) == "missing"
        assert self.count_users(database) == 0

    def test_good_password_creates_admin_and_is_not_logged(self, monkeypatch, database,
                                                           auth_manager, caplog):
        monkeypatch.setenv("ADMIN_PASSWORD", GOOD_PASSWORD)
        with caplog.at_level(logging.DEBUG):
            assert auth.ensure_admin_user(auth_manager, database) == "created"
        assert auth_manager.authenticate_user("admin", GOOD_PASSWORD)
        assert GOOD_PASSWORD not in caplog.text

    def test_existing_users_are_left_alone(self, monkeypatch, database, auth_manager):
        auth_manager.create_user("admin", GOOD_PASSWORD)
        monkeypatch.setenv("ADMIN_PASSWORD", "different-password")
        assert auth.ensure_admin_user(auth_manager, database) == "exists"
        assert auth_manager.authenticate_user("admin", GOOD_PASSWORD)


# ---------------------------------------------------------------------------
# Unauthenticated requests: 401, and every non-public route needs a token
# ---------------------------------------------------------------------------

PUBLIC_PATHS = {"/", "/api/health", "/api/auth/login"}
# Routes that must be protected; guards against the audit passing vacuously
KNOWN_PROTECTED = {"/api/lightshow/status", "/api/lightshow/start", "/api/schedules",
                   "/api/buttons/status", "/api/test/status", "/api/auth/password",
                   "/api/songs", "/api/songs/{index}/settings"}


def operations():
    """Every (method, path, operation) in the OpenAPI schema.

    The schema is used rather than app.routes because newer FastAPI versions
    wrap included routers in objects that hide their routes. Auth dependencies
    (HTTPBearer via get_current_user/require_admin) show up as `security`.
    """
    main.app.openapi_schema = None  # rebuild: routes may have been added since
    for path, methods in main.app.openapi()["paths"].items():
        for method, op in methods.items():
            yield method.upper(), path, op


@pytest.mark.unit
class TestRouteAudit:

    def test_every_non_public_route_requires_auth(self):
        ops = list(operations())
        assert KNOWN_PROTECTED <= {path for _, path, _ in ops}
        open_routes = [
            f"{method} {path}" for method, path, op in ops
            if path not in PUBLIC_PATHS
            and not path.startswith("/api/public/")
            and not op.get("security")
        ]
        assert open_routes == []

    def test_public_routes_need_no_token(self):
        for method, path, op in operations():
            if path in PUBLIC_PATHS or path.startswith("/api/public/"):
                assert not op.get("security"), f"{method} {path}"

    # Routes whose other dependencies resolve without the lifespan (buttons and
    # test mode return 503 first when their manager isn't set; the audit above
    # covers them).
    @pytest.mark.parametrize("path", ["/api/lightshow/status", "/api/schedules"])
    def test_missing_token_is_401(self, client, path):
        res = client.get(path)
        assert res.status_code == 401
        assert res.headers.get("www-authenticate") == "Bearer"

    def test_garbage_token_is_401(self, client):
        res = client.get("/api/lightshow/status", headers={"Authorization": "Bearer nope"})
        assert res.status_code == 401


# ---------------------------------------------------------------------------
# Login rate limit and IP whitelist
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestLoginRateLimit:

    def test_five_failures_then_429_even_with_right_password(self, client):
        for _ in range(main.LOGIN_FAILURES_PER_MINUTE):
            assert login(client, "wrong-password").status_code == 401
        res = login(client)
        assert res.status_code == 429
        assert int(res.headers["retry-after"]) >= 1

    def test_successful_logins_are_not_limited(self, client):
        for _ in range(main.LOGIN_FAILURES_PER_MINUTE + 3):
            assert login(client).status_code == 200

    def test_ip_whitelist_is_applied(self, client, auth_manager):
        auth_manager.create_user("family", GOOD_PASSWORD, allowed_ips=["192.168.1.50"])
        # TestClient's peer is "testclient", not in the list
        assert login(client, username="family").status_code == 401


@pytest.mark.unit
class TestClientIp:

    def request(self, peer, headers=None):
        return SimpleNamespace(client=SimpleNamespace(host=peer), headers=headers or {})

    def test_cf_header_trusted_from_loopback(self):
        req = self.request("127.0.0.1", {"cf-connecting-ip": "203.0.113.9"})
        assert client_ip(req) == "203.0.113.9"

    def test_cf_header_ignored_from_lan(self):
        req = self.request("192.168.1.20", {"cf-connecting-ip": "203.0.113.9"})
        assert client_ip(req) == "192.168.1.20"

    def test_no_client(self):
        assert client_ip(SimpleNamespace(client=None, headers={})) == "unknown"


@pytest.mark.unit
class TestRateLimiter:

    def test_window_expires(self):
        rl = RateLimiter(max_events=2, window_s=60)
        assert rl.hit("a", now=0) and rl.hit("a", now=1)
        assert not rl.hit("a", now=2)
        assert rl.retry_after("a", now=2) == pytest.approx(58)
        assert rl.hit("a", now=61)

    def test_keys_are_independent(self):
        rl = RateLimiter(max_events=1, window_s=60)
        assert rl.hit("a", now=0)
        assert rl.hit("b", now=0)

    def test_tracked_keys_are_bounded(self):
        rl = RateLimiter(max_events=1, window_s=60, max_keys=3)
        for i in range(10):
            rl.record(f"ip{i}", now=0)
        assert len(rl._events) == 3


# ---------------------------------------------------------------------------
# Password change
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestPasswordChange:

    def change(self, client, headers, current, new):
        return client.post("/api/auth/password", headers=headers,
                           json={"current_password": current, "new_password": new})

    def test_change_password(self, client):
        headers = bearer(client)
        assert self.change(client, headers, GOOD_PASSWORD, "candy-cane-99").status_code == 204
        assert login(client).status_code == 401
        assert login(client, "candy-cane-99").status_code == 200

    def test_requires_token(self, client):
        assert self.change(client, {}, GOOD_PASSWORD, "candy-cane-99").status_code == 401

    def test_wrong_current_password(self, client):
        res = self.change(client, bearer(client), "nope-nope", "candy-cane-99")
        assert res.status_code == 400

    @pytest.mark.parametrize("new", ["admin123", GOOD_PASSWORD])
    def test_rejects_bad_new_password(self, client, new):
        res = self.change(client, bearer(client), GOOD_PASSWORD, new)
        assert res.status_code == 400

    def test_too_short_is_rejected_by_schema(self, client):
        res = self.change(client, bearer(client), GOOD_PASSWORD, "short")
        assert res.status_code == 422

    def test_wrong_current_password_counts_toward_limit(self, client):
        headers = bearer(client)
        for _ in range(main.LOGIN_FAILURES_PER_MINUTE):
            assert self.change(client, headers, "nope-nope", "candy-cane-99").status_code == 400
        assert self.change(client, headers, GOOD_PASSWORD, "candy-cane-99").status_code == 429


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCors:

    def test_default_allows_no_cross_origin(self, monkeypatch):
        monkeypatch.delenv("LSP_DEV", raising=False)
        assert main.load_cors_origins() == []

    def test_dev_mode_adds_vite_origins(self, monkeypatch):
        monkeypatch.setenv("LSP_DEV", "1")
        assert "http://localhost:3000" in main.load_cors_origins()

    def test_config_list_is_parsed(self, monkeypatch):
        monkeypatch.delenv("LSP_DEV", raising=False)
        monkeypatch.setattr(main, "APIConfig", lambda: SimpleNamespace(
            cors_origins=["https://lights.example.com"]))
        assert main.load_cors_origins() == ["https://lights.example.com"]

    def test_app_sends_no_wildcard_cors(self, client):
        res = client.get("/api/health", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in res.headers


# ---------------------------------------------------------------------------
# python -m api.manage set-password
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestManageCli:

    def run(self, monkeypatch, tmp_path, password, *args):
        import io
        from api import manage
        monkeypatch.setattr("sys.stdin", io.StringIO(password + "\n"))
        db_path = str(tmp_path / "cli.db")
        rc = manage.main(["set-password", "admin", "--stdin", "--db", db_path, *args])
        return rc, Database(db_path)

    def test_creates_then_updates(self, monkeypatch, tmp_path, capsys):
        rc, db = self.run(monkeypatch, tmp_path, GOOD_PASSWORD)
        assert rc == 0 and "created" in capsys.readouterr().out
        rc, db = self.run(monkeypatch, tmp_path, "candy-cane-99")
        assert rc == 0 and "updated" in capsys.readouterr().out
        assert AuthManager(db).authenticate_user("admin", "candy-cane-99")
        assert not AuthManager(db).authenticate_user("admin", GOOD_PASSWORD)

    def test_rejects_default_password(self, monkeypatch, tmp_path, capsys):
        rc, _ = self.run(monkeypatch, tmp_path, "admin123")
        assert rc == 1
        assert "published default" in capsys.readouterr().err


@pytest.mark.unit
def test_api_startup_refuses_without_jwt_key(monkeypatch):
    """The real lifespan stops before touching config or the DB."""
    monkeypatch.setattr(auth, "SECRET_KEY", auth.SECRET_KEY)
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("LSP_DEV", raising=False)
    monkeypatch.setattr(main, "get_api_config",
                        lambda: pytest.fail("startup continued without a JWT key"))
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        with TestClient(main.app):
            pass
