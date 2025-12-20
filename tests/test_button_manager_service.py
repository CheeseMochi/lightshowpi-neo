"""Tests for Button Manager Service

Tests the button manager service layer that handles both physical
and virtual button actions.
"""

import pytest
import time
from datetime import datetime
from unittest.mock import Mock, MagicMock

from api.services.button_manager import ButtonManagerService, ButtonAction


class MockLightshowManager:
    """Mock lightshow manager for testing."""

    def __init__(self):
        self.running = False
        self.skip_called = False
        self.start_called = False

    def is_running(self):
        return self.running

    def skip(self):
        self.skip_called = True

    def start(self):
        self.start_called = True
        self.running = True

    def stop(self):
        self.running = False


@pytest.fixture
def mock_lightshow_manager():
    """Fixture providing a mock lightshow manager."""
    return MockLightshowManager()


@pytest.fixture
def button_config():
    """Fixture providing button manager configuration."""
    return {
        'button_manager': {
            'enabled': True,
            'audio_timeout': 300,
            'cooldown': 5
        }
    }


@pytest.fixture
def button_manager(button_config, mock_lightshow_manager):
    """Fixture providing a button manager service instance."""
    manager = ButtonManagerService(button_config, mock_lightshow_manager)
    return manager


class TestButtonManagerInit:
    """Test button manager initialization."""

    def test_init_with_config(self, button_config, mock_lightshow_manager):
        """Test initialization with valid config."""
        manager = ButtonManagerService(button_config, mock_lightshow_manager)

        assert manager.enabled == True
        assert manager.repeat_mode == False
        assert manager.audio_on == False
        assert manager.audio_timeout == 300
        assert manager.cooldown_duration == 5
        assert manager.lightshow_manager is not None

    def test_init_disabled(self, mock_lightshow_manager):
        """Test initialization with button manager disabled."""
        config = {'button_manager': {'enabled': False}}
        manager = ButtonManagerService(config, mock_lightshow_manager)

        assert manager.enabled == False


class TestButtonManagerStatus:
    """Test status reporting."""

    def test_get_status_initial(self, button_manager):
        """Test getting initial status."""
        status = button_manager.get_status()

        assert status['enabled'] == True
        assert status['repeat_mode'] == False
        assert status['audio_on'] == False
        assert status['last_action'] is None
        assert status['last_action_time'] is None

    def test_get_status_after_action(self, button_manager):
        """Test status after performing action."""
        button_manager.handle_button_action(ButtonAction.SKIP)
        status = button_manager.get_status()

        assert status['last_action'] == 'skip'
        assert status['last_action_time'] is not None
        assert isinstance(status['last_action_time'], datetime)


class TestSkipButton:
    """Test skip button action."""

    def test_skip_when_not_playing(self, button_manager, mock_lightshow_manager):
        """Test skip starts playback when not running."""
        mock_lightshow_manager.running = False

        result = button_manager.handle_button_action(ButtonAction.SKIP)

        assert mock_lightshow_manager.start_called == True
        assert mock_lightshow_manager.skip_called == False
        assert button_manager.audio_on == True

    def test_skip_when_playing(self, button_manager, mock_lightshow_manager):
        """Test skip advances to next song when playing."""
        mock_lightshow_manager.running = True

        result = button_manager.handle_button_action(ButtonAction.SKIP)

        assert mock_lightshow_manager.skip_called == True
        assert button_manager.audio_on == True

    def test_skip_when_disabled(self, button_config, mock_lightshow_manager):
        """Test skip fails when button manager disabled."""
        button_config['button_manager']['enabled'] = False
        manager = ButtonManagerService(button_config, mock_lightshow_manager)

        with pytest.raises(Exception, match="Button manager is disabled"):
            manager.handle_button_action(ButtonAction.SKIP)


class TestRepeatToggle:
    """Test repeat mode toggle."""

    def test_repeat_enable(self, button_manager, mock_lightshow_manager):
        """Test enabling repeat mode."""
        assert button_manager.repeat_mode == False

        result = button_manager.handle_button_action(ButtonAction.REPEAT_TOGGLE)

        assert result == True  # Returns new state
        assert button_manager.repeat_mode == True
        assert mock_lightshow_manager.start_called == True
        assert button_manager.audio_on == True

    def test_repeat_disable(self, button_manager):
        """Test disabling repeat mode."""
        button_manager.repeat_mode = True
        button_manager.audio_on = True

        result = button_manager.handle_button_action(ButtonAction.REPEAT_TOGGLE)

        assert result == False  # Returns new state
        assert button_manager.repeat_mode == False
        assert button_manager.audio_on == False

    def test_repeat_toggle_cycle(self, button_manager):
        """Test toggling repeat on and off."""
        # Enable
        button_manager.handle_button_action(ButtonAction.REPEAT_TOGGLE)
        assert button_manager.repeat_mode == True

        # Disable
        button_manager.handle_button_action(ButtonAction.REPEAT_TOGGLE)
        assert button_manager.repeat_mode == False

        # Enable again
        button_manager.handle_button_action(ButtonAction.REPEAT_TOGGLE)
        assert button_manager.repeat_mode == True


class TestAudioToggle:
    """Test audio relay toggle."""

    def test_audio_toggle_on(self, button_manager, mock_lightshow_manager):
        """Test turning audio on."""
        assert button_manager.audio_on == False

        result = button_manager.handle_button_action(ButtonAction.AUDIO_TOGGLE)

        assert result == True  # Returns new state
        assert button_manager.audio_on == True
        assert button_manager.audio_shutoff_time is not None
        # Should start playback if not running
        assert mock_lightshow_manager.start_called == True

    def test_audio_toggle_off(self, button_manager):
        """Test turning audio off."""
        button_manager.audio_on = True

        result = button_manager.handle_button_action(ButtonAction.AUDIO_TOGGLE)

        assert result == False  # Returns new state
        assert button_manager.audio_on == False
        assert button_manager.audio_shutoff_time is None

    def test_audio_toggle_cooldown(self, button_manager):
        """Test audio toggle respects cooldown."""
        # First toggle - should succeed
        button_manager.handle_button_action(ButtonAction.AUDIO_TOGGLE)

        # Immediate second toggle - should fail due to cooldown
        with pytest.raises(Exception, match="Audio toggle on cooldown"):
            button_manager.handle_button_action(ButtonAction.AUDIO_TOGGLE)

    def test_audio_toggle_after_cooldown(self, button_manager):
        """Test audio toggle works after cooldown expires."""
        # First toggle
        button_manager.handle_button_action(ButtonAction.AUDIO_TOGGLE)
        assert button_manager.audio_on == True

        # Fast-forward past cooldown
        button_manager.audio_cooldown_until = time.time() - 1

        # Second toggle should succeed
        result = button_manager.handle_button_action(ButtonAction.AUDIO_TOGGLE)
        assert result == False  # Toggled off
        assert button_manager.audio_on == False

    def test_audio_auto_shutoff(self, button_manager):
        """Test audio auto-shutoff timer."""
        button_manager.audio_on = True
        button_manager.repeat_mode = False
        button_manager.audio_shutoff_time = time.time() - 1  # Past shutoff time

        button_manager.check_auto_shutoff()

        assert button_manager.audio_on == False

    def test_audio_no_shutoff_in_repeat_mode(self, button_manager):
        """Test audio doesn't auto-shutoff in repeat mode."""
        button_manager.audio_on = True
        button_manager.repeat_mode = True
        button_manager.audio_shutoff_time = time.time() - 1  # Past shutoff time

        button_manager.check_auto_shutoff()

        # Should still be on because repeat mode is active
        assert button_manager.audio_on == True


class TestStuckButtonDetection:
    """Test stuck button detection."""

    def test_health_check_healthy(self, button_manager):
        """Test health check when no stuck buttons."""
        health = button_manager.check_health()

        assert health['healthy'] == True
        assert health['stuck_button'] is None
        assert health['stuck_duration'] is None
        assert health['warning'] is None

    def test_stuck_skip_button(self, button_manager):
        """Test detection of stuck skip button."""
        # Simulate skip button pressed and held for 31 seconds
        button_manager.button_press_start['skip'] = time.time() - 31

        health = button_manager.check_health()

        assert health['healthy'] == False
        assert health['stuck_button'] == 'skip'
        assert health['stuck_duration'] > 30
        assert 'stuck' in health['warning'].lower()

    def test_stuck_audio_button(self, button_manager):
        """Test detection of stuck audio button."""
        # Simulate audio button pressed and held for 35 seconds
        button_manager.button_press_start['audio'] = time.time() - 35

        health = button_manager.check_health()

        assert health['healthy'] == False
        assert health['stuck_button'] == 'audio'
        assert health['stuck_duration'] > 30

    def test_repeat_button_not_stuck(self, button_manager):
        """Test repeat button is excluded from stuck detection."""
        # Simulate repeat button held for 31 seconds (should be ignored)
        button_manager.button_press_start['repeat'] = time.time() - 31

        health = button_manager.check_health()

        # Should still be healthy - repeat button excluded
        assert health['healthy'] == True
        assert health['stuck_button'] is None

    def test_repeat_and_skip_buttons_pressed(self, button_manager):
        """Test repeat button ignored when other buttons stuck."""
        # Repeat button held long time (should be ignored)
        button_manager.button_press_start['repeat'] = time.time() - 100
        # Skip button stuck
        button_manager.button_press_start['skip'] = time.time() - 31

        health = button_manager.check_health()

        # Should detect skip as stuck, not repeat
        assert health['healthy'] == False
        assert health['stuck_button'] == 'skip'

    def test_button_press_tracking(self, button_manager):
        """Test button press/release tracking."""
        # Simulate button press
        button_manager.update_from_physical({'button_pressed': 'skip'})
        assert 'skip' in button_manager.button_press_start

        # Simulate button release
        button_manager.update_from_physical({'button_released': 'skip'})
        assert 'skip' not in button_manager.button_press_start

    def test_multiple_stuck_buttons(self, button_manager):
        """Test only first stuck button is reported."""
        # Multiple buttons stuck
        button_manager.button_press_start['skip'] = time.time() - 40
        button_manager.button_press_start['audio'] = time.time() - 35

        health = button_manager.check_health()

        # Should report one of them (whichever is checked first)
        assert health['healthy'] == False
        assert health['stuck_button'] in ['skip', 'audio']


class TestPhysicalButtonIntegration:
    """Test integration with physical button manager."""

    def test_update_state_from_physical(self, button_manager):
        """Test updating state from physical button manager."""
        state = {
            'repeat_mode': True,
            'audio_on': True
        }

        button_manager.update_from_physical(state)

        assert button_manager.repeat_mode == True
        assert button_manager.audio_on == True

    def test_set_lightshow_manager_after_init(self, button_config):
        """Test setting lightshow manager after initialization."""
        manager = ButtonManagerService(button_config, None)
        assert manager.lightshow_manager is None

        mock_manager = MockLightshowManager()
        manager.set_lightshow_manager(mock_manager)

        assert manager.lightshow_manager is not None
        assert manager.lightshow_manager == mock_manager
