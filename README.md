# LightShowPi Neo

> Modern Python 3 resurrection of LightShowPi for Raspberry Pi 3+

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: BSD](https://img.shields.io/badge/License-BSD-green.svg)](LICENSE)
[![Pi 3+](https://img.shields.io/badge/Raspberry%20Pi-3%2B-red.svg)](https://www.raspberrypi.org/)
[![Neo](https://img.shields.io/badge/LightShowPi-Neo-brightgreen.svg)](FORK.md)
[![Tests](https://img.shields.io/badge/tests-passing-brightgreen.svg)](#testing)

**LightShowPi Neo** is a modernized fork of the original LightShowPi that synchronizes Christmas light displays to music on Raspberry Pi. Rebuilt for Python 3.9+, Pi 3/4/5, and pure PyPI dependencies.

**Latest Version:** v0.9.0 Beta 1 (December 2025) • [See what's different from original →](FORK.md)

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
- Physical buttons for manual control
- Arduino/NodeMCU for network client setups

### Software

- **Python:** 3.9 or newer
- **OS:** Raspbian/Raspberry Pi OS (Bookworm or newer)
- **Dependencies:** All available via PyPI (see `requirements.txt`)

---

## 🚀 Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/CheeseMochi/lightshowpi-neo.git
cd lightshowpi-neo

# 2. Install system dependencies
sudo ./install.sh

# 3. Install Miniconda (if not already installed)
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-aarch64.sh
bash Miniconda3-latest-Linux-aarch64.sh
source ~/.bashrc

# 4. Create conda environment
conda env create -f environment.yml
conda activate lightshowpi-neo

# 5. Link system lgpio to conda environment
./bin/link_lgpio.sh

# 6. Set environment variable
export SYNCHRONIZED_LIGHTS_HOME=$(pwd)
echo "export SYNCHRONIZED_LIGHTS_HOME=$(pwd)" >> ~/.bashrc

# 7. Verify installation
pytest -v

# 8. Run your first lightshow!
sudo $(which python) py/synchronized_lights.py
```

For detailed installation options (venv vs conda), see [Installation](#installation) section below.


---

## 📦 Installation

Installation consists of two main steps:
1. **Install system dependencies** (required for all users)
2. **Set up Python environment** (choose conda OR venv)

---

### Step 1: Install System Dependencies (Required)

**All users must complete this step first**, regardless of whether you use conda or venv.

#### Option A: Automated (Recommended)

```bash
sudo ./install.sh
```

This script:
- Checks for supported Raspberry Pi hardware (Pi 3/4/5)
- Installs python3-lgpio, ALSA libraries, and other system packages
- Displays next steps for Python environment setup

#### Option B: Manual

```bash
sudo apt-get update
sudo apt-get install -y \
    python3-lgpio \
    libasound2-dev \
    libasound2 \
    alsa-utils \
```

---

### Step 2: Set Up Python Environment (Choose One)

After installing system dependencies, choose **either** Method A (conda) **or** Method B (venv).

<details open>
<summary><b>Method A: Using Conda (Recommended)</b></summary>

Conda provides better dependency management and Python version control.

#### 1. Install Miniconda (if not already installed)

```bash
# Download Miniconda for ARM64 (Raspberry Pi)
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-aarch64.sh

# Install Miniconda
bash Miniconda3-latest-Linux-aarch64.sh

# Follow prompts, then restart your shell or run:
source ~/.bashrc
```

#### 2. Create Conda Environment

```bash
# Create environment from environment.yml
conda env create -f environment.yml

# Activate the environment
conda activate lightshowpi-neo
```

This installs all Python dependencies automatically (numpy, pydantic, fastapi, etc.).

#### 3. Link System lgpio to Conda Environment

Since lgpio is installed system-wide via apt, symlink it into your conda environment:

```bash
./bin/link_lgpio.sh
```

**Verify it works:**
```bash
python -c "import lgpio; print('lgpio version:', lgpio.__version__)"
```

<details>
<summary><b>Troubleshooting: Python version mismatch</b></summary>

If the symlink fails (e.g., system Python 3.11 vs conda Python 3.13), compile lgpio for your conda Python:

```bash
# Install lgpio development libraries
sudo apt-get install -y liblgpio-dev liblgpio1

# Install lgpio via pip in conda environment
LDFLAGS="-L/usr/lib/aarch64-linux-gnu -L/usr/lib" \
CFLAGS="-I/usr/include" \
pip install lgpio

# Verify
python -c "import lgpio; print('lgpio version:', lgpio.__version__)"
```

</details>

#### 4. Set Environment Variable

```bash
export SYNCHRONIZED_LIGHTS_HOME=$(pwd)
echo "export SYNCHRONIZED_LIGHTS_HOME=$(pwd)" >> ~/.bashrc
```

#### 5. Verify Installation

```bash
pytest -v
```

</details>

<details>
<summary><b>Method B: Using venv (Alternative)</b></summary>

Standard Python virtual environment without conda.

#### 1. Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
```

#### 2. Install Python Dependencies

```bash
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

#### 3. Link System lgpio to venv

Since lgpio is installed system-wide via apt, symlink it into your venv:

```bash
./bin/link_lgpio.sh
```

**Verify it works:**
```bash
python -c "import lgpio; print('lgpio version:', lgpio.__version__)"
```

**Note:** If symlink fails due to Python version mismatch, you'll need to compile lgpio from source or use conda instead.

#### 4. Set Environment Variable

```bash
export SYNCHRONIZED_LIGHTS_HOME=$(pwd)
echo "export SYNCHRONIZED_LIGHTS_HOME=$(pwd)" >> ~/.bashrc
```

#### 5. Verify Installation

```bash
pytest -v
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

### GPIO Permissions and Sudo

**IMPORTANT:** GPIO access requires root permissions on Raspberry Pi. You have two options:

#### Option 1: Run with sudo (Recommended for conda users)

When using conda/miniconda environments, the easiest way is to run with `sudo` while preserving your environment:

```bash
# Activate conda environment first
conda activate lightshowpi-neo

# Run with sudo, sourcing the conda activation
sudo bash -c "source /home/$(whoami)/miniconda3/bin/activate lightshowpi-neo && python py/synchronized_lights.py"

# Or use this shorter form for the active environment
sudo $(which python) py/synchronized_lights.py
```

#### Option 2: Add user to gpio group (Alternative)

If you don't want to use sudo, add your user to the `gpio` group:

```bash
sudo usermod -a -G gpio $USER
# Logout and login again for group membership to take effect
```

**Note:** Even with gpio group membership, some operations may still require sudo depending on your system configuration.

**Testing GPIO with sudo:**
```bash
# Test GPIO hardware
sudo $(which python) tests/test_gpio_hardware.py

# Test hardware controller
sudo $(which python) py/hardware_controller.py --test
```

### Basic Playlist Mode

```bash
# Play from default playlist (configured in config file)
python py/synchronized_lights.py

# Play from a specific playlist file
python py/synchronized_lights.py --playlist config/playlist.playlist

# Play specific song
python py/synchronized_lights.py --file music/song.mp3
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

### v0.9.0 Beta 1 (December 2025) - Beta Release

**🎉 LightShowPi Neo - Modern Python 3 Resurrection**

This is the first **beta release** of **LightShowPi Neo**, a modernization fork of the original LightShowPi for Python 3.13 and Raspberry Pi 3/4/5. Core functionality works, API and web UI are operational, but many planned features are not yet implemented.

**⚠️ Beta Status:** This release is functional but not feature-complete. See [RELEASE_NOTES.md](RELEASE_NOTES.md) for the complete roadmap to v1.0.

**Core System:**
- ✅ Python 3.13 Support - Full compatibility with latest Python
- ✅ Raspberry Pi 5 Support - Works on Pi 3, 4, and 5
- ✅ Modern GPIO - Uses lgpio instead of deprecated RPi.GPIO/wiringPi
- ✅ Pure PyPI Dependencies - All dependencies installable via pip/conda
- ✅ Conda Environment Support - First-class conda/miniconda support

**NEW: API Backend & Web Frontend:**
- ✅ FastAPI REST API - Modern async API for remote control
- ✅ React 18 UI - Modern single-page application
- ✅ Real-time Dashboard - Live status updates
- ✅ Schedule Manager - Visual interface for creating/editing schedules
- ✅ JWT Authentication - Secure token-based authentication

**Technical Improvements:**
- ✅ All dependencies migrated to PyPI
- ✅ Replaced deprecated wiringPi with modern lgpio
- ✅ Replaced git-based decoder with soundfile
- ✅ Updated Pydantic v2, PyJWT, Python 3.13 compatibility
- ✅ Comprehensive test suite (76+ tests)

**What's Missing (Planned for v1.0):**
- Button manager API integration
- Public web hosting
- Control plane architecture
- Test mode for individual channels
- Song upload via web UI
- User management
- Systemd service

For complete release notes and roadmap, see [RELEASE_NOTES.md](RELEASE_NOTES.md)

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

## 👥 LightshowPi Contributors

A huge thanks to all those who have contributed to the original LightShowPi:

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
