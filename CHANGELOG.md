# Changelog

All notable changes to LightShowPi Neo will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Button API integration**: `api/routers/buttons.py` and `api/services/button_manager.py`
  expose skip / audio toggle / repeat / outlet controls over the REST API; the
  physical button manager (`py/buttonmanager.py`) can drive the show through the API.
- **Virtual buttons** in the web dashboard mirroring the physical buttons.
- **Ambient mode**: lights on steady with no music (`--mode ambient`), selectable
  per schedule and from the dashboard.
- **Schedule modes**: each schedule can start the show in `playlist`, `ambient`,
  `audio-in` or `stream-in` mode (new `schedule.mode` column, migrated automatically).
- **Experimental sACN networking** (`py/networking_sacn.py`) for server/client shows,
  with a JSON control plane for channel overrides.
- **Client/server scripts**: `bin/start_lightshow_server`, `bin/stop_lightshow_server`,
  `bin/start_lightshow_client`, `bin/stop_lightshow_client`, `bin/start_buttons.sh`,
  `bin/stop_buttons.sh`.
- **systemd units** for the API and button manager (`systemd/`), with setup docs in
  `systemd/README.md`. Units are not installed automatically by `install.sh`.
- **`environment-dev.yml`**: conda environment for macOS/Linux development (no GPIO/ALSA).
- API and button service tests (`tests/test_button_endpoints.py`,
  `tests/test_button_manager_service.py`).

### Changed
- Audio decoding switched from **soundfile** to **audioread**, supporting more file formats.
- `requirements.txt`, `requirements-dev.txt` and `environment.yml` trued up so the Pi
  and development installs match; docs updated accordingly.
- Configuration loading uses fallbacks for `[lightshow]` keys, so older override files
  no longer crash on missing options.
- Missing or unreadable playlist files now produce a clear error message.

### Fixed
- Raspberry Pi 5 install issues (`install.sh`, `environment.yml`, `bin/link_lgpio.sh`).
- Button manager bug fixes and test fixes on macOS.
- `requests` is now installed as a dependency.

## [0.9.0] - 2025-12-14

First beta of **LightShowPi Neo**, a modernization fork of the original LightShowPi (v3.21)
for Python 3.13 and Raspberry Pi 3/4/5. See [RELEASE_NOTES.md](RELEASE_NOTES.md) for the
full release notes.

### Added
- **FastAPI REST API** with JWT authentication, SQLite database, and lightshow
  start/stop/skip endpoints.
- **React web UI** with dashboard and schedule manager.
- **APScheduler-based scheduling** with per-day schedules and upcoming events.
- **Button manager** (`py/buttonmanager.py`) with `[buttons]` configuration: skip,
  audio toggle, repeat mode, outlet relay, cooldown and auto-shutoff timers.
- **pytest test suite** covering platform detection, GPIO adapter, configuration,
  FFT, audio decoding and the button manager, with macOS-friendly mocking.
- Documentation: README, CONTRIBUTING.md, FORK.md, LICENSE, GitHub issue/PR templates.

### Changed
- **GPIO**: lgpio via a compatibility adapter with WiringPi → BCM pin translation.
- **Audio processing**: CPU numpy FFT (GPU FFT removed); `audioop` replaced with numpy.
- **Audio decoding**: git-based decoder replaced with soundfile from PyPI
  (later replaced by audioread, see [Unreleased]).
- **Platform detection** simplified and fixed for newer Raspberry Pi OS cpuinfo formats.
- Default chunk size increased from 2048 to 4096 for Pi 3+.
- Dependencies updated for Python 3.13 (Pydantic v2, PyJWT).
- Configuration class no longer takes an SMS parameter.

### Removed
- **SMS control**: outdated, with security concerns.
- **Twitter integration**: API changes and limited usefulness.
- **FM broadcasting**: legal restrictions and liability concerns.
- **GPU FFT** and `rpi_audio_levels`: CPU FFT is sufficient on Pi 3+.
- **wiringPi**: deprecated, replaced by lgpio.
- **Git-based audio decoder**.
- **microweb** web interface: replaced by the React UI.
- **Raspberry Pi 1/2/Zero support**: insufficient performance for real-time FFT.

### Fixed
- Button manager: `Configuration(True)` SMS parameter bug, hardcoded home path,
  print statements replaced with logging.
- Configuration loader absolute path handling.
- Network cleanup race condition on shutdown.
- Playlist path environment variable expansion.
- JWT exception handling, schedule JSON parsing, frontend error display, API CORS/routing.

### Migration from original LightShowPi
- **Hardware**: Raspberry Pi 3 or newer (40-pin header) is required.
- **Installation**: run the new `install.sh`, or create the conda environment from
  `environment.yml`.
- **Configuration**: remove `[sms]`, Twitter and FM sections from `overrides.cfg`;
  add a `[buttons]` section if using the button manager and an `[api]` section for the API.
- **Verify**: run `pytest` before deploying and check the logs for deprecated features.

See [FORK.md](FORK.md) for a detailed comparison with the original LightShowPi.

---

## Original LightShowPi releases (2013-2021)

For the history of the original project this fork is based on, see the
[original LightShowPi repository](https://bitbucket.org/togiles/lightshowpi/).

[Unreleased]: https://github.com/CheeseMochi/lightshowpi-neo/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/CheeseMochi/lightshowpi-neo/releases/tag/v0.9.0
