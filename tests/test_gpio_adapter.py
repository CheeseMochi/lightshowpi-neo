"""
Tests for gpio_adapter.py - lgpio compatibility layer providing wiringPi-like API.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
import sys


@pytest.fixture
def mock_lgpio():
    """Mock lgpio module for testing."""
    lgpio_mock = MagicMock()
    lgpio_mock.gpiochip_open.return_value = 0  # Mock chip handle
    lgpio_mock.OUTPUT = 1
    lgpio_mock.INPUT = 0
    lgpio_mock.HIGH = 1
    lgpio_mock.LOW = 0

    # Patch lgpio in sys.modules before importing gpio_adapter
    with patch.dict('sys.modules', {'lgpio': lgpio_mock}):
        yield lgpio_mock


@pytest.mark.unit
class TestGPIOAdapterInitialization:
    """Test GPIO adapter initialization."""

    def test_module_imports(self, mock_lgpio):
        """Test that gpio_adapter can be imported with mocked lgpio."""
        import gpio_adapter
        assert gpio_adapter is not None

    def test_initialization_succeeds(self, mock_lgpio):
        """Test that initialization succeeds with mock."""
        if 'gpio_adapter' in sys.modules:
            del sys.modules['gpio_adapter']

        with patch.dict('sys.modules', {'lgpio': mock_lgpio}):
            import gpio_adapter
            gpio_adapter.wiringPiSetupPY()
            # Verify the module initialized successfully
            assert gpio_adapter._chip_handle is not None


@pytest.mark.unit
class TestConstants:
    """Test that wiringPi-compatible constants are defined."""

    def test_mode_constants(self, mock_lgpio):
        """Test INPUT/OUTPUT constants exist."""
        import gpio_adapter
        assert hasattr(gpio_adapter, 'INPUT')
        assert hasattr(gpio_adapter, 'OUTPUT')
        assert gpio_adapter.OUTPUT == 1
        assert gpio_adapter.INPUT == 0

    def test_value_constants(self, mock_lgpio):
        """Test HIGH/LOW constants exist."""
        import gpio_adapter
        assert hasattr(gpio_adapter, 'HIGH')
        assert hasattr(gpio_adapter, 'LOW')
        assert gpio_adapter.HIGH == 1
        assert gpio_adapter.LOW == 0


@pytest.mark.unit
class TestDigitalIO:
    """Test digital I/O functions."""

    def test_pin_mode_output(self, mock_lgpio):
        """Test setting pin mode to OUTPUT."""
        if 'gpio_adapter' in sys.modules:
            del sys.modules['gpio_adapter']

        with patch.dict('sys.modules', {'lgpio': mock_lgpio}):
            import gpio_adapter
            gpio_adapter.wiringPiSetupPY()
            gpio_adapter.pinModePY(17, gpio_adapter.OUTPUT)
            # Verify gpio_claim_output was called
            assert mock_lgpio.gpio_claim_output.called

    def test_pin_mode_input(self, mock_lgpio):
        """Test setting pin mode to INPUT."""
        if 'gpio_adapter' in sys.modules:
            del sys.modules['gpio_adapter']

        with patch.dict('sys.modules', {'lgpio': mock_lgpio}):
            import gpio_adapter
            gpio_adapter.wiringPiSetupPY()
            gpio_adapter.pinModePY(17, gpio_adapter.INPUT)
            # Verify gpio_claim_input was called
            assert mock_lgpio.gpio_claim_input.called

    def test_digital_write(self, mock_lgpio):
        """Test writing to a pin."""
        if 'gpio_adapter' in sys.modules:
            del sys.modules['gpio_adapter']

        with patch.dict('sys.modules', {'lgpio': mock_lgpio}):
            import gpio_adapter
            gpio_adapter.wiringPiSetupPY()
            gpio_adapter.pinModePY(17, gpio_adapter.OUTPUT)
            gpio_adapter.digitalWritePY(17, 1)
            # Verify gpio_write was called
            assert mock_lgpio.gpio_write.called

    def test_digital_read(self, mock_lgpio):
        """Test reading from a pin."""
        if 'gpio_adapter' in sys.modules:
            del sys.modules['gpio_adapter']

        mock_lgpio.gpio_read.return_value = 1

        with patch.dict('sys.modules', {'lgpio': mock_lgpio}):
            import gpio_adapter
            gpio_adapter.wiringPiSetupPY()
            gpio_adapter.pinModePY(17, gpio_adapter.INPUT)
            value = gpio_adapter.digitalReadPY(17)
            assert value == 1
            assert mock_lgpio.gpio_read.called


@pytest.mark.unit
class TestSoftwarePWM:
    """Test software PWM functions."""

    def test_pwm_create(self, mock_lgpio):
        """Test creating a PWM pin."""
        if 'gpio_adapter' in sys.modules:
            del sys.modules['gpio_adapter']

        with patch.dict('sys.modules', {'lgpio': mock_lgpio}):
            import gpio_adapter
            gpio_adapter.wiringPiSetupPY()
            result = gpio_adapter.softPwmCreatePY(18, 0, 100)
            # Should return 0 for success
            assert result == 0
            # Pin should be claimed as output
            assert mock_lgpio.gpio_claim_output.called

    def test_pwm_write(self, mock_lgpio):
        """Test writing PWM value."""
        if 'gpio_adapter' in sys.modules:
            del sys.modules['gpio_adapter']

        with patch.dict('sys.modules', {'lgpio': mock_lgpio}):
            import gpio_adapter
            gpio_adapter.wiringPiSetupPY()
            gpio_adapter.softPwmCreatePY(18, 0, 100)
            gpio_adapter.softPwmWritePY(18, 50)
            # Should call tx_pwm
            assert mock_lgpio.tx_pwm.called

    def test_pwm_stop(self, mock_lgpio):
        """Test stopping PWM on a pin."""
        if 'gpio_adapter' in sys.modules:
            del sys.modules['gpio_adapter']

        with patch.dict('sys.modules', {'lgpio': mock_lgpio}):
            import gpio_adapter
            gpio_adapter.wiringPiSetupPY()
            gpio_adapter.softPwmCreatePY(18, 0, 100)
            gpio_adapter.softPwmStopPY(18)
            # Should call tx_pwm with 0 frequency to stop
            assert mock_lgpio.tx_pwm.called


@pytest.mark.unit
class TestExpanderWarnings:
    """Test that expander functions warn appropriately."""

    def test_i2c_expander_warning(self, mock_lgpio):
        """Test that I2C expander setup shows warning and returns -1."""
        if 'gpio_adapter' in sys.modules:
            del sys.modules['gpio_adapter']

        with patch.dict('sys.modules', {'lgpio': mock_lgpio}):
            import gpio_adapter
            result = gpio_adapter.mcp23017SetupPY(100, 0x20)
            # Should return -1 (not implemented)
            assert result == -1

    def test_spi_expander_warning(self, mock_lgpio):
        """Test that SPI expander setup shows warning and returns -1."""
        if 'gpio_adapter' in sys.modules:
            del sys.modules['gpio_adapter']

        with patch.dict('sys.modules', {'lgpio': mock_lgpio}):
            import gpio_adapter
            result = gpio_adapter.mcp23s17SetupPY(100, 0, 0)
            # Should return -1 (not implemented)
            assert result == -1


@pytest.mark.unit
class TestErrorHandling:
    """Test error handling in gpio_adapter."""

    def test_pwm_write_without_init(self, mock_lgpio):
        """Test PWM write without calling wiringPiSetupPY."""
        if 'gpio_adapter' in sys.modules:
            del sys.modules['gpio_adapter']

        with patch.dict('sys.modules', {'lgpio': mock_lgpio}):
            import gpio_adapter
            # Don't call wiringPiSetupPY - should handle gracefully
            gpio_adapter.softPwmWritePY(18, 50)
            # Should not crash, just log error

    def test_digital_write_without_init(self, mock_lgpio):
        """Test digital write without calling wiringPiSetupPY."""
        if 'gpio_adapter' in sys.modules:
            del sys.modules['gpio_adapter']

        with patch.dict('sys.modules', {'lgpio': mock_lgpio}):
            import gpio_adapter
            # Don't call wiringPiSetupPY - should handle gracefully
            gpio_adapter.digitalWritePY(17, 1)
            # Should not crash, just log error
