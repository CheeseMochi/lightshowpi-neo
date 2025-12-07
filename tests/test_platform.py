"""
Tests for Platform.py - Hardware detection and platform-specific functionality.
"""
import pytest
from unittest.mock import patch, mock_open
import sys
import os


# Import Platform module
import Platform


@pytest.mark.unit
class TestPlatformDetection:
    """Test platform detection functionality."""

    def test_pi3_detection(self, mock_cpuinfo_pi3):
        """Test that Pi 3 is correctly detected."""
        with patch('builtins.open', mock_open(read_data=mock_cpuinfo_pi3.read_text())):
            version = Platform.pi_version()
            assert version == Platform.PI_3, f"Expected PI_3, got {version}"

    def test_pi4_detection(self, mock_cpuinfo_pi4):
        """Test that Pi 4 is correctly detected."""
        with patch('builtins.open', mock_open(read_data=mock_cpuinfo_pi4.read_text())):
            version = Platform.pi_version()
            assert version == Platform.PI_4, f"Expected PI_4, got {version}"

    def test_pi5_detection(self, mock_cpuinfo_pi5):
        """Test that Pi 5 is correctly detected."""
        with patch('builtins.open', mock_open(read_data=mock_cpuinfo_pi5.read_text())):
            version = Platform.pi_version()
            assert version == Platform.PI_5, f"Expected PI_5, got {version}"

    def test_pi2_rejected(self, mock_cpuinfo_pi2):
        """Test that Pi 2 is correctly rejected (returns None)."""
        with patch('builtins.open', mock_open(read_data=mock_cpuinfo_pi2.read_text())):
            version = Platform.pi_version()
            assert version is None, f"Pi 2 should be rejected, got {version}"

    def test_pi1_rejected(self, mock_cpuinfo_pi1):
        """Test that Pi 1 is correctly rejected (returns None)."""
        with patch('builtins.open', mock_open(read_data=mock_cpuinfo_pi1.read_text())):
            version = Platform.pi_version()
            assert version is None, f"Pi 1 should be rejected, got {version}"

    def test_missing_cpuinfo(self):
        """Test graceful handling when /proc/cpuinfo is missing."""
        with patch('builtins.open', side_effect=FileNotFoundError()):
            version = Platform.pi_version()
            assert version is None, "Should return None when cpuinfo missing"

    def test_platform_detect_pi3(self, mock_cpuinfo_pi3):
        """Test platform_detect returns RASPBERRY_PI for Pi 3."""
        with patch('builtins.open', mock_open(read_data=mock_cpuinfo_pi3.read_text())):
            platform = Platform.platform_detect()
            assert platform == Platform.RASPBERRY_PI

    def test_platform_detect_pi4(self, mock_cpuinfo_pi4):
        """Test platform_detect returns RASPBERRY_PI for Pi 4."""
        with patch('builtins.open', mock_open(read_data=mock_cpuinfo_pi4.read_text())):
            platform = Platform.platform_detect()
            assert platform == Platform.RASPBERRY_PI

    def test_platform_detect_unsupported(self, mock_cpuinfo_pi1):
        """Test platform_detect returns UNKNOWN for unsupported Pi."""
        with patch('builtins.open', mock_open(read_data=mock_cpuinfo_pi1.read_text())):
            platform = Platform.platform_detect()
            assert platform == Platform.UNKNOWN


@pytest.mark.unit
class TestI2CBus:
    """Test I2C bus detection."""

    def test_i2c_bus_always_1(self, mock_cpuinfo_pi3):
        """Test that I2C bus is always 1 on Pi 3+."""
        with patch('builtins.open', mock_open(read_data=mock_cpuinfo_pi3.read_text())):
            bus = Platform.get_i2c_bus()
            assert bus == 1, "I2C bus should always be 1 on Pi 3+"

    def test_i2c_bus_pi4(self, mock_cpuinfo_pi4):
        """Test that I2C bus is 1 on Pi 4."""
        with patch('builtins.open', mock_open(read_data=mock_cpuinfo_pi4.read_text())):
            bus = Platform.get_i2c_bus()
            assert bus == 1

    def test_i2c_bus_pi5(self, mock_cpuinfo_pi5):
        """Test that I2C bus is 1 on Pi 5."""
        with patch('builtins.open', mock_open(read_data=mock_cpuinfo_pi5.read_text())):
            bus = Platform.get_i2c_bus()
            assert bus == 1


@pytest.mark.unit
class TestHardwareInfo:
    """Test hardware info extraction."""

    def test_get_hardware_info_pi3(self, mock_cpuinfo_pi3):
        """Test hardware info extraction for Pi 3."""
        with patch('builtins.open', mock_open(read_data=mock_cpuinfo_pi3.read_text())):
            info = Platform.get_hardware_info()
            assert isinstance(info, dict)
            assert info['version'] == Platform.PI_3
            assert 'Pi 3' in info['model']
            assert info['platform'] == Platform.RASPBERRY_PI
            assert info['supported'] == True

    def test_get_hardware_info_pi4(self, mock_cpuinfo_pi4):
        """Test hardware info extraction for Pi 4."""
        with patch('builtins.open', mock_open(read_data=mock_cpuinfo_pi4.read_text())):
            info = Platform.get_hardware_info()
            assert isinstance(info, dict)
            assert info['version'] == Platform.PI_4
            assert 'Pi 4' in info['model']
            assert info['platform'] == Platform.RASPBERRY_PI
            assert info['supported'] == True

    def test_get_hardware_info_pi5(self, mock_cpuinfo_pi5):
        """Test hardware info extraction for Pi 5."""
        with patch('builtins.open', mock_open(read_data=mock_cpuinfo_pi5.read_text())):
            info = Platform.get_hardware_info()
            assert isinstance(info, dict)
            assert info['version'] == Platform.PI_5
            assert 'Pi 5' in info['model']
            assert info['platform'] == Platform.RASPBERRY_PI
            assert info['supported'] == True

    def test_get_hardware_info_missing_file(self):
        """Test hardware info when cpuinfo is missing."""
        with patch('builtins.open', side_effect=FileNotFoundError()):
            info = Platform.get_hardware_info()
            assert isinstance(info, dict)
            assert info['version'] is None
            assert info['supported'] == False
            assert info['platform'] == Platform.UNKNOWN


@pytest.mark.unit
class TestPiModel:
    """Test Pi model string extraction."""

    def test_get_pi_model_pi3(self, mock_cpuinfo_pi3):
        """Test model extraction for Pi 3."""
        with patch('builtins.open', mock_open(read_data=mock_cpuinfo_pi3.read_text())):
            cpuinfo = mock_cpuinfo_pi3.read_text()
            model = Platform.get_pi_model(cpuinfo)
            assert 'Pi 3' in model

    def test_get_pi_model_pi4(self, mock_cpuinfo_pi4):
        """Test model extraction for Pi 4."""
        with patch('builtins.open', mock_open(read_data=mock_cpuinfo_pi4.read_text())):
            cpuinfo = mock_cpuinfo_pi4.read_text()
            model = Platform.get_pi_model(cpuinfo)
            assert 'Pi 4' in model

    def test_get_pi_model_no_match(self):
        """Test model extraction with no Model line but with revision."""
        cpuinfo_no_model = "Hardware : BCM2711\nRevision : c03111\n"
        model = Platform.get_pi_model(cpuinfo_no_model)
        # Should fall back to revision code lookup
        assert model is not None
        assert 'Pi 4' in model


@pytest.mark.unit
class TestConstants:
    """Test that expected constants are defined."""

    def test_platform_constants_exist(self):
        """Verify platform constants are defined."""
        assert hasattr(Platform, 'RASPBERRY_PI')
        assert hasattr(Platform, 'UNKNOWN')
        assert Platform.RASPBERRY_PI == 1
        assert Platform.UNKNOWN == 0

    def test_pi_version_constants_exist(self):
        """Verify Pi version constants are defined."""
        assert hasattr(Platform, 'PI_3')
        assert hasattr(Platform, 'PI_4')
        assert hasattr(Platform, 'PI_5')
        assert Platform.PI_3 == 3
        assert Platform.PI_4 == 4
        assert Platform.PI_5 == 5

    def test_header40_exists(self):
        """Verify header40 constant exists and contains pin info."""
        assert hasattr(Platform, 'header40')
        # header40 is now a string containing GPIO documentation
        assert isinstance(Platform.header40, str)
        assert 'GPIO' in Platform.header40
        assert '40-pin' in Platform.header40
        assert 'Pi 3, 4, 5' in Platform.header40

    def test_no_legacy_constants(self):
        """Verify legacy constants have been removed."""
        # These should NOT exist after simplification
        assert not hasattr(Platform, 'BEAGLEBONE_BLACK')
        assert not hasattr(Platform, 'MINNOWBOARD')
        assert not hasattr(Platform, 'header26')
        assert not hasattr(Platform, 'pi_revision')
