# LightShowPi Neo - Release Notes

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
git clone https://github.com/YOUR_USERNAME/lightshowpi-neo.git
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

### High Priority
- [ ] **Button manager API integration** - Physical buttons don't control API yet
- [ ] **Public web hosting** - Website currently only runs locally
- [ ] **Control plane architecture** - Remote control infrastructure
- [ ] **Test mode** - Individual channel control for testing
- [ ] **Song upload** - Upload music files via web UI
- [ ] **User management** - Multiple users, permissions
- [ ] **Systemd service** - Auto-start on boot

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

- Button manager runs independently, not integrated with API
- Web UI must be accessed via local network (no public hosting yet)
- Error messages could be more user-friendly
- No progress indicator for long-running operations
- Schedule conflicts not prevented (can have overlapping schedules)
- No password change functionality in UI yet

## Roadmap to 1.0

- **v0.9.1**: Bug fixes, button manager integration
- **v0.9.5**: Public hosting, control plane, test mode
- **v1.0.0**: Feature-complete, production-ready
