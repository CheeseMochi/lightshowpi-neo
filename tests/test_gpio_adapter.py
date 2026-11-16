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


@pytest.fixture
def fresh_adapter(mock_lgpio, monkeypatch):
    """Import a fresh gpio_adapter bound to the mocked lgpio, with LSP_GPIOCHIP unset."""
    monkeypatch.delenv('LSP_GPIOCHIP', raising=False)
    sys.modules.pop('gpio_adapter', None)
    import gpio_adapter
    yield gpio_adapter
    gpio_adapter.cleanup()


@pytest.mark.unit
class TestLgpioImportGuard:
    """gpio_adapter must import without lgpio and fail clearly when used."""

    def test_import_without_lgpio(self):
        sys.modules.pop('gpio_adapter', None)
        with patch.dict('sys.modules', {'lgpio': None}):
            import gpio_adapter
            assert gpio_adapter.lgpio is None
            with pytest.raises(RuntimeError, match='lgpio is not available'):
                gpio_adapter.wiringPiSetupGpio()
            with pytest.raises(RuntimeError, match='lgpio is not available'):
                gpio_adapter.wiringPiSetupPY()
            assert gpio_adapter._chip_handle is None
        sys.modules.pop('gpio_adapter', None)


@pytest.mark.unit
class TestGpiochipSelection:
    """Test selection of the /dev/gpiochipN that drives the 40-pin header."""

    @pytest.mark.parametrize('value, expected', [
        ('4', 4), ('0', 0), ('gpiochip4', 4), ('/dev/gpiochip4', 4), (' 2 ', 2),
    ])
    def test_env_var_selects_chip(self, fresh_adapter, monkeypatch, value, expected):
        monkeypatch.setenv('LSP_GPIOCHIP', value)
        with patch.object(fresh_adapter, '_list_chip_labels') as labels:
            assert fresh_adapter.select_gpiochip() == expected
            labels.assert_not_called()  # env var wins, no probing

    @pytest.mark.parametrize('value', ['abc', 'gpiochip', '-1', '/dev/gpiomem'])
    def test_invalid_env_var_raises(self, fresh_adapter, monkeypatch, value):
        monkeypatch.setenv('LSP_GPIOCHIP', value)
        with pytest.raises(ValueError, match='LSP_GPIOCHIP'):
            fresh_adapter.select_gpiochip()

    def test_empty_env_var_is_ignored(self, fresh_adapter, monkeypatch):
        monkeypatch.setenv('LSP_GPIOCHIP', '')
        with patch.object(fresh_adapter, '_list_chip_labels', return_value=[]):
            assert fresh_adapter.select_gpiochip() == 0

    @pytest.mark.parametrize('chips, expected', [
        # Pi 5, older kernel: RP1 header controller is gpiochip4
        ([(0, 'gpio-brcmstb@107d508500'), (1, 'gpio-brcmstb@107d508520'),
          (2, 'gpio-brcmstb@107d517c00'), (3, 'gpio-brcmstb@107d517c20'),
          (4, 'pinctrl-rp1')], 4),
        # Pi 5, newer kernel: RP1 renumbered to gpiochip0
        ([(0, 'pinctrl-rp1'), (10, 'gpio-brcmstb@107d508500')], 0),
        # Pi 4
        ([(0, 'pinctrl-bcm2711'), (1, 'raspberrypi-exp-gpio')], 0),
        # Pi 3
        ([(0, 'pinctrl-bcm2835'), (1, 'raspberrypi-exp-gpio')], 0),
    ], ids=['pi5-old-kernel', 'pi5-new-kernel', 'pi4', 'pi3'])
    def test_auto_detect_by_label(self, fresh_adapter, chips, expected):
        with patch.object(fresh_adapter, '_list_chip_labels', return_value=chips):
            assert fresh_adapter.select_gpiochip() == expected

    @pytest.mark.parametrize('chips', [[], [(0, 'some-other-chip'), (3, 'gpio-mockup')]],
                             ids=['no-chips', 'unknown-labels'])
    def test_falls_back_to_chip_zero(self, fresh_adapter, chips):
        with patch.object(fresh_adapter, '_list_chip_labels', return_value=chips):
            assert fresh_adapter.select_gpiochip() == 0

    def test_detection_error_falls_back_to_chip_zero(self, fresh_adapter):
        with patch.object(fresh_adapter, '_list_chip_labels', side_effect=OSError('boom')):
            assert fresh_adapter.select_gpiochip() == 0

    def test_list_chip_labels_reads_labels_via_lgpio(self, fresh_adapter, mock_lgpio):
        handles = {0: 10, 4: 14}
        mock_lgpio.gpiochip_open.side_effect = lambda n: handles[n]
        mock_lgpio.gpio_get_chip_info.side_effect = lambda h: {
            10: (0, 54, 'gpiochip0', 'gpio-brcmstb@107d508500'),
            14: (0, 54, 'gpiochip4', 'pinctrl-rp1'),
        }[h]
        with patch.object(fresh_adapter.glob, 'glob',
                          return_value=['/dev/gpiochip4', '/dev/gpiochip0', '/dev/gpiochip-bad']):
            chips = fresh_adapter._list_chip_labels()
        assert chips == [(0, 'gpio-brcmstb@107d508500'), (4, 'pinctrl-rp1')]
        closed = sorted(c.args[0] for c in mock_lgpio.gpiochip_close.call_args_list)
        assert closed == [10, 14]

    def test_list_chip_labels_skips_unopenable_chip(self, fresh_adapter, mock_lgpio):
        mock_lgpio.gpiochip_open.side_effect = PermissionError('denied')
        with patch.object(fresh_adapter.glob, 'glob', return_value=['/dev/gpiochip0']):
            assert fresh_adapter._list_chip_labels() == []

    @pytest.mark.parametrize('setup', ['wiringPiSetupPY', 'wiringPiSetupGpio'])
    def test_setup_opens_selected_chip(self, fresh_adapter, mock_lgpio, setup):
        with patch.object(fresh_adapter, '_list_chip_labels', return_value=[(4, 'pinctrl-rp1')]):
            getattr(fresh_adapter, setup)()
        mock_lgpio.gpiochip_open.assert_called_once_with(4)
        assert fresh_adapter.get_info()['chip_number'] == 4

    def test_setup_defaults_to_chip_zero(self, fresh_adapter, mock_lgpio):
        with patch.object(fresh_adapter, '_list_chip_labels', return_value=[]):
            fresh_adapter.wiringPiSetupPY()
        mock_lgpio.gpiochip_open.assert_called_once_with(0)

    def test_cleanup_resets_chip_number(self, fresh_adapter, mock_lgpio, monkeypatch):
        monkeypatch.setenv('LSP_GPIOCHIP', '4')
        fresh_adapter.wiringPiSetupGpio()
        fresh_adapter.cleanup()
        assert fresh_adapter.get_info()['chip_number'] is None
        mock_lgpio.gpiochip_close.assert_called_once()
