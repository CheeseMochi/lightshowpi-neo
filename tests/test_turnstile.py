"""Tests for Cloudflare Turnstile on the admin login: tunnel detection,
/api/auth/config, siteverify and the login flow. No real network."""

import io
import logging
import socket
import urllib.error
import urllib.parse
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api import main
from api.core import turnstile
from api.core.auth import AuthManager
from api.core.database import Database
from api.core.rate_limit import is_tunnel_request

GOOD_PASSWORD = "snowflake-2026"
SITE_KEY = "1x00000000000000000000AA"
SECRET = "0x-very-secret-turnstile-key"
VISITOR_IP = "203.0.113.9"
TUNNEL_HEADERS = {"CF-Connecting-IP": VISITOR_IP}


def config(site=SITE_KEY, secret=SECRET):
    return SimpleNamespace(turnstile_site_key=site, turnstile_secret_key=secret)


@pytest.fixture
def database(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize_schema()
    AuthManager(db).create_user("admin", GOOD_PASSWORD)
    return db


def make_client(monkeypatch, database, cfg, peer):
    monkeypatch.setattr(main, "db", database)
    monkeypatch.setattr(main, "api_config", cfg)
    main.login_limiter.reset()
    return TestClient(main.app, client=(peer, 50000))


@pytest.fixture
def tunnel(monkeypatch, database):
    """Loopback peer (cloudflared) with Turnstile configured."""
    yield make_client(monkeypatch, database, config(), "127.0.0.1")
    main.login_limiter.reset()


@pytest.fixture
def lan(monkeypatch, database):
    """LAN peer with Turnstile configured."""
    yield make_client(monkeypatch, database, config(), "192.168.1.20")
    main.login_limiter.reset()


def login(client, token=None, password=GOOD_PASSWORD, headers=TUNNEL_HEADERS):
    body = {"username": "admin", "password": password}
    if token is not None:
        body["turnstile_token"] = token
    return client.post("/api/auth/login", json=body, headers=headers)


# ---------------------------------------------------------------------------
# Tunnel detection and config
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestIsTunnelRequest:

    def request(self, peer, headers=None):
        return SimpleNamespace(client=SimpleNamespace(host=peer) if peer else None,
                               headers=headers or {})

    @pytest.mark.parametrize("peer", ["127.0.0.1", "::1"])
    def test_loopback_with_cf_header(self, peer):
        assert is_tunnel_request(self.request(peer, {"cf-connecting-ip": VISITOR_IP}))

    def test_loopback_without_header_is_local(self):
        # The button manager logs in to http://localhost with no CF header
        assert not is_tunnel_request(self.request("127.0.0.1"))

    def test_blank_header_is_local(self):
        assert not is_tunnel_request(self.request("127.0.0.1", {"cf-connecting-ip": "  "}))

    def test_lan_peer_with_spoofed_header(self):
        assert not is_tunnel_request(self.request("192.168.1.20",
                                                  {"cf-connecting-ip": VISITOR_IP}))

    def test_no_client(self):
        assert not is_tunnel_request(self.request(None, {"cf-connecting-ip": VISITOR_IP}))


@pytest.mark.unit
class TestKeys:

    def test_both_set(self):
        assert turnstile.keys(config()) == (SITE_KEY, SECRET)

    @pytest.mark.parametrize("cfg", [None, config("", ""), config(SITE_KEY, ""),
                                     config("", SECRET), config("  ", "  ")])
    def test_off(self, cfg):
        assert turnstile.keys(cfg) == ("", "")

    def test_api_config_defaults_off(self):
        from api.core.config import APIConfig
        cfg = APIConfig()
        assert cfg.turnstile_site_key == ""
        assert cfg.turnstile_secret_key == ""


# ---------------------------------------------------------------------------
# siteverify
# ---------------------------------------------------------------------------

class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.mark.unit
class TestVerify:

    def test_success_sends_form_fields(self):
        with patch("urllib.request.urlopen",
                   return_value=FakeResp(b'{"success": true}')) as urlopen:
            assert turnstile.verify(SECRET, "tok", VISITOR_IP)
        req = urlopen.call_args.args[0]
        assert req.full_url == turnstile.SITEVERIFY_URL
        assert req.get_method() == "POST"
        fields = urllib.parse.parse_qs(req.data.decode())
        assert fields == {"secret": [SECRET], "response": ["tok"], "remoteip": [VISITOR_IP]}
        assert urlopen.call_args.kwargs["timeout"] == turnstile.TIMEOUT_SECONDS

    def test_rejected(self, caplog):
        body = b'{"success": false, "error-codes": ["invalid-input-response"]}'
        with caplog.at_level(logging.DEBUG), \
                patch("urllib.request.urlopen", return_value=FakeResp(body)):
            assert not turnstile.verify(SECRET, "tok", VISITOR_IP)
        assert SECRET not in caplog.text

    @pytest.mark.parametrize("error", [urllib.error.URLError("down"), socket.timeout("slow"),
                                       ConnectionResetError()])
    def test_network_error_fails_closed(self, error, caplog):
        with caplog.at_level(logging.DEBUG), \
                patch("urllib.request.urlopen", side_effect=error):
            assert not turnstile.verify(SECRET, "tok", VISITOR_IP)
        assert SECRET not in caplog.text

    def test_bad_json_fails_closed(self):
        with patch("urllib.request.urlopen", return_value=FakeResp(b"<html>")):
            assert not turnstile.verify(SECRET, "tok")

    def test_blank_token_makes_no_request(self):
        with patch("urllib.request.urlopen") as urlopen:
            assert not turnstile.verify(SECRET, "")
        urlopen.assert_not_called()


# ---------------------------------------------------------------------------
# GET /api/auth/config
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestAuthConfigEndpoint:

    def test_off(self, monkeypatch, database):
        client = make_client(monkeypatch, database, config("", ""), "127.0.0.1")
        res = client.get("/api/auth/config", headers=TUNNEL_HEADERS)
        assert res.status_code == 200
        assert res.json() == {"turnstile_site_key": None, "required": False}

    def test_no_api_config(self, monkeypatch, database):
        client = make_client(monkeypatch, database, None, "127.0.0.1")
        assert client.get("/api/auth/config", headers=TUNNEL_HEADERS).json()["required"] is False

    def test_configured_lan_not_required(self, lan):
        res = lan.get("/api/auth/config", headers=TUNNEL_HEADERS)  # spoofed header
        assert res.json() == {"turnstile_site_key": SITE_KEY, "required": False}

    def test_configured_local_without_header_not_required(self, tunnel):
        assert tunnel.get("/api/auth/config").json()["required"] is False

    def test_configured_tunnel_required(self, tunnel):
        res = tunnel.get("/api/auth/config", headers=TUNNEL_HEADERS)
        assert res.json() == {"turnstile_site_key": SITE_KEY, "required": True}

    def test_secret_never_returned(self, tunnel):
        res = tunnel.get("/api/auth/config", headers=TUNNEL_HEADERS)
        assert SECRET not in res.text
        assert set(res.json()) == {"turnstile_site_key", "required"}


# ---------------------------------------------------------------------------
# POST /api/auth/login
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestLogin:

    def test_tunnel_without_token_rejected(self, tunnel):
        with patch.object(turnstile, "verify", wraps=turnstile.verify) as verify, \
                patch("urllib.request.urlopen") as urlopen:
            res = login(tunnel)
        assert res.status_code == 400
        assert "CAPTCHA" in res.json()["detail"]
        assert verify.called
        urlopen.assert_not_called()

    def test_tunnel_with_valid_token_logs_in(self, tunnel):
        with patch.object(turnstile, "verify", return_value=True) as verify:
            res = login(tunnel, token="good")
        assert res.status_code == 200
        assert "access_token" in res.json()
        verify.assert_called_once_with(SECRET, "good", VISITOR_IP)

    def test_valid_token_wrong_password_is_401(self, tunnel):
        with patch.object(turnstile, "verify", return_value=True):
            assert login(tunnel, token="good", password="wrong-pass").status_code == 401

    def test_token_checked_before_password(self, tunnel):
        with patch.object(turnstile, "verify", return_value=False), \
                patch.object(AuthManager, "authenticate_user") as auth:
            assert login(tunnel, token="bad").status_code == 400
        auth.assert_not_called()

    def test_siteverify_failure_rejected(self, tunnel):
        body = b'{"success": false}'
        with patch("urllib.request.urlopen", return_value=FakeResp(body)):
            assert login(tunnel, token="bad").status_code == 400

    def test_siteverify_timeout_fails_closed(self, tunnel):
        with patch("urllib.request.urlopen", side_effect=socket.timeout("slow")):
            assert login(tunnel, token="tok").status_code == 400

    def test_captcha_failures_count_toward_limit(self, tunnel):
        with patch.object(turnstile, "verify", return_value=False) as verify:
            for _ in range(main.LOGIN_FAILURES_PER_MINUTE):
                assert login(tunnel).status_code == 400
            assert login(tunnel, token="tok").status_code == 429
        # The limited attempt never reached siteverify
        assert verify.call_count == main.LOGIN_FAILURES_PER_MINUTE

    def test_local_login_without_header_needs_no_token(self, tunnel):
        # The button manager: loopback, no CF-Connecting-IP
        with patch.object(turnstile, "verify") as verify:
            assert login(tunnel, headers={}).status_code == 200
        verify.assert_not_called()

    def test_lan_login_needs_no_token(self, lan):
        with patch.object(turnstile, "verify") as verify:
            assert login(lan, headers={}).status_code == 200
            assert login(lan).status_code == 200  # spoofed CF header from the LAN
        verify.assert_not_called()

    def test_turnstile_off_unchanged(self, monkeypatch, database):
        client = make_client(monkeypatch, database, config("", ""), "127.0.0.1")
        with patch.object(turnstile, "verify") as verify:
            assert login(client).status_code == 200
            assert login(client, password="wrong-pass").status_code == 401
        verify.assert_not_called()
        main.login_limiter.reset()

    def test_secret_not_logged(self, tunnel, caplog):
        with caplog.at_level(logging.DEBUG), \
                patch("urllib.request.urlopen", side_effect=urllib.error.URLError("x")):
            login(tunnel, token="tok")
        assert SECRET not in caplog.text
