"""
Tests for buttonmanager.py - Physical button controls for lightshow.

Note: These tests use mocks since they test GPIO hardware interactions.
Integration tests on actual Pi hardware should be performed manually.

Many tests require gpiozero which is Linux/Pi only - these will skip on macOS/Windows.
"""
import pytest
import sys
import time
from unittest.mock import Mock, MagicMock, patch, call
from pathlib import Path

# Mock gpiozero for platforms where it's not available (macOS, Windows)
# This allows us to test the logic without requiring actual GPIO hardware
if 'gpiozero' not in sys.modules:
    sys.modules['gpiozero'] = MagicMock()

# Now we can import buttonmanager safely
try:
    import buttonmanager
    BUTTONMANAGER_AVAILABLE = True
except ImportError as e:
    BUTTONMANAGER_AVAILABLE = False
    buttonmanager = None


@pytest.mark.unit
class TestButtonConfiguration:
    """Test button configuration loading."""

    def test_config_section_exists(self):
        """Test that [buttons] section exists in defaults.cfg."""
        import configuration_manager as cm

        config = cm.Configuration()
        assert config.config.has_section('buttons')

    def test_config_has_required_fields(self):
        """Test that all required configuration fields are present."""
        import configuration_manager as cm

        config = cm.Configuration()

        # Check all required fields
        assert config.config.has_option('buttons', 'enabled')
        assert config.config.has_option('buttons', 'repeat_pin')
        assert config.config.has_option('buttons', 'skip_pin')
        assert config.config.has_option('buttons', 'audio_toggle_pin')
        assert config.config.has_option('buttons', 'outlet_relay_pin')
        assert config.config.has_option('buttons', 'button_cooldown')
        assert config.config.has_option('buttons', 'audio_auto_shutoff')
        assert config.config.has_option('buttons', 'repeat_max_iterations')
        assert config.config.has_option('buttons', 'repeat_hold_time')
        assert config.config.has_option('buttons', 'log_button_presses')

    def test_config_default_values(self):
        """Test that configuration default values are reasonable."""
        import configuration_manager as cm

        config = cm.Configuration()

        # Test GPIO pins are in valid range (0-27 for BCM)
        repeat_pin = config.config.getint('buttons', 'repeat_pin')
        skip_pin = config.config.getint('buttons', 'skip_pin')
        audio_pin = config.config.getint('buttons', 'audio_toggle_pin')
        outlet_pin = config.config.getint('buttons', 'outlet_relay_pin')

        assert 0 <= repeat_pin <= 27
        assert 0 <= skip_pin <= 27
        assert 0 <= audio_pin <= 27
        assert 0 <= outlet_pin <= 27

        # Test pins are unique
        pins = [repeat_pin, skip_pin, audio_pin, outlet_pin]
        assert len(pins) == len(set(pins)), "Button pins must be unique"

        # Test timing values are positive
        assert config.config.getint('buttons', 'button_cooldown') > 0
        assert config.config.getint('buttons', 'audio_auto_shutoff') > 0
        assert config.config.getint('buttons', 'repeat_max_iterations') > 0
        assert config.config.getint('buttons', 'repeat_hold_time') > 0


@pytest.mark.unit
@pytest.mark.gpio
@pytest.mark.skipif(not BUTTONMANAGER_AVAILABLE, reason="buttonmanager not available (requires gpiozero)")
class TestButtonFunctions:
    """Test button manager functions with mocked GPIO."""

    @patch('buttonmanager.outlet')
    @patch('buttonmanager.cm')
    def test_audio_on(self, mock_cm, mock_outlet):
        """Test audio_on() function."""
        buttonmanager.audio_on()

        # Should turn outlet on
        mock_outlet.on.assert_called_once()

        # Should set cooldown and turnoff timers
        assert buttonmanager.audio_cooldown > time.time()
        assert buttonmanager.audio_turnoff > time.time()

    @patch('buttonmanager.outlet')
    @patch('buttonmanager.cm')
    def test_audio_off(self, mock_cm, mock_outlet):
        """Test audio_off() function."""
        import buttonmanager

        buttonmanager.audio_off()

        # Should turn outlet off
        mock_outlet.off.assert_called_once()

        # Should set cooldown
        assert buttonmanager.audio_cooldown > time.time()

    @patch('buttonmanager.outlet')
    @patch('buttonmanager.cm')
    @patch('buttonmanager.playsong')
    def test_audio_toggle_when_off_and_playing(self, mock_playsong, mock_cm, mock_outlet):
        """Test audio_toggle() when outlet is off and song is playing."""
        import buttonmanager

        # Mock outlet is not lit
        mock_outlet.is_lit = False

        # Mock song is playing
        mock_cm.get_state.return_value = "1"

        buttonmanager.audio_toggle()

        # Should toggle outlet
        mock_outlet.toggle.assert_called_once()

        # Should NOT call playsong
        mock_playsong.assert_not_called()

    @patch('buttonmanager.outlet')
    @patch('buttonmanager.cm')
    @patch('buttonmanager.playsong')
    def test_audio_toggle_when_off_and_not_playing(self, mock_playsong, mock_cm, mock_outlet):
        """Test audio_toggle() when outlet is off and no song playing."""
        import buttonmanager

        # Mock outlet is not lit
        mock_outlet.is_lit = False

        # Mock no song is playing
        mock_cm.get_state.return_value = "0"

        buttonmanager.audio_toggle()

        # Should call playsong to start playback
        mock_playsong.assert_called_once_with(-1)

        # Should NOT toggle outlet (playsong handles it)
        mock_outlet.toggle.assert_not_called()

    @patch('buttonmanager.cm')
    @patch('buttonmanager.audio_on')
    def test_playsong(self, mock_audio_on, mock_cm):
        """Test playsong() function."""
        import buttonmanager

        buttonmanager.playsong(-1)

        # Should update state
        mock_cm.update_state.assert_called_once_with('play_now', -1)

        # Should turn audio on
        mock_audio_on.assert_called_once()

    @patch('buttonmanager.cm')
    @patch('buttonmanager.playsong')
    def test_songbutton_skip(self, mock_playsong, mock_cm):
        """Test songbutton() for skip button."""
        import buttonmanager

        # Mock play_now is ready (0)
        mock_cm.get_state.return_value = "0"

        # Create mock button with skip pin
        mock_button = Mock()
        mock_button.pin.number = buttonmanager.SKIP_PIN

        buttonmanager.songbutton(mock_button)

        # Should trigger playsong
        mock_playsong.assert_called_once_with(-1)

    @patch('buttonmanager.cm')
    @patch('buttonmanager.playsong')
    def test_songbutton_blocked_when_busy(self, mock_playsong, mock_cm):
        """Test songbutton() is blocked when play_now is already set."""
        import buttonmanager

        # Mock play_now is busy (not 0)
        mock_cm.get_state.return_value = "5"

        mock_button = Mock()
        mock_button.pin.number = buttonmanager.SKIP_PIN

        buttonmanager.songbutton(mock_button)

        # Should NOT trigger playsong
        mock_playsong.assert_not_called()

    @patch('buttonmanager.playsong')
    @patch('buttonmanager.audio_off')
    def test_toggleRepeat_enable(self, mock_audio_off, mock_playsong):
        """Test toggleRepeat() to enable repeat mode."""
        import buttonmanager

        buttonmanager.repeat_mode = False

        buttonmanager.toggleRepeat()

        # Should enable repeat mode
        assert buttonmanager.repeat_mode is True

        # Should start playing
        mock_playsong.assert_called_once_with(-1)

        # Should NOT turn audio off
        mock_audio_off.assert_not_called()

    @patch('buttonmanager.playsong')
    @patch('buttonmanager.audio_off')
    def test_toggleRepeat_disable(self, mock_audio_off, mock_playsong):
        """Test toggleRepeat() to disable repeat mode."""
        import buttonmanager

        buttonmanager.repeat_mode = True

        buttonmanager.toggleRepeat()

        # Should disable repeat mode
        assert buttonmanager.repeat_mode is False

        # Should turn audio off
        mock_audio_off.assert_called_once()

        # Should NOT start playing
        mock_playsong.assert_not_called()

    @patch('buttonmanager.audio_toggle')
    def test_audiobutton_with_cooldown_ok(self, mock_audio_toggle):
        """Test audiobutton() when cooldown has expired."""
        import buttonmanager

        # Set cooldown to past
        buttonmanager.audio_cooldown = time.time() - 10

        buttonmanager.audiobutton()

        # Should call audio_toggle
        mock_audio_toggle.assert_called_once()

    @patch('buttonmanager.audio_toggle')
    def test_audiobutton_with_cooldown_active(self, mock_audio_toggle):
        """Test audiobutton() when cooldown is active."""
        import buttonmanager

        # Set cooldown to future
        buttonmanager.audio_cooldown = time.time() + 10

        buttonmanager.audiobutton()

        # Should NOT call audio_toggle
        mock_audio_toggle.assert_not_called()


@pytest.mark.unit
@pytest.mark.skipif(not BUTTONMANAGER_AVAILABLE, reason="buttonmanager not available (requires gpiozero)")
class TestButtonCleanup:
    """Test cleanup and shutdown behavior."""

    @patch('buttonmanager.outlet')
    @patch('buttonmanager.repeat_button')
    @patch('buttonmanager.skip_button')
    @patch('buttonmanager.audio_button')
    @patch('buttonmanager.cm')
    @patch('buttonmanager.sys.exit')
    def test_doCleanup(self, mock_exit, mock_cm, mock_audio_btn, mock_skip_btn,
                       mock_repeat_btn, mock_outlet):
        """Test doCleanup() closes all resources."""
        import buttonmanager

        buttonmanager.doCleanup()

        # Should reset play_now state
        mock_cm.update_state.assert_called_once_with('play_now', "0")

        # Should turn off outlet
        mock_outlet.off.assert_called_once()

        # Should close all GPIO resources
        mock_outlet.close.assert_called_once()
        mock_repeat_btn.close.assert_called_once()
        mock_skip_btn.close.assert_called_once()
        mock_audio_btn.close.assert_called_once()

        # Should exit
        mock_exit.assert_called_once_with(0)


@pytest.mark.unit
@pytest.mark.skipif(not BUTTONMANAGER_AVAILABLE, reason="buttonmanager not available (requires gpiozero)")
class TestButtonIntegration:
    """Integration tests for button manager (still using mocks)."""

    @patch('buttonmanager.outlet')
    @patch('buttonmanager.cm')
    def test_repeat_mode_flow(self, mock_cm, mock_outlet):
        """Test complete repeat mode flow."""
        import buttonmanager

        # Start in non-repeat mode
        buttonmanager.repeat_mode = False

        # Enable repeat mode
        with patch('buttonmanager.playsong') as mock_playsong:
            buttonmanager.toggleRepeat()
            assert buttonmanager.repeat_mode is True
            mock_playsong.assert_called_with(-1)

        # Disable repeat mode
        with patch('buttonmanager.audio_off') as mock_audio_off:
            buttonmanager.toggleRepeat()
            assert buttonmanager.repeat_mode is False
            mock_audio_off.assert_called_once()

    @patch('buttonmanager.outlet')
    @patch('buttonmanager.cm')
    def test_audio_cooldown_prevents_spam(self, mock_cm, mock_outlet):
        """Test that cooldown prevents button spam."""
        import buttonmanager

        # First press should work (cooldown in the past)
        buttonmanager.audio_cooldown = 0
        with patch('buttonmanager.audio_toggle') as mock_toggle:
            buttonmanager.audiobutton()
            assert mock_toggle.call_count == 1

        # Simulate cooldown being set by audio_toggle
        buttonmanager.audio_cooldown = time.time() + 10  # 10 seconds in future

        # Immediate second press should be blocked
        with patch('buttonmanager.audio_toggle') as mock_toggle:
            buttonmanager.audiobutton()
            assert mock_toggle.call_count == 0


@pytest.mark.integration
@pytest.mark.gpio
@pytest.mark.skipif(not BUTTONMANAGER_AVAILABLE, reason="buttonmanager not available (requires gpiozero)")
class TestButtonManagerMain:
    """Test main() function behavior (requires manual testing on Pi)."""

    def test_main_imports(self):
        """Test that buttonmanager module can be imported."""
        try:
            import buttonmanager
            assert buttonmanager is not None
        except ImportError as e:
            pytest.skip(f"buttonmanager not importable: {e}")

    def test_main_has_required_functions(self):
        """Test that all required functions exist."""
        import buttonmanager

        assert hasattr(buttonmanager, 'main')
        assert hasattr(buttonmanager, 'doCleanup')
        assert hasattr(buttonmanager, 'audio_on')
        assert hasattr(buttonmanager, 'audio_off')
        assert hasattr(buttonmanager, 'audio_toggle')
        assert hasattr(buttonmanager, 'playsong')
        assert hasattr(buttonmanager, 'toggleRepeat')


@pytest.mark.unit
@pytest.mark.skipif(not BUTTONMANAGER_AVAILABLE, reason="buttonmanager not available (requires gpiozero)")
class TestButtonManagerConstants:
    """Test that configuration constants are properly loaded."""

    def test_constants_exist(self):
        """Test that all required constants are defined."""
        import buttonmanager

        assert hasattr(buttonmanager, 'REPEAT_PIN')
        assert hasattr(buttonmanager, 'SKIP_PIN')
        assert hasattr(buttonmanager, 'AUDIO_PIN')
        assert hasattr(buttonmanager, 'OUTLET_PIN')
        assert hasattr(buttonmanager, 'DEFAULT_COOLDOWN')
        assert hasattr(buttonmanager, 'DEFAULT_AUDIO_TIMEOUT')
        assert hasattr(buttonmanager, 'REPEAT_MAX_ITERATIONS')
        assert hasattr(buttonmanager, 'REPEAT_HOLD_TIME')

    def test_constants_are_valid(self):
        """Test that configuration constants have valid values."""
        import buttonmanager

        # GPIO pins should be in valid BCM range
        assert 0 <= buttonmanager.REPEAT_PIN <= 27
        assert 0 <= buttonmanager.SKIP_PIN <= 27
        assert 0 <= buttonmanager.AUDIO_PIN <= 27
        assert 0 <= buttonmanager.OUTLET_PIN <= 27

        # Timing values should be positive
        assert buttonmanager.DEFAULT_COOLDOWN > 0
        assert buttonmanager.DEFAULT_AUDIO_TIMEOUT > 0
        assert buttonmanager.REPEAT_MAX_ITERATIONS > 0
        assert buttonmanager.REPEAT_HOLD_TIME > 0
