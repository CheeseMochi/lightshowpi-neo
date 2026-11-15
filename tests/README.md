# LightShowPi Neo Test Suite

This directory contains the test suite for the modernized LightShowPi Neo codebase.

## Test Organization

Tests are organized by the component they test:

- **test_platform.py** - Platform detection and hardware identification (Pi 3+ only)
- **test_gpio_adapter.py** - lgpio compatibility layer (replaces wiringPi)
- **test_configuration.py** - Configuration file loading and validation
- **test_fft.py** - CPU-based FFT audio processing (no GPU dependency)
- **test_decoder.py** - soundfile-based audio decoding (replaces git-based decoder)
- **test_gpio_hardware.py** - GPIO loopback tests for the Pi test bench (`LSP_BENCH=1`, wiring in `bench.yaml`)

### Test Fixtures

- **fixtures/test_tone.wav** - Test audio file (1-second 440Hz stereo sine wave at 44100Hz)
  - Used by decoder integration tests
  - Can be regenerated if needed (see `fixtures/generate_test_audio.py`)

## Running Tests

### Prerequisites

Install dependencies (on development machine):
```bash
conda env create -f environment-dev.yml
conda activate lightshowpi-neo-dev
```

Install dependencies (on Raspberry Pi):
```bash
conda env create -f environment.yml
conda activate lightshowpi-neo
```

### Run All Tests

```bash
# From repository root
pytest

# With verbose output
pytest -v

# With coverage report
pytest --cov=py --cov-report=html
```

### Run Specific Test Files

```bash
# Test platform detection
pytest tests/test_platform.py

# Test GPIO adapter
pytest tests/test_gpio_adapter.py

# Test configuration
pytest tests/test_configuration.py
```

### Run Tests by Marker

Tests are marked with categories for selective execution:

```bash
# Run only unit tests
pytest -m unit

# Run only integration tests
pytest -m integration

# Run tests that don't require Pi hardware (hardware tests are also
# skipped automatically unless LSP_BENCH=1, see below)
pytest -m "not platform and not gpio and not audio"

# Run only fast tests
pytest -m "not slow"
```

## Test Markers

- **unit** - Unit tests for individual components (no external dependencies)
- **integration** - Integration tests across multiple components
- **platform** - Tests requiring a Raspberry Pi (Pi 3+) *
- **gpio** - Tests requiring real GPIO hardware wired per `bench.yaml` *
- **audio** - Tests requiring real audio hardware (ALSA) *
- **slow** - Tests that take significant time to run

\* Hardware markers. `conftest.py` skips these unless `LSP_BENCH=1`, so a plain
`pytest` on the Mac or in CI never touches hardware. Tests that only *mock*
hardware (e.g. the button manager and `gpio_adapter` tests) use `unit` or
`integration`, not these markers, so they always run.

## Hardware Test Bench (LSP_BENCH)

`test_gpio_hardware.py` holds loopback tests that drive real pins and read them
back through jumper wires. They are meant to run unattended on a Raspberry Pi
test bench (e.g. a self-hosted GitHub Actions runner).

### Wiring

Pin pairs live in `tests/bench.yaml` as `[output BCM, input BCM]`. The defaults:

| Pair | Output | Input | Jumper (physical pins) |
|------|--------|-------|------------------------|
| 1 | BCM 17 | BCM 27 | pin 11 -> pin 13 |
| 2 | BCM 22 | BCM 23 | pin 15 -> pin 16 |

Six more pairs are listed (commented out) in `bench.yaml` for a fuller bench,
up to 8 pairs / 16 pins. A 1k ohm series resistor per jumper is recommended.
Point `LSP_BENCH_CONFIG` at another file to use a different wiring.

### What runs (per pair)

- Digital HIGH/LOW loopback through `gpio_adapter`
- `hardware_controller.Channel` on/off, `active_low` and inverted overrides
- Soft PWM duty cycle (adapter and PWM `Channel`): the input is sampled for
  `pwm_sample_seconds` (default 0.1 s) and the fraction of HIGH samples must be
  within `pwm_tolerance` (default 0.10) of the requested duty

### Running

```bash
# On the bench Pi, from the repo root (user in the gpio group, or use sudo)
export SYNCHRONIZED_LIGHTS_HOME=$(pwd)
LSP_BENCH=1 pytest -m gpio

# Convenience wrapper (sets LSP_BENCH=1 and runs -m gpio on that file)
python tests/test_gpio_hardware.py

# All hardware-marked tests (gpio, audio, platform)
LSP_BENCH=1 pytest -m "gpio or audio or platform"
```

Off the Pi, `LSP_BENCH=1 pytest -m gpio` skips every GPIO test with a reason
(not a Raspberry Pi, lgpio missing, no `/dev/gpiochip*`).

### GPIO chip selection

`gpio_adapter` picks the gpiochip that drives the 40-pin header by label
(`pinctrl-rp1` on Pi 5, `pinctrl-bcm2711` on Pi 4, `pinctrl-bcm2835` on Pi 3),
falling back to `gpiochip0`. On a Pi 5 with an older kernel the header is
`gpiochip4`. Force a chip with `LSP_GPIOCHIP=4` (also accepts `gpiochip4` or
`/dev/gpiochip4`).

## Test Coverage

The test suite covers:

### 1. Hardware Simplification (Pi 3+ Only)
- ✅ Pi 3, 4, 5 detection
- ✅ Pi 1, 2, Zero rejection
- ✅ I2C bus detection (always bus 1 on Pi 3+)
- ✅ Hardware info extraction
- ✅ Removal of legacy platform support

### 2. Modern Dependencies
- ✅ lgpio compatibility layer (gpio_adapter.py)
- ✅ soundfile decoder wrapper
- ✅ CPU-based numpy FFT
- ✅ No GPU dependencies

### 3. Feature Removal
- ✅ No FM configuration sections
- ✅ No SMS configuration sections
- ✅ No Twitter references
- ✅ GPU acceleration disabled

### 4. Performance Optimization
- ✅ Default chunk_size = 4096 for Pi 3+
- ✅ Various chunk sizes supported (1024-8192)
- ✅ Multiple sample rates supported

## Writing New Tests

### Test File Structure

```python
"""
Brief description of what this test file covers.
"""
import pytest
from unittest.mock import Mock, patch

@pytest.mark.unit
class TestComponentName:
    """Test specific functionality."""

    def test_feature(self):
        """Test that feature works correctly."""
        # Arrange
        # Act
        # Assert
        assert True
```

### Using Fixtures

Shared fixtures are defined in `conftest.py`:

```python
def test_with_config(temp_config_file):
    """Use temporary config file fixture."""
    config = load_config(temp_config_file)
    assert config is not None

def test_pi3_detection(mock_cpuinfo_pi3):
    """Use mock Pi 3 cpuinfo fixture."""
    version = detect_pi_version()
    assert version == 3
```

## Continuous Integration

These tests are designed to run in CI/CD pipelines (GitHub Actions):

- All unit tests run on every push
- Integration tests run on pull requests
- Hardware tests (`gpio`, `audio`, `platform`) run on a self-hosted Pi runner with `LSP_BENCH=1`

## Troubleshooting

### Import Errors

If you get import errors, ensure the `py/` directory is in your Python path:

```bash
export PYTHONPATH="${PYTHONPATH}:$(pwd)/py"
pytest
```

Or use the pytest.ini configuration (already set up):
```ini
pythonpath = py
```

### Platform-Specific Tests

Some tests require Raspberry Pi hardware. On development machines, these tests will be skipped or use mocks.

To run platform-specific tests on a Raspberry Pi:
```bash
LSP_BENCH=1 pytest -m "gpio or audio or platform"
```

### Missing Dependencies

If tests fail due to missing dependencies:

```bash
# On Raspberry Pi
conda env update -f environment.yml --prune

# On development machine (macOS/Windows)
conda env update -f environment-dev.yml --prune
```

## Test Philosophy

1. **Isolation** - Tests should not depend on external state
2. **Mocking** - Hardware dependencies are mocked for portability
3. **Coverage** - Aim for >80% code coverage on critical paths
4. **Speed** - Unit tests should run quickly (<1s each)
5. **Clarity** - Test names clearly describe what they test

## Contributing

When adding new features:

1. Write tests first (TDD approach)
2. Ensure existing tests still pass
3. Add appropriate markers (@pytest.mark.unit, etc.)
4. Update this README if adding new test categories
