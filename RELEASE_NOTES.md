# LightShowPi Neo - Release Notes

## Version 0.10.0 (in progress)

Draft. See the [Unreleased] section of [CHANGELOG.md](CHANGELOG.md).

### ✨ Highlights

- **Songs page**: reorder, rename, disable and play songs from the web UI, and
  edit each song's settings (always on/off channels, attenuation, pre/postshow)
  without touching its `.cfg` file.
- **Sequence variants per song**: pick FFT, the song's CSV, one composed
  variant, or rotate/randomize across several, so each play of a song can look
  different. Copy research variants in with `tools/install_sequences.py`.
  Songs you don't configure play exactly as before.

### 🐛 Notable Fixes

- LED strips and matrices over sACN (e.g. an ESPixelStick) light up again: they
  no longer need BiblioPixel, which can't be installed on Neo. All matrix
  patterns draw exactly as before. SPI and SERIAL LED configs still need
  BiblioPixel and are skipped with an error in the log. After the
  season the LED sender moves to the maintained `sacn` library.
- Multi-Pi shows: client LEDs now follow the show, clients with fewer channels
  no longer crash, and a client dropping off Wi-Fi no longer crashes the server.
- A blank line in the playlist no longer stops the show.
- `audio-in` mode drives the lights again.
- `bin/stop_music_and_lights` stops a show started from root's crontab again
  (it used to kill only the current song).

### ⚠️ Upgrade Notes

- `light_delay` keeps its old timing by default. To use true seconds, set
  `light_delay_legacy_math = False` under `[lightshow]` in `overrides.cfg` and
  re-tune `light_delay` (expect roughly 4x the old value for stereo files).
- `sacn_server` / `sacn_client` networking now works (Pi-to-Pi over E1.31);
  the default GPIO `universe_start` is 100.
- **The API needs `JWT_SECRET_KEY`** (32+ characters) and, on a fresh database,
  `ADMIN_PASSWORD`; there is no `admin123` default any more. Ansible-provisioned
  Pis already have both in `/etc/lightshowpi/api.env`. Manual installs: see
  `systemd/README.md`. Development: `LSP_DEV=1`.
- `/api/lightshow/status` now needs a login. Anything that polled it without a
  token must log in first (the web UI and the button manager already do).
- Changing the admin password breaks the button manager's API login until
  `/etc/lightshowpi/buttons.env` is updated too.
- Ambient mode and `set_array` scripts now light LED strips as well as GPIO.

## Version 0.9.1 - Beta 2 (September 2026)

Second beta. It integrates the button manager with the API, adds live status and
schedule suspend/resume, and fixes several bugs that stopped API features from working.
See [CHANGELOG.md](CHANGELOG.md) for the full list.

### ✨ Highlights

- **Buttons through the API**: physical buttons (`py/buttonmanager.py`) and new virtual
  buttons in the dashboard now drive the show through `/api/buttons/*`.
- **Live status**: the dashboard shows the song title, elapsed/total time and the
  next scheduled event.
- **Pause vs Stop**: *Pause* stops until the next scheduled start. *Stop* suspends
  the schedule until you press *Start* again.
- **Ambient mode** and per-schedule modes (playlist, ambient, audio-in, stream-in).
- **systemd units** for the API and button manager (`systemd/`, installed manually).
- **Experimental sACN** server/client networking.
- **audioread** decoder (more formats) and Raspberry Pi 5 install fixes.

### 🐛 Notable Fixes

- The button manager's API mode never connected (wrong port and route prefix).
- Concurrent writes to `config/state.cfg` could drop each other's keys.
- `/api/lightshow/status` returned `"0"`/`"1"` as the song name, and could fail
  during start/stop.
- `/api/health` always reported idle with zero uptime.

### ⚠️ Upgrade Notes

- **Button manager password**: the hardcoded `admin`/`admin123` login is gone. Set
  `LIGHTSHOWPI_API_PASSWORD` (e.g. in `/etc/lightshowpi/buttons.env`) or
  `[buttons] api_password` in `config/overrides.cfg`. Without one, `--mode auto`
  runs in direct mode and `--mode api` exits. See [systemd/README.md](systemd/README.md).
- **Virtual buttons** need `[buttons] enabled = True`.
- **Button endpoints** moved from `/buttons/*` to `/api/buttons/*`.
- The `schedule.mode` database column is added automatically on first start.

---

## Version 0.9.0 - Beta 1 (December 2025)

### 🚧 Beta Release - Functional but Not Feature-Complete

This is the first **beta release** of **LightShowPi Neo**, a modernization of the original LightShowPi project for Python 3.13 and Raspberry Pi 3/4/5.

**Status**: Core functionality works, API and web UI are operational, but many planned features are not yet implemented. See "What's Missing" section below.

### ✨ New Features

#### Core System
- **Python 3.13 Support**: Full compatibility with latest Python
- **Raspberry Pi 5 Support**: Works on Pi 3, 4, and 5 (Pi 1, 2, Zero not supported)
- **Modern GPIO**: Uses lgpio instead of deprecated RPi.GPIO/wiringPi
- **Pure PyPI Dependencies**: All dependencies installable via pip/conda
- **Conda Environment Support**: First-class conda/miniconda support

#### API Backend (NEW!)
- **FastAPI REST API**: Modern async API for remote control
- **JWT Authentication**: Secure token-based authentication
- **Real-time Status**: WebSocket-ready status updates
- **Schedule Management**: Full CRUD API for automatic schedules
- **Database**: SQLite database for schedules and settings

#### Web Frontend (NEW!)
- **React 18 UI**: Modern single-page application
- **Real-time Dashboard**: Live status updates every 2 seconds
- **Remote Control**: Start/stop/skip lightshow from any device
- **Schedule Manager**: Visual interface for creating/editing schedules
- **Responsive Design**: Works on desktop, tablet, and mobile
- **Dark Theme**: Christmas-themed dark UI

#### Scheduling System (NEW!)
- **APScheduler Integration**: Cron-style automatic scheduling
- **Multiple Schedules**: Support for multiple daily schedules
- **Day Selection**: Choose which days each schedule runs
- **Auto Start/Stop**: Automatic lightshow control
- **Upcoming Events**: View next scheduled events

### 🔧 Technical Improvements

#### Platform Detection
- Fixed /proc/cpuinfo parsing for newer Raspberry Pi OS versions
- Supports both old ("Hardware") and new ("Model") cpuinfo formats
- Accurate Pi 3/4/5 detection

#### GPIO System
- WiringPi to BCM pin number translation
- Software PWM via lgpio
- GPIO expander support (MCP23017, MCP23008, etc.)
- Proper cleanup on shutdown

#### Audio Processing
- Replaced removed `audioop` module with numpy
- ALSA compatibility (via pyalsaaudio with alsa-lib)
- No audio quality degradation

#### Dependencies
- **Pydantic v2**: Updated all validators and field definitions
- **PyJWT**: Fixed exception handling for newer versions
- **Python 3.13**: Replaced all deprecated APIs

### 📚 Documentation

- Complete README with installation instructions
- API documentation (auto-generated via FastAPI)
- Testing guide with 76+ unit tests
- GPIO permissions and sudo usage guide
- Troubleshooting section

### 🧪 Testing

- 76+ unit and integration tests
- Platform compatibility tests
- GPIO hardware tests (with loopback support)
- Pydantic v2 compatibility tests
- Python 3.13 compatibility tests

### 🐛 Bug Fixes

- Fixed network cleanup race condition
- Fixed playlist path environment variable expansion
- Fixed JWT exception handling
- Fixed schedule JSON parsing
- Fixed frontend error display
- Fixed API CORS and routing

### 🔒 Security

- JWT token-based authentication
- bcrypt password hashing
- Secure default password warning
- CORS middleware for web frontend

### 📦 Installation Methods

1. **Conda (Recommended)**: Full conda environment with all dependencies
2. **venv**: Standard Python virtual environment
3. **System**: Direct installation (not recommended)

### 🚀 Quick Start

```bash
# Clone repository
git clone https://github.com/CheeseMochi/lightshowpi-neo.git
cd lightshowpi-neo

# Install dependencies
conda env create -f environment.yml
conda activate lightshowpi-neo

# Install system lgpio
sudo apt-get install python3-lgpio
./bin/link_lgpio.sh

# Configure
cp config/defaults.cfg config/overrides.cfg
nano config/overrides.cfg

# Run
sudo $(which python) py/synchronized_lights.py
```

### 🌐 API/Web UI Quick Start

```bash
# Start API backend
sudo $(which python) -m api.main

# Start web frontend (separate terminal)
cd web && npm install && npm run dev

# Access at http://YOUR_PI_IP:3000
```

### ⚙️ Configuration

- **API**: Edit `config/overrides.cfg`, add `[api]` section
- **GPIO Pins**: Uses wiringPi numbering (0-7) with automatic BCM translation
- **Schedules**: Manage via web UI or API
- **Network**: Client/server mode for synchronized multi-Pi shows

### 🙏 Credits

**Original LightShowPi**: Todd Giles, Tom Enos, Chris Usey, and all contributors

**Neo Modernization**: Complete rewrite for Python 3.13 compatibility

### 📝 License

BSD License (same as original LightShowPi)

---

## ⚠️ What's Missing (Planned for 1.0)

### Done since 0.9.0
- [x] **Button manager API integration** - Physical and virtual buttons control the show through the API
- [x] **Systemd units** - Provided in `systemd/` (manual install; `install.sh` does not set up auto-start yet)

### High Priority
- [ ] **Public web hosting** - Website currently only runs locally
- [ ] **Control plane architecture** - Remote control infrastructure
- [ ] **Test mode** - Individual channel control for testing
- [ ] **Song upload** - Upload music files via web UI
- [ ] **User management** - Multiple users, permissions
- [ ] **Auto-start on boot** - `install.sh` should install and enable the systemd units

### Medium Priority
- [ ] **Analytics dashboard** - View usage stats, play history
- [ ] **Multi-client UI** - Register and manage multiple Pis
- [ ] **WebSocket support** - Real-time updates without polling
- [ ] **Cloud relay mode** - Control from anywhere
- [ ] **Mobile app** - Native iOS/Android apps
- [ ] **Playlist management** - Create/edit playlists in UI
- [ ] **Audio visualization** - Real-time FFT display

### Low Priority
- [ ] **Configuration UI** - Edit config through web interface
- [ ] **Backup/restore** - Backup schedules and settings
- [ ] **Firmware updates** - Update LightShowPi through UI
- [ ] **Multi-language** - Internationalization support

## Known Issues

- systemd units must be installed manually (see `systemd/README.md`)
- Web UI must be accessed via local network (no public hosting yet)
- Error messages could be more user-friendly
- No progress indicator for long-running operations
- Schedule conflicts not prevented (can have overlapping schedules)
- No password change functionality in UI yet

## Roadmap to 1.0

- **v0.9.1**: Button manager API integration and virtual buttons, ambient mode and
  per-schedule modes, experimental sACN networking, audioread decoder, systemd units,
  Pi 5 install fixes, live song title/elapsed time in status, schedule suspend/resume
  ("stop" disables the schedule until the next start), configurable button API credentials
- **v0.9.5**: Test mode, multi-client support, public hosting, control plane
- **v1.0.0**: Feature-complete, production-ready
