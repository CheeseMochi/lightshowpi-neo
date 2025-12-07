# LightShowPi Neo

> Modern Python 3 resurrection of LightShowPi for Raspberry Pi 3+

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: BSD](https://img.shields.io/badge/License-BSD-green.svg)](LICENSE)
[![Pi 3+](https://img.shields.io/badge/Raspberry%20Pi-3%2B-red.svg)](https://www.raspberrypi.org/)
[![Neo](https://img.shields.io/badge/LightShowPi-Neo-brightgreen.svg)](FORK.md)
[![Tests](https://img.shields.io/badge/tests-passing-brightgreen.svg)](#testing)

**LightShowPi Neo** is a modernized fork of the original LightShowPi that synchronizes Christmas light displays to music on Raspberry Pi. Rebuilt for Python 3.9+, Pi 3/4/5, and pure PyPI dependencies.

**Latest Version:** Neo 1.0 (2025) • [See what's different from original →](FORK.md)

> **⚠️ Important:** This is an independent fork, not the official LightShowPi. For the original project, visit [lightshowpi.org](http://lightshowpi.org/)

---

## ✨ Features

- 🎵 **Audio Synchronization** - Real-time FFT-based light control synced to music
- 🌈 **RGB LED Support** - Control individually addressable LED strips
- 🎛️ **GPIO Expanders** - Support for MCP23017, MCP23008 for 100+ channels
- 🕹️ **Physical Controls** - Optional button manager for skip, repeat, and audio toggle
- 📊 **Multiple Modes** - Playlist, audio-in, streaming, and network client modes
- ⚙️ **Highly Configurable** - Extensive configuration for timing, frequencies, and effects
- 🧪 **Fully Tested** - Comprehensive test suite with 76+ unit and integration tests
- 🚀 **Modern Stack** - Pure PyPI dependencies, no git packages required

---

## 📋 Table of Contents

- [Requirements](#requirements)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [Configuration](#configuration)
- [Button Manager](#button-manager-optional)
- [Testing](#testing)
- [Usage](#usage)
- [Directory Structure](#directory-structure)
- [Contributing](#contributing)
- [Community](#community)
- [Release Notes](#release-notes)
- [Contributors](#contributors)
- [License](#license)

---

## 🔧 Requirements

### Hardware

**Supported Raspberry Pi Models:**
- ✅ Raspberry Pi 3 (all variants)
- ✅ Raspberry Pi 4 (all RAM sizes)
- ✅ Raspberry Pi 5
- ❌ Pi 1, Pi 2, Pi Zero (insufficient performance)

**Why Pi 3+ Required:**
- Real-time audio processing requires significant CPU
- 40-pin GPIO header (all supported models)
- Minimum 1GB RAM, 2-8GB recommended

**Optional Hardware:**
- GPIO expanders (MCP23017/MCP23008) for additional channels
- USB audio interface for audio-in mode
- Physical buttons for manual control (GPIO 20, 21, 26, 8)
- Arduino/NodeMCU for network client setups

### Software

- **Python:** 3.9 or newer
- **OS:** Raspbian/Raspberry Pi OS (Bullseye or newer)
- **Dependencies:** All available via PyPI (see `requirements.txt`)

---

## 🚀 Quick Start

```bash
# Clone the repository
git clone https://github.com/CheeseMochi/lightshowpi-neo.git
cd lightshowpi-neo

# Set environment variable (add to ~/.bashrc for persistence)
export SYNCHRONIZED_LIGHTS_HOME=$(pwd)
echo "export SYNCHRONIZED_LIGHTS_HOME=$(pwd)" >> ~/.bashrc

# Run the installer (interactive setup)
sudo ./install.sh

# Test your installation
pytest -v

# Run a test show
python py/synchronized_lights.py --playlist config/playlist.playlist
```

---

## 📦 Installation

### Automated Installation (Recommended)

The `install.sh` script handles all dependencies and configuration:

```bash
sudo ./install.sh
```

**What it does:**
- Installs system packages (ALSA, GPIO libraries, etc.)
- Sets up Python virtual environment (optional)
- Installs all Python dependencies from requirements.txt
- Configures audio output
- Sets correct file permissions
- Creates necessary directories

### Manual Installation

<details>
<summary>Click to expand manual installation steps</summary>

#### 1. Install System Dependencies

```bash
sudo apt-get update
sudo apt-get install -y \
    python3-dev \
    python3-pip \
    python3-venv \
    libasound2-dev \
    libatlas-base-dev \
    git
```

#### 2. Create Virtual Environment (Recommended)

```bash
python3 -m venv venv
source venv/bin/activate
```

#### 3. Install Python Dependencies

```bash
pip install -r requirements.txt
```

#### 4. Set Environment Variable

```bash
export SYNCHRONIZED_LIGHTS_HOME=$(pwd)
echo "export SYNCHRONIZED_LIGHTS_HOME=$(pwd)" >> ~/.bashrc
```

#### 5. Configure Audio (if needed)

```bash
# Test audio output
speaker-test -t wav -c 2

# Adjust volume
alsamixer
```

</details>

### Post-Installation Setup

1. **Add music files** to the `music/` directory (MP3, WAV, FLAC supported)
2. **Configure GPIO pins** in `config/defaults.cfg` or `config/overrides.cfg`
3. **Test without hardware** using terminal mode:
   ```bash
   python py/synchronized_lights.py --playlist config/playlist.playlist
   ```

---

## ⚙️ Configuration

### Quick Configuration

Edit `config/overrides.cfg` to customize your setup without modifying defaults:

```ini
[hardware]
# Your GPIO pin configuration (BCM numbering)
gpio_pins = 0,1,2,3,4,5,6,7

[audio_processing]
# Chunk size - larger = better quality, more latency
chunk_size = 4096

# Frequency range for FFT
min_frequency = 20
max_frequency = 15000

[lightshow]
# Mode: playlist, audio-in, or streaming
mode = playlist

# Playlist path
playlist_path = $SYNCHRONIZED_LIGHTS_HOME/music/.playlist
```

### Configuration Files

| File | Purpose |
|------|---------|
| `config/defaults.cfg` | Default settings (don't modify) |
| `config/overrides.cfg` | Your custom settings (override defaults) |
| `config/state.cfg` | Runtime state (auto-generated) |

### Pin Configuration

**BCM Pin Numbering (Physical pins may differ):**

For 8-channel setup:
```ini
[hardware]
gpio_pins = 0,1,2,3,4,5,6,7
pin_modes = pwm,pwm,pwm,pwm,pwm,pwm,pwm,pwm
```

**Using GPIO Expanders:**

```ini
[hardware]
# Enable MCP23017 I2C expander
devices = {"mcp23017":[{"address": 32, "pinBase": 65, "i2c_bus": 1}]}
```

For detailed configuration, see the [Configuration Guide](config/README.md).

---

## 🕹️ Button Manager (Optional)

Control your lightshow with physical buttons!

### Features

- **Skip Button** - Skip to next song
- **Audio Toggle** - Turn audio relay on/off with cooldown
- **Repeat Mode** - Hold button for 5s to enable continuous play
- **Auto-Shutoff** - Audio automatically turns off after timeout

### Configuration

Enable in `config/overrides.cfg`:

```ini
[buttons]
enabled = True
repeat_pin = 20
skip_pin = 21
audio_toggle_pin = 26
outlet_relay_pin = 8
button_cooldown = 5
audio_auto_shutoff = 300
```

### Running Button Manager

```bash
# Start button manager
python py/buttonmanager.py

# Run with debug logging
python py/buttonmanager.py --log-level DEBUG

# Cleanup GPIO resources
python py/buttonmanager.py --cleanup
```

**Note:** Button manager runs as a separate process. For systemd auto-start, see [Button Manager Documentation](BUTTONMANAGER_API.md).

---

## 🧪 Testing

LightShowPi includes a comprehensive test suite with 76+ tests.

### Run All Tests

```bash
# Run all tests
pytest

# Verbose output
pytest -v

# With coverage report
pytest --cov=py --cov-report=html
```

### Run Specific Test Categories

```bash
# Unit tests only
pytest -m unit

# Platform tests (Pi detection)
pytest tests/test_platform.py

# GPIO tests (uses mocks, safe on any system)
pytest tests/test_gpio_adapter.py

# Configuration tests
pytest tests/test_configuration.py
```

### Platform-Specific Tests

Some tests require Linux/ALSA and will skip on macOS/Windows:

```bash
# Tests that skip on macOS:
# - decoder tests (require pyalsaaudio)
# - button manager function tests (require gpiozero)
# These run normally on Raspberry Pi/Linux
```

**Test Coverage:**
- ✅ Platform detection (Pi 3, 4, 5)
- ✅ GPIO adapter (lgpio compatibility layer)
- ✅ Configuration loading and validation
- ✅ FFT audio processing
- ✅ Audio decoder (soundfile wrapper)
- ✅ Button manager logic

For more details, see [Testing Documentation](tests/README.md).

---

## 🎮 Usage

### Basic Playlist Mode

```bash
# Play from default playlist
python py/synchronized_lights.py --playlist config/playlist.playlist

# Play specific song
python py/synchronized_lights.py --file music/song.mp3
```

### Audio-In Mode (Stream from Spotify, etc.)

```bash
# Enable audio-in mode in config
python py/synchronized_lights.py
```

### Advanced Options

```bash
# Use custom configuration
python py/synchronized_lights.py --config overrides2.cfg --playlist myplaylist.playlist

# Set log level
python py/synchronized_lights.py --log DEBUG --playlist config/playlist.playlist

# Terminal mode (no hardware required for testing)
# Set terminal.enabled = True in config
python py/synchronized_lights.py --playlist config/playlist.playlist
```

### Creating Playlists

Edit `config/playlist.playlist`:

```
Song Name 1	/path/to/song1.mp3
Song Name 2	/path/to/song2.mp3
Christmas Song	music/christmas.mp3
```

Format: `<tab>`-separated values (song name, file path)

---

## 📁 Directory Structure

```
lightshowpi/
├── bin/                    # Bash scripts and utilities
├── config/                 # Configuration files
│   ├── defaults.cfg       # Default configuration (don't modify)
│   ├── overrides.cfg      # Your custom settings
│   └── state.cfg          # Runtime state (auto-generated)
├── crontab/               # Cron job examples for auto-start
├── logs/                  # Application logs (auto-generated)
├── music/                 # Your music files
│   └── sample/           # Sample audio files
├── py/                    # Python source code
│   ├── synchronized_lights.py    # Main application
│   ├── buttonmanager.py          # Physical button controls
│   ├── configuration_manager.py  # Config handling
│   ├── gpio_adapter.py           # GPIO compatibility layer
│   ├── fft.py                    # Audio processing
│   └── ...
├── tests/                 # Test suite
│   ├── test_*.py         # Unit and integration tests
│   └── fixtures/         # Test data files
├── tools/                 # Configuration and debugging tools
├── requirements.txt       # Python dependencies
├── install.sh            # Automated installer
└── README.md             # This file
```

---

## 🤝 Contributing

We welcome contributions! Whether it's bug fixes, new features, documentation, or testing.

### How to Contribute

1. **Fork the repository**
2. **Create a feature branch**
   ```bash
   git checkout -b feature/amazing-feature
   ```
3. **Make your changes**
   - Write tests for new functionality
   - Update documentation as needed
   - Follow existing code style
4. **Run tests**
   ```bash
   pytest -v
   ```
5. **Commit your changes**
   ```bash
   git commit -m "Add amazing feature"
   ```
6. **Push to your fork**
   ```bash
   git push origin feature/amazing-feature
   ```
7. **Open a Pull Request**

For detailed guidelines, see [CONTRIBUTING.md](CONTRIBUTING.md).

### Development Setup

```bash
# Clone your fork
git clone https://github.com/CheeseMochi/lightshowpi.git

# Install development dependencies
pip install -r requirements-dev.txt

# Run tests before making changes
pytest -v

# Format code
black py/

# Lint code
flake8 py/
```

---

## 💬 Community

### LightShowPi Neo (This Project)
- 💬 **Discussions:** [GitHub Discussions](../../discussions)
- 🐛 **Issues:** [GitHub Issues](../../issues)
- 💡 **Pull Requests:** [Contribute!](CONTRIBUTING.md)

### Original LightShowPi Community
- 🌐 **Website:** [lightshowpi.org](http://lightshowpi.org/) (official project)
- 💬 **Reddit:** [r/LightShowPi](https://www.reddit.com/r/LightShowPi/)

---

## 📝 Release Notes

### Neo 1.0 (2025) - Initial Release

**🎉 LightShowPi Neo - Modern Python 3 Resurrection**

This is the first official release of **LightShowPi Neo**, a complete modernization fork of the original LightShowPi (v3.21). While based on the original codebase, Neo represents a significant divergence with breaking changes and new direction.

**Hardware:**
- ✅ Raspberry Pi 3, 4, 5 support
- ❌ Dropped Pi 1, 2, Zero (insufficient performance)
- Requires 40-pin GPIO header (all supported models)

**Dependencies:**
- ✅ All dependencies migrated to PyPI
- ✅ Replaced deprecated wiringPi with modern lgpio
- ✅ Replaced git-based decoder with soundfile
- ✅ Removed GPU FFT dependency (CPU sufficient on Pi 3+)
- ✅ Created requirements.txt with pinned versions

**Features:**
- ❌ Removed SMS support (deprecated)
- ❌ Removed Twitter integration (deprecated)
- ❌ Removed FM broadcasting (deprecated)
- ✅ Added button manager with configuration support
- ✅ Added comprehensive test suite (76+ tests)

**Performance:**
- Optimized defaults for Pi 3+ (larger chunk size: 4096)
- Better audio quality with CPU-based numpy FFT
- Simplified platform detection

**Developer Experience:**
- ✅ Full pytest test coverage
- ✅ Modern code formatting and linting
- ✅ GitHub-ready with templates and workflows
- ✅ API documentation for future webapp
- ✅ Updated installation process

**Migration Guide:**
- Update to Pi 3+ hardware
- Run new `install.sh` script
- Update config files (remove SMS/Twitter/FM sections)
- Test with `pytest` before deploying

<details>
<summary>Previous Versions (Click to expand)</summary>

### Version 3.21 (2021/11/11)
- CSV parsing for sequence channel data (Caleb H)
- Per-channel active_low_mode (Filippo B)

### Version 3.20 (2021/11/09)
- Pi 4B support
- Kernel and Debian 11 fixes
- dir_play multiple uploads

### Version 3.11 (2019/12/24)
- Microweb directory play page

### Version 3.10 (2019/12/20)
- Network support for specific IPs
- LED tools addition
- Arduino/NodeMCU support

### Version 3.0 (2019/10/05)
- **Upgrade to Python 3.x**
- Pi 4 support
- Latest Raspbian compatibility

### Version 1.4 (2018/10/16)
- Microweb V3
- RGB LED pixel patterns
- NodeMCU sketch for client devices

### Version 1.3 (2017/10/27)
- RGB LED support
- Microweb UI
- Twitter integration

### Version 1.2 (2016/10/16)
- GPU FFT optimization (3-4x speed)
- Streaming audio support (Pandora, Airplay)
- FM broadcast for Pi 2/3
- Per-song configuration overrides

### Version 1.1 (2014/11/27)
- piFM support
- Audio-in mode
- Expansion card support (MCP23S17, MCP23017)
- RPi B+ support

### Version 1.0 (2014/02/16)
- First stable release

</details>

---

## 👥 Contributors

A huge thanks to all those who have contributed to LightShowPi:

**Core Team:**
- Todd Giles ([@toddgiles](mailto:todd@lightshowpi.org)) - Original creator
- Chris Usey - Major feature development
- Tom Enos - Performance optimizations
- Ken B - Microweb, RGB LEDs, testing

**Contributors:**
- Ryan Jennings, Sean Millar, Scott Driscoll, Micah Wedemeyer
- Chase Cromwell, Bruce Goheen, Paul Dunn, Stephen Burning
- Eric Higdon, Brandon Lyon, Paul Barnett, Anthony Tod
- Brent Reinhard, Caleb H, Filippo B

And many others who have reported issues, tested features, and shared their amazing light shows!

---

## 📄 License

All files are released under the **BSD License**. See [LICENSE](LICENSE) for details.

We ask that you contribute back any improvements you make so the whole community can benefit!

---

## 🌟 Show Your Support

If you find this project useful:
- ⭐ Star this repository
- 🐛 Report bugs and suggest features via [Issues](../../issues)
- 💡 Contribute improvements via [Pull Requests](../../pulls)

---

**Made with ❤️ for the holiday season**
