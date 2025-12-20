"""Tests for Button Manager API Endpoints

Tests the REST API endpoints for button manager functionality.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch, MagicMock
import time

from api.routers import buttons
from api.services.button_manager import ButtonManagerService, ButtonAction
from api.core.auth import get_current_user


@pytest.fixture
def mock_button_manager():
    """Fixture providing a mock button manager."""
    manager = Mock(spec=ButtonManagerService)
    manager.enabled = True
    manager.repeat_mode = False
    manager.audio_on = False
    manager.last_action = None
    manager.last_action_time = None

    # Mock get_status
    manager.get_status.return_value = {
        'enabled': True,
        'repeat_mode': False,
        'audio_on': False,
        'last_action': None,
        'last_action_time': None
    }

    # Mock check_health
    manager.check_health.return_value = {
        'healthy': True,
        'stuck_button': None,
        'stuck_duration': None,
        'warning': None
    }

    # Mock handle_button_action
    manager.handle_button_action.return_value = True

    return manager


@pytest.fixture
def test_app(mock_button_manager):
    """Fixture providing a test FastAPI app with button routes."""
    app = FastAPI()

    # Mock the button manager in the router
    buttons._button_manager = mock_button_manager

    # Include the button router
    app.include_router(buttons.router)

    return app


@pytest.fixture
def client(test_app):
    """Fixture providing a test client."""
    return TestClient(test_app)


@pytest.fixture
def mock_current_user():
    """Fixture providing a mock current user."""
    return {"username": "testuser", "id": 1}


@pytest.fixture
def auth_headers(test_app, mock_current_user):
    """Fixture providing authentication headers with mocked auth."""
    # Override the get_current_user dependency
    async def override_get_current_user():
        return mock_current_user

    test_app.dependency_overrides[get_current_user] = override_get_current_user

    # Return empty dict since auth is mocked
    return {}


class TestButtonEndpointsAuth:
    """Test authentication requirements for button endpoints."""

    def test_status_requires_auth(self, test_app):
        """Test status endpoint requires authentication."""
        # Create client without auth override
        client = TestClient(test_app)
        response = client.get("/buttons/status")
        # FastAPI returns 403 when dependency fails, or 401 if we implement it
        # For now, just test that it's not 200 (successful)
        assert response.status_code in [401, 403, 422]

    def test_health_requires_auth(self, test_app):
        """Test health endpoint requires authentication."""
        client = TestClient(test_app)
        response = client.get("/buttons/health")
        assert response.status_code in [401, 403, 422]

    def test_skip_requires_auth(self, test_app):
        """Test skip endpoint requires authentication."""
        client = TestClient(test_app)
        response = client.post("/buttons/skip")
        assert response.status_code in [401, 403, 422]

    def test_repeat_toggle_requires_auth(self, test_app):
        """Test repeat toggle requires authentication."""
        client = TestClient(test_app)
        response = client.post("/buttons/repeat/toggle")
        assert response.status_code in [401, 403, 422]

    def test_audio_toggle_requires_auth(self, test_app):
        """Test audio toggle requires authentication."""
        client = TestClient(test_app)
        response = client.post("/buttons/audio/toggle")
        assert response.status_code in [401, 403, 422]


class TestStatusEndpoint:
    """Test GET /buttons/status endpoint."""

    def test_get_status(self, client, auth_headers, mock_button_manager):
        """Test getting button manager status."""
        response = client.get("/buttons/status", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()

        assert 'enabled' in data
        assert 'repeat_mode' in data
        assert 'audio_on' in data
        assert 'last_action' in data
        assert 'last_action_time' in data

        # Verify mock was called
        mock_button_manager.get_status.assert_called_once()

    def test_get_status_with_last_action(self, client, auth_headers, mock_button_manager):
        """Test status includes last action when present."""
        mock_button_manager.get_status.return_value = {
            'enabled': True,
            'repeat_mode': True,
            'audio_on': True,
            'last_action': 'skip',
            'last_action_time': '2025-12-18T12:00:00'
        }

        response = client.get("/buttons/status", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data['last_action'] == 'skip'
        assert data['repeat_mode'] == True
        assert data['audio_on'] == True


class TestHealthEndpoint:
    """Test GET /buttons/health endpoint."""

    def test_health_check_healthy(self, client, auth_headers, mock_button_manager):
        """Test health check when system is healthy."""
        response = client.get("/buttons/health", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()

        assert data['healthy'] == True
        assert data['stuck_button'] is None
        assert data['stuck_duration'] is None
        assert data['warning'] is None

        mock_button_manager.check_health.assert_called_once()

    def test_health_check_stuck_button(self, client, auth_headers, mock_button_manager):
        """Test health check with stuck button."""
        mock_button_manager.check_health.return_value = {
            'healthy': False,
            'stuck_button': 'skip',
            'stuck_duration': 35.5,
            'warning': "Button 'skip' may be stuck (35.5s)"
        }

        response = client.get("/buttons/health", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()

        assert data['healthy'] == False
        assert data['stuck_button'] == 'skip'
        assert data['stuck_duration'] == 35.5
        assert 'stuck' in data['warning'].lower()


class TestSkipEndpoint:
    """Test POST /buttons/skip endpoint."""

    def test_skip_button(self, client, auth_headers, mock_button_manager):
        """Test skip button action."""
        response = client.post("/buttons/skip", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()

        assert data['success'] == True
        assert data['action'] == 'skip'
        assert 'skip' in data['message'].lower()

        # Verify service method was called
        mock_button_manager.handle_button_action.assert_called_once_with(ButtonAction.SKIP)

    def test_skip_button_failure(self, client, auth_headers, mock_button_manager):
        """Test skip button when action fails."""
        mock_button_manager.handle_button_action.side_effect = Exception("Lightshow not available")

        response = client.post("/buttons/skip", headers=auth_headers)

        assert response.status_code == 500
        assert 'detail' in response.json()
        assert 'failed' in response.json()['detail'].lower()


class TestRepeatToggleEndpoint:
    """Test POST /buttons/repeat/toggle endpoint."""

    def test_repeat_toggle_enable(self, client, auth_headers, mock_button_manager):
        """Test enabling repeat mode."""
        mock_button_manager.handle_button_action.return_value = True

        response = client.post("/buttons/repeat/toggle", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()

        assert data['success'] == True
        assert data['action'] == 'repeat_toggle'
        assert 'enabled' in data['message'].lower()

        mock_button_manager.handle_button_action.assert_called_once_with(ButtonAction.REPEAT_TOGGLE)

    def test_repeat_toggle_disable(self, client, auth_headers, mock_button_manager):
        """Test disabling repeat mode."""
        mock_button_manager.handle_button_action.return_value = False

        response = client.post("/buttons/repeat/toggle", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()

        assert data['success'] == True
        assert 'disabled' in data['message'].lower()

    def test_repeat_toggle_failure(self, client, auth_headers, mock_button_manager):
        """Test repeat toggle when action fails."""
        mock_button_manager.handle_button_action.side_effect = Exception("Button manager disabled")

        response = client.post("/buttons/repeat/toggle", headers=auth_headers)

        assert response.status_code == 500
        assert 'detail' in response.json()


class TestAudioToggleEndpoint:
    """Test POST /buttons/audio/toggle endpoint."""

    def test_audio_toggle_on(self, client, auth_headers, mock_button_manager):
        """Test turning audio on."""
        mock_button_manager.handle_button_action.return_value = True

        response = client.post("/buttons/audio/toggle", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()

        assert data['success'] == True
        assert data['action'] == 'audio_toggle'
        assert 'on' in data['message'].lower()

        mock_button_manager.handle_button_action.assert_called_once_with(ButtonAction.AUDIO_TOGGLE)

    def test_audio_toggle_off(self, client, auth_headers, mock_button_manager):
        """Test turning audio off."""
        mock_button_manager.handle_button_action.return_value = False

        response = client.post("/buttons/audio/toggle", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()

        assert data['success'] == True
        assert 'off' in data['message'].lower()

    def test_audio_toggle_cooldown(self, client, auth_headers, mock_button_manager):
        """Test audio toggle respects cooldown."""
        mock_button_manager.handle_button_action.side_effect = Exception("Audio toggle on cooldown (3.5s remaining)")

        response = client.post("/buttons/audio/toggle", headers=auth_headers)

        assert response.status_code == 500
        assert 'cooldown' in response.json()['detail'].lower()


class TestButtonManagerNotInitialized:
    """Test endpoints when button manager is not initialized."""

    def test_endpoints_fail_without_button_manager(self, mock_current_user):
        """Test endpoints return 503 when button manager not initialized."""
        # Create app without button manager
        app = FastAPI()
        buttons._button_manager = None
        app.include_router(buttons.router)

        # Override auth
        async def override_get_current_user():
            return mock_current_user
        app.dependency_overrides[get_current_user] = override_get_current_user

        client = TestClient(app)

        # Try each endpoint
        response = client.get("/buttons/status")
        assert response.status_code == 503
        assert 'not initialized' in response.json()['detail'].lower()

        response = client.post("/buttons/skip")
        assert response.status_code == 503

        response = client.post("/buttons/repeat/toggle")
        assert response.status_code == 503


class TestButtonEndpointIntegration:
    """Integration tests for button endpoints."""

    def test_skip_updates_status(self, client, auth_headers, mock_button_manager):
        """Test skip action updates last_action in status."""
        # Perform skip
        response = client.post("/buttons/skip", headers=auth_headers)
        assert response.status_code == 200

        # Update mock to reflect state change
        mock_button_manager.get_status.return_value['last_action'] = 'skip'

        # Check status
        response = client.get("/buttons/status", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()['last_action'] == 'skip'

    def test_repeat_toggle_updates_status(self, client, auth_headers, mock_button_manager):
        """Test repeat toggle updates status."""
        # Enable repeat
        mock_button_manager.handle_button_action.return_value = True
        response = client.post("/buttons/repeat/toggle", headers=auth_headers)
        assert response.status_code == 200

        # Update mock
        mock_button_manager.get_status.return_value['repeat_mode'] = True

        # Check status
        response = client.get("/buttons/status", headers=auth_headers)
        assert response.json()['repeat_mode'] == True
