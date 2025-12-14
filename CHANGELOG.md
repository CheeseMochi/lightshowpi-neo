# Changelog

All notable changes to LightShowPi Neo will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Neo 1.0] - 2026-01-XX

### 🎉 Initial Release - LightShowPi Neo

This is the first official release of **LightShowPi Neo**, a complete modernization fork of the original LightShowPi (v3.21). While based on the original codebase, Neo represents a significant divergence with breaking changes and a new direction.

### Added

#### Core Features
- ✅ **Button Manager** - Physical button controls with configuration support
  - Skip button (GPIO 21)
  - Audio toggle button (GPIO 26)
  - Repeat mode button (GPIO 20)
  - Outlet relay control (GPIO 8)
  - Configurable cooldown protection
  - Auto-shutoff timers
  - 20+ unit tests

#### Testing & Quality
- ✅ **Comprehensive Test Suite** - 76+ tests covering:
  - Platform detection (Pi 3, 4, 5)
  - GPIO adapter functionality
  - Configuration loading and validation
  - FFT audio processing
  - Audio decoder
  - Button manager logic
- ✅ **pytest Framework** - Modern testing with fixtures, markers, and mocking
- ✅ **Cross-platform Tests** - Tests run on macOS/Linux with proper mocking
- ✅ **Code Coverage** - Coverage reports with pytest-cov

#### Dependencies & Compatibility
- ✅ **100% PyPI Dependencies** - All packages installable via pip
- ✅ **Modern GPIO Library** - Replaced deprecated wiringPi with lgpio
- ✅ **Soundfile Audio Decoder** - Replaced git-based decoder with PyPI package
- ✅ **requirements.txt** - Pinned versions for reproducible builds
- ✅ **Python 3.9+ Only** - Leverages modern Python features
- ✅ **Raspberry Pi 3/4/5** - Optimized for current Pi models

#### Documentation
- ✅ **Comprehensive README** - Installation, configuration, usage guides
- ✅ **CONTRIBUTING.md** - Contribution guidelines and coding standards
- ✅ **FORK.md** - Detailed explanation of fork relationship
- ✅ **LICENSE** - Dual copyright (original + Neo)
- ✅ **CHANGELOG.md** - This file
- ✅ **BUTTONMANAGER_API.md** - API documentation for web integration
- ✅ **GitHub Issue Templates** - Bug reports and feature requests
- ✅ **Pull Request Template** - Standardized PR process

#### Development Experience
- ✅ **black** - Code formatting
- ✅ **flake8** - Linting
- ✅ **mypy** - Type checking support
- ✅ **GitHub Templates** - Issues, PRs, discussions

### Changed

#### Architecture
- 🔄 **Configuration System** - Removed SMS parameter from Configuration class
- 🔄 **GPIO Adapter** - Created compatibility layer for lgpio
- 🔄 **Audio Processing** - CPU-based numpy FFT (GPU FFT removed)
- 🔄 **Platform Detection** - Simplified Pi version detection
- 🔄 **Chunk Size** - Increased default from 2048 to 4096 for Pi 3+

#### Hardware Support
- 🔄 **Raspberry Pi 3+ Only** - Dropped support for Pi 1, 2, Zero
- 🔄 **40-pin GPIO Header** - All supported models use this standard

### Removed

#### Deprecated Features
- ❌ **SMS Control** - Outdated communication method with security concerns
- ❌ **Twitter Integration** - API changes and limited usefulness
- ❌ **FM Broadcasting** - Legal restrictions and liability concerns
- ❌ **GPU FFT** - Deprecated library, CPU sufficient on Pi 3+
- ❌ **Pi 1/2/Zero Support** - Insufficient performance for real-time FFT

#### Dependencies
- ❌ **wiringPi** - Replaced with lgpio (wiringPi deprecated)
- ❌ **Git-based Decoder** - Replaced with soundfile from PyPI
- ❌ **rpi_audio_levels** - GPU FFT no longer needed

### Fixed

#### Button Manager
- 🐛 Fixed `Configuration(True)` SMS parameter bug
- 🐛 Fixed hardcoded path (`/home/scroce/lightshowpi/`)
- 🐛 Replaced all print statements with proper logging
- 🐛 Added configuration integration via `[buttons]` section

#### Configuration
- 🐛 Fixed absolute path handling in configuration loader
- 🐛 Fixed test failures with temp file paths

#### Testing
- 🐛 Fixed macOS compatibility with gpiozero mocking
- 🐛 Fixed cooldown test timing issues
- 🐛 Added proper skipif decorators for platform-specific tests

### Performance

- ⚡ **Optimized for Pi 3+** - Better defaults for modern hardware
- ⚡ **Larger Chunk Size** - 4096 samples for better quality
- ⚡ **Simplified Platform Detection** - Faster startup

### Security

- 🔒 **Removed SMS** - Eliminated SMS-related security concerns
- 🔒 **Updated Dependencies** - All packages from maintained PyPI sources

### Migration from Original LightShowPi

#### Hardware Requirements
- **Upgrade to Pi 3+** - Pi 1/2/Zero no longer supported
- **40-pin GPIO** - All supported models have this

#### Installation
- **Run new install.sh** - Updated installation process
- **Update requirements.txt** - All PyPI dependencies

#### Configuration
- **Remove SMS sections** - No longer needed
- **Remove Twitter sections** - No longer supported
- **Remove FM sections** - No longer available
- **Add [buttons] section** - If using button manager

#### Testing
- **Run pytest** - Verify everything works before deploying
- **Check logs** - Ensure no deprecated features in use

See [FORK.md](FORK.md) for detailed comparison with original LightShowPi.

---

## Future Releases

### Planned for Neo 1.1
- FastAPI web backend
- React frontend
- WebSocket real-time updates
- REST API for remote control
- Docker support

### Under Consideration
- Mobile app (iOS/Android)
- Cloud integration
- Multi-zone synchronization
- Advanced LED patterns
- MQTT support

---

## Version History

### Neo Releases (2025+)
- **Neo 1.0** (2025-01-XX) - Initial release

### Original LightShowPi Releases (2013-2021)

For the complete history of the original LightShowPi project that this fork is based on, see:
- [Original LightShowPi Repository](https://bitbucket.org/togiles/lightshowpi/)
- [Original Release Notes](README.md#release-notes)

Notable original releases that influenced Neo:
- **v3.21** (2021-11-11) - CSV parsing, per-channel active_low_mode
- **v3.20** (2021-11-09) - Pi 4B support
- **v3.0** (2019-10-05) - Python 3.x upgrade
- **v1.4** (2018-10-16) - RGB LED patterns, microweb V3
- **v1.3** (2017-10-27) - RGB LED support, Twitter integration
- **v1.2** (2016-10-16) - GPU FFT, FM broadcast
- **v1.0** (2014-02-16) - First stable release

---

## How to Update

### From Neo 1.0 to Future Releases

```bash
# Backup your configuration
cp config/overrides.cfg config/overrides.cfg.bak

# Pull latest changes
git pull origin main

# Update dependencies
pip install -r requirements.txt --upgrade

# Run tests
pytest -v

# Check CHANGELOG for breaking changes
```

### From Original LightShowPi to Neo 1.0

See the [Migration Guide in FORK.md](FORK.md#migration-from-original-lightshowpi) for detailed steps.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on reporting bugs, suggesting features, and submitting pull requests.

---

**Format Guidelines:**
- **Added** - New features
- **Changed** - Changes to existing functionality
- **Deprecated** - Soon-to-be removed features
- **Removed** - Removed features
- **Fixed** - Bug fixes
- **Security** - Security improvements
- **Performance** - Performance improvements

[Neo 1.0]: https://github.com/CheeseMochi/lightshowpi-neo/releases/tag/v1.0
