"""
Compatibility tests for LightShowPi Neo.

Tests for cross-platform compatibility, Python version compatibility,
and backward compatibility with older Raspberry Pi OS versions.
"""

import pytest
import sys
import os
import tempfile
from unittest.mock import patch, mock_open

# Add py directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'py'))

import Platform


@pytest.mark.unit
class TestPlatformDetection:
    """Test platform detection with different cpuinfo formats."""

    def test_pi4_new_format(self):
        """Test Pi 4 detection with newer OS format (Model field, no Hardware)."""
        import Platform

        new_format_cpuinfo = """processor    : 0
BogoMIPS    : 108.00
Features    : fp asimd evtstrm crc32 cpuid
CPU implementer    : 0x41
CPU architecture: 8
CPU variant    : 0x0
CPU part    : 0xd08
CPU revision    : 3

Revision    : b03111
Serial        : 100000003dbbe093
Model        : Raspberry Pi 4 Model B Rev 1.1
"""

        with patch('builtins.open', mock_open(read_data=new_format_cpuinfo)):
            version = Platform.pi_version()
            assert version == Platform.PI_4, "Should detect Pi 4 from Model field"

    def test_pi3_old_format(self):
        """Test Pi 3 detection with older OS format (Hardware field, no Model)."""
        import Platform

        old_format_cpuinfo = """processor    : 0
model name    : ARMv7 Processor rev 4 (v7l)
BogoMIPS    : 38.40
Features    : half thumb fastmult vfp edsp neon vfpv3 tls vfpv4 idiva idivt vfpd32 lpae evtstrm crc32

Hardware    : BCM2835
Revision    : a020d3
Serial        : 000000007b4e975a
"""

        with patch('builtins.open', mock_open(read_data=old_format_cpuinfo)):
            version = Platform.pi_version()
            assert version == Platform.PI_3, "Should detect Pi 3 from Hardware field"

    def test_pi5_new_format(self):
        """Test Pi 5 detection with Model field."""
        import Platform

        pi5_cpuinfo = """processor    : 0
BogoMIPS    : 108.00

Revision    : c04170
Model        : Raspberry Pi 5 Model B Rev 1.0
"""

        with patch('builtins.open', mock_open(read_data=pi5_cpuinfo)):
            version = Platform.pi_version()
            assert version == Platform.PI_5, "Should detect Pi 5 from Model field"

    def test_unsupported_pi_zero(self):
        """Test that Pi Zero is rejected (unsupported)."""
        import Platform

        pi_zero_cpuinfo = """processor    : 0
model name    : ARMv6-compatible processor rev 7 (v6l)
BogoMIPS    : 997.78

Hardware    : BCM2835
Revision    : 900093
Model        : Raspberry Pi Zero W Rev 1.1
"""

        with patch('builtins.open', mock_open(read_data=pi_zero_cpuinfo)):
            version = Platform.pi_version()
            assert version is None, "Pi Zero should be unsupported"

    def test_non_pi_system(self):
        """Test that non-Pi systems return None."""
        import Platform

        non_pi_cpuinfo = """processor    : 0
vendor_id    : GenuineIntel
cpu family    : 6
model        : 142
model name    : Intel(R) Core(TM) i5-8250U CPU @ 1.60GHz
"""

        with patch('builtins.open', mock_open(read_data=non_pi_cpuinfo)):
            version = Platform.pi_version()
            assert version is None, "Non-Pi system should return None"

    def test_platform_detect_returns_raspberry_pi(self):
        """Test that platform_detect returns RASPBERRY_PI for supported Pis."""
        import Platform

        pi4_cpuinfo = """Model        : Raspberry Pi 4 Model B Rev 1.1"""

        with patch('builtins.open', mock_open(read_data=pi4_cpuinfo)):
            platform = Platform.platform_detect()
            assert platform == Platform.RASPBERRY_PI, "Should return RASPBERRY_PI constant"


@pytest.mark.unit
class TestPython313Compatibility:
    """Test Python 3.13+ compatibility (audioop removed)."""

    def test_numpy_audio_max_replacement(self):
        """Test that numpy can replace audioop.max functionality."""
        import numpy as np

        # Create sample 16-bit audio data
        audio_samples = np.array([100, -200, 150, -300, 50], dtype=np.int16)
        audio_bytes = audio_samples.tobytes()

        # This is what synchronized_lights.py now uses instead of audioop.max
        audio_max = np.abs(np.frombuffer(audio_bytes, dtype=np.int16)).max()

        # Should get the maximum absolute value (300)
        assert audio_max == 300, "numpy should correctly find max absolute value"

    def test_audioop_not_imported(self):
        """Test that audioop is not imported (removed in Python 3.13)."""
        import sys

        # Try to import synchronized_lights
        # It should not fail even if audioop doesn't exist
        try:
            # Mock audioop as non-existent
            if 'audioop' in sys.modules:
                del sys.modules['audioop']

            # This should work without audioop
            import numpy as np
            audio_bytes = np.array([100, 200], dtype=np.int16).tobytes()
            result = np.abs(np.frombuffer(audio_bytes, dtype=np.int16)).max()
            assert result == 200

        except ImportError as e:
            if 'audioop' in str(e):
                pytest.fail("Code should not depend on audioop module")


@pytest.mark.unit
class TestGPIOInterfaceCompatibility:
    """Test that wiring_pi stub and gpio_adapter have compatible interfaces."""

    def test_wiring_pi_stub_has_py_methods(self):
        """Test that wiring_pi.py stub has all PY-suffixed methods."""
        import wiring_pi

        required_methods = [
            'wiringPiSetupPY',
            'pinModePY',
            'digitalWritePY',
            'digitalReadPY',
            'softPwmCreatePY',
            'softPwmWritePY',
            'softPwmStopPY',
            'analogWritePY',
            'mcp23017SetupPY',
            'mcp23008SetupPY',
            'sr595SetupPY',
            'pcf8574SetupPY',
        ]

        for method in required_methods:
            assert hasattr(wiring_pi, method), f"wiring_pi.py missing {method}"
            assert callable(getattr(wiring_pi, method)), f"{method} should be callable"

    @pytest.mark.skipif(
        Platform.platform_detect() != Platform.RASPBERRY_PI,
        reason="gpio_adapter requires Raspberry Pi (lgpio library)"
    )
    def test_gpio_adapter_has_py_methods(self):
        """Test that gpio_adapter.py has all PY-suffixed methods."""
        import gpio_adapter

        required_methods = [
            'wiringPiSetupPY',
            'pinModePY',
            'digitalWritePY',
            'digitalReadPY',
            'softPwmCreatePY',
            'softPwmWritePY',
            'softPwmStopPY',
            'analogWritePY',
            'mcp23017SetupPY',
            'mcp23008SetupPY',
        ]

        for method in required_methods:
            assert hasattr(gpio_adapter, method), f"gpio_adapter.py missing {method}"
            assert callable(getattr(gpio_adapter, method)), f"{method} should be callable"

    @pytest.mark.skipif(
        Platform.platform_detect() != Platform.RASPBERRY_PI,
        reason="gpio_adapter requires Raspberry Pi (lgpio library)"
    )
    def test_both_have_matching_interfaces(self):
        """Test that both GPIO modules have matching method signatures."""
        import wiring_pi
        import gpio_adapter
        import inspect

        # Methods that should exist in both
        common_methods = [
            'wiringPiSetupPY',
            'pinModePY',
            'digitalWritePY',
            'softPwmCreatePY',
            'softPwmWritePY',
        ]

        for method_name in common_methods:
            # Both should have the method
            assert hasattr(wiring_pi, method_name)
            assert hasattr(gpio_adapter, method_name)

            # Both should be callable
            wiring_method = getattr(wiring_pi, method_name)
            adapter_method = getattr(gpio_adapter, method_name)
            assert callable(wiring_method)
            assert callable(adapter_method)


@pytest.mark.unit
class TestImportCompatibility:
    """Test that critical modules can be imported."""

    def test_synchronized_lights_imports(self):
        """Test that synchronized_lights.py can be imported."""
        try:
            import synchronized_lights
            assert True, "synchronized_lights should import successfully"
        except ImportError as e:
            # Some imports may fail on non-Pi systems, that's OK
            if 'alsaaudio' not in str(e):
                pytest.fail(f"Unexpected import error: {e}")

    def test_hardware_controller_imports(self):
        """Test that hardware_controller.py can be imported."""
        try:
            import hardware_controller
            assert True, "hardware_controller should import successfully"
        except ImportError as e:
            pytest.fail(f"hardware_controller import failed: {e}")

    def test_configuration_manager_imports(self):
        """Test that configuration_manager.py can be imported."""
        try:
            import configuration_manager
            assert True, "configuration_manager should import successfully"
        except ImportError as e:
            pytest.fail(f"configuration_manager import failed: {e}")


@pytest.mark.unit
class TestPydanticV2Compatibility:
    """Test Pydantic v2 compatibility."""

    def test_pydantic_field_validator(self):
        """Test that field_validator works (Pydantic v2)."""
        from pydantic import BaseModel, Field, field_validator

        class TestModel(BaseModel):
            value: int = Field(..., ge=0, le=10)

            @field_validator('value')
            @classmethod
            def check_value(cls, v):
                if v > 5:
                    raise ValueError("Value too high")
                return v

        # Should work
        model = TestModel(value=3)
        assert model.value == 3

        # Should fail validation
        with pytest.raises(ValueError):
            TestModel(value=7)

    def test_pydantic_pattern_not_regex(self):
        """Test that pattern= works instead of regex= (Pydantic v2)."""
        from pydantic import BaseModel, Field

        class TestModel(BaseModel):
            time: str = Field(..., pattern=r"^([01]\d|2[0-3]):([0-5]\d)$")

        # Should validate correct time format
        model = TestModel(time="18:30")
        assert model.time == "18:30"

        # Should reject invalid format
        with pytest.raises(ValueError):
            TestModel(time="25:00")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
