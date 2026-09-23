"""Tests for buttonmanager.py API credential resolution, login and re-login.

`requests` is mocked; no API server is needed.
"""
import configparser
import sys
from unittest.mock import MagicMock, Mock

import pytest

if 'gpiozero' not in sys.modules:
    sys.modules['gpiozero'] = MagicMock()

import buttonmanager as bm  # noqa: E402


def make_config(buttons=None, api=None):
    config = configparser.RawConfigParser()
    config.add_section('buttons')
    config.add_section('api')
    for key, value in (buttons or {}).items():
        config.set('buttons', key, value)
    for key, value in (api or {}).items():
        config.set('api', key, value)
    return config


def response(status, json_data=None, text=""):
    resp = Mock()
    resp.status_code = status
    resp.json.return_value = json_data or {}
    resp.text = text
    return resp


@pytest.fixture(autouse=True)
def reset_globals(monkeypatch):
    monkeypatch.setattr(bm, "API_ENABLED", False)
    monkeypatch.setattr(bm, "API_TOKEN", None)
    monkeypatch.setattr(bm, "API_BASE_URL", "http://localhost:8000/api")
    monkeypatch.setattr(bm, "API_USERNAME", "admin")
    monkeypatch.setattr(bm, "API_PASSWORD", None)
    monkeypatch.setattr(bm, "BUTTON_MODE", "auto")


@pytest.fixture
def mock_requests(monkeypatch):
    fake = MagicMock()
    fake.exceptions = bm.requests.exceptions
    monkeypatch.setattr(bm, "requests", fake)
    return fake


@pytest.mark.unit
class TestResolveApiSettings:

    def test_defaults_use_api_port(self):
        url, user, password = bm.resolve_api_settings(make_config(api={'port': '8123'}), environ={})
        assert url == "http://localhost:8123/api"
        assert user == "admin"
        assert password is None

    def test_default_port_8000_when_not_configured(self):
        url, _, _ = bm.resolve_api_settings(make_config(), environ={})
        assert url == "http://localhost:8000/api"

    def test_defaults_cfg_resolves_to_port_8000_without_password(self):
        import configuration_manager
        cm = configuration_manager.Configuration()
        url, user, password = bm.resolve_api_settings(cm.config, environ={})
        assert url == f"http://localhost:{cm.config.getint('api', 'port')}/api"
        assert user == "admin"
        assert password is None

    def test_config_values_used(self):
        config = make_config(buttons={'api_url': 'http://pi.local:9000/api/',
                                      'api_username': 'buttons',
                                      'api_password': 'from-config'})
        assert bm.resolve_api_settings(config, environ={}) == (
            "http://pi.local:9000/api", "buttons", "from-config")

    def test_env_overrides_config(self):
        config = make_config(buttons={'api_url': 'http://config/api',
                                      'api_username': 'config-user',
                                      'api_password': 'config-pass'})
        environ = {bm.ENV_API_URL: 'http://env/api',
                   bm.ENV_API_USERNAME: 'env-user',
                   bm.ENV_API_PASSWORD: 'env-pass'}
        assert bm.resolve_api_settings(config, environ=environ) == (
            "http://env/api", "env-user", "env-pass")

    def test_blank_config_values_fall_through_to_defaults(self):
        config = make_config(buttons={'api_url': '', 'api_username': '', 'api_password': ''})
        assert bm.resolve_api_settings(config, environ={}) == (
            "http://localhost:8000/api", "admin", None)

    def test_configure_api_never_logs_password(self, caplog):
        caplog.set_level("DEBUG")
        bm.configure_api(make_config(buttons={'api_password': 's3cret-value'}), environ={})
        assert bm.API_PASSWORD == 's3cret-value'
        assert 's3cret-value' not in caplog.text


@pytest.mark.unit
class TestInitApiWithoutPassword:

    def test_auto_mode_falls_back_to_direct(self, mock_requests):
        assert bm.init_api("auto") is False
        assert bm.API_ENABLED is False
        mock_requests.get.assert_not_called()
        mock_requests.post.assert_not_called()

    def test_api_mode_exits(self, mock_requests):
        with pytest.raises(SystemExit):
            bm.init_api("api")
        mock_requests.post.assert_not_called()

    def test_direct_mode_skips_api(self, mock_requests):
        bm.API_PASSWORD = "pw"
        assert bm.init_api("direct") is False
        mock_requests.get.assert_not_called()


@pytest.mark.unit
class TestInitApiWithPassword:

    def test_login_success_enables_api(self, mock_requests, caplog):
        caplog.set_level("DEBUG")
        bm.API_PASSWORD = "pw-123"
        mock_requests.get.return_value = response(200)
        mock_requests.post.return_value = response(200, {"access_token": "tok"})

        assert bm.init_api("auto") is True
        assert bm.API_ENABLED is True
        assert bm.API_TOKEN == "tok"
        mock_requests.post.assert_called_once_with(
            "http://localhost:8000/api/auth/login",
            json={"username": "admin", "password": "pw-123"},
            timeout=2)
        assert "pw-123" not in caplog.text

    def test_bad_password_auto_mode_falls_back(self, mock_requests):
        bm.API_PASSWORD = "wrong"
        mock_requests.get.return_value = response(200)
        mock_requests.post.return_value = response(401)
        assert bm.init_api("auto") is False
        assert bm.API_ENABLED is False

    def test_bad_password_api_mode_exits(self, mock_requests):
        bm.API_PASSWORD = "wrong"
        mock_requests.get.return_value = response(200)
        mock_requests.post.return_value = response(401)
        with pytest.raises(SystemExit):
            bm.init_api("api")


@pytest.mark.unit
class TestCallApiRelogin:

    @pytest.fixture
    def logged_in(self):
        bm.API_PASSWORD = "pw"
        bm.API_ENABLED = True
        bm.API_TOKEN = "old-token"

    def test_success_without_relogin(self, mock_requests, logged_in):
        mock_requests.post.return_value = response(200)
        assert bm.call_api("/buttons/skip") is True
        assert mock_requests.post.call_count == 1

    def test_401_triggers_single_relogin_and_retry(self, mock_requests, logged_in):
        mock_requests.post.side_effect = [
            response(401),                                   # original call
            response(200, {"access_token": "new-token"}),    # login
            response(200),                                   # retry
        ]
        assert bm.call_api("/buttons/skip") is True
        assert bm.API_TOKEN == "new-token"

        calls = mock_requests.post.call_args_list
        assert len(calls) == 3
        assert calls[1].args[0] == "http://localhost:8000/api/auth/login"
        assert calls[2].kwargs["headers"] == {"Authorization": "Bearer new-token"}

    def test_401_retry_only_once(self, mock_requests, logged_in):
        mock_requests.post.side_effect = [
            response(401),
            response(200, {"access_token": "new-token"}),
            response(401),
        ]
        assert bm.call_api("/buttons/skip") is False
        assert mock_requests.post.call_count == 3

    def test_relogin_failure_returns_false(self, mock_requests, logged_in):
        mock_requests.post.side_effect = [response(401), response(401)]
        assert bm.call_api("/buttons/skip") is False
        assert mock_requests.post.call_count == 2

    def test_get_requests_relogin_too(self, mock_requests, logged_in):
        mock_requests.get.side_effect = [response(401), response(200)]
        mock_requests.post.return_value = response(200, {"access_token": "new-token"})
        assert bm.call_api("/buttons/status", method="GET") is True
        assert mock_requests.get.call_count == 2
