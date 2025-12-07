# Platform-Specific Test Behavior

## Overview

Some tests in the LightShowPi test suite are **platform-specific** and will be skipped on non-Linux systems. This is **expected behavior** and not a test failure.

## Why Tests Are Skipped on macOS/Windows

### ALSA Audio Library
The primary reason for skipped tests is the **pyalsaaudio** dependency, which provides access to ALSA (Advanced Linux Sound Architecture). This is a **Linux-only** audio system that:

- **Does not exist** on macOS (uses CoreAudio instead)
- **Does not exist** on Windows (uses DirectSound/WASAPI instead)
- **Is required** for `synchronized_lights.py` to import properly

### Expected Skip Behavior

When running tests on **macOS or Windows**, you will see:

```
SKIPPED [10] Requires Linux/ALSA (skipped on macOS/Windows)
```

These tests are marked with `@pytest.mark.platform` and include:
- `TestAudioFileWrapper` - Tests for audio file wrapper class
- `TestDecoderCompatibility` - Tests for decoder compatibility layer
- `TestWrapperMethods` - Tests for wrapper methods (getframerate, getnchannels, readframes)

### Tests That Run Everywhere

These tests **do not require ALSA** and run on all platforms:
- `TestSoundFileIntegration` - Tests that soundfile library is installed
- `TestNoGitDependencies` - Tests that old git-based decoder is not used
- `TestSupportedFormats` - Tests that common audio formats are recognized
- All Platform.py tests (Pi detection)
- All gpio_adapter.py tests (using mocks)
- All configuration tests
- All FFT tests

## Running Tests on Raspberry Pi/Linux

When you run tests on a **Raspberry Pi or Linux** system with pyalsaaudio installed, all platform-specific tests will execute.

### Install ALSA on Linux
```bash
# Debian/Ubuntu/Raspberry Pi OS
sudo apt-get install libasound2-dev

# Install Python package
pip install pyalsaaudio>=0.10.0
```

## Test Audio Files

Some tests require actual audio files:
```
SKIPPED [2] Requires test audio file
```

To enable these tests, place test audio files in the `tests/fixtures/` directory.

### Creating Test Fixtures

You can create a simple test WAV file with:

```python
import numpy as np
import soundfile as sf

# Generate 1 second of 440Hz sine wave
sample_rate = 44100
t = np.linspace(0, 1, sample_rate)
audio = np.sin(2 * np.pi * 440 * t)

# Save as test file
sf.write('tests/fixtures/test_tone.wav', audio, sample_rate)
```

## Summary Table

| Test Class | Requires Linux/ALSA | Requires Test Audio | Runs on macOS |
|------------|---------------------|---------------------|---------------|
| TestAudioFileWrapper | ✅ | ❌ | ❌ |
| TestDecoderCompatibility | ✅ | ❌ | ❌ |
| TestWrapperMethods | ✅ | ❌ | ❌ |
| TestRealAudioFile | ✅ | ✅ | ❌ |
| TestSoundFileIntegration | ❌ | ❌ | ✅ |
| TestNoGitDependencies | ❌ | ❌ | ✅ |
| All Platform tests | ❌ | ❌ | ✅ |
| All gpio_adapter tests | ❌ | ❌ | ✅ |
| All configuration tests | ❌ | ❌ | ✅ |
| All FFT tests | ❌ | ❌ | ✅ |

## Continuous Integration

When setting up GitHub Actions CI/CD:
- Use **Ubuntu runners** for full test coverage
- Consider adding a macOS runner to verify tests skip correctly
- Mark platform-specific jobs clearly in workflow
