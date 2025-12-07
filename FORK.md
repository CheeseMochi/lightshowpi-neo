# Fork Information

## LightShowPi Neo vs. Original LightShowPi

**LightShowPi Neo is a modernized, independent fork** of the original LightShowPi project. This document explains the relationship, differences, and why you might choose one over the other.

---

## 🌳 Project Lineage

```
LightShowPi (togiles)
├── 2013: Created by Todd Giles
├── 2013-2021: Active development, 30+ contributors
├── Features: SMS, Twitter, FM, Pi 1/2 support, GPU FFT
│
└── Fork: slowandlow/lightshowpi (Bitbucket)
    ├── ~2018: Personal fork with custom features
    ├── Added: Button manager, custom integrations
    ├── Status: Private modifications
    │
    └── LightShowPi Neo (This Project)
        ├── 2025: Complete modernization
        ├── Breaking changes from original
        └── Independent future development
```

---

## 🔄 Relationship to Original

### We Are:
- ✅ **Grateful** - Built on the shoulders of Todd Giles' excellent work
- ✅ **Compliant** - Fully BSD-licensed, properly attributed
- ✅ **Independent** - Autonomous development and decision-making
- ✅ **Compatible** - Similar concepts, different implementation

### We Are NOT:
- ❌ **Official** - Not endorsed by or affiliated with original team
- ❌ **Upstream** - Changes here don't go to original project
- ❌ **Replacement** - Both projects serve different use cases
- ❌ **Competitive** - Different target audiences and goals

---

## ⚖️ Key Differences

| Feature | Original LightShowPi | LightShowPi Neo |
|---------|---------------------|-----------------|
| **Python** | 3.x (older versions) | 3.9+ only |
| **Pi Support** | Pi 1, 2, 3, 4 | Pi 3, 4, 5 only |
| **Dependencies** | Some from git repos | 100% PyPI |
| **GPIO Library** | wiringPi (deprecated) | lgpio (modern) |
| **Audio Decoder** | Custom git package | soundfile (PyPI) |
| **FFT** | GPU-based (rpi_audio_levels) | CPU numpy |
| **SMS Control** | ✅ Yes | ❌ Removed |
| **Twitter** | ✅ Yes | ❌ Removed |
| **FM Broadcast** | ✅ Yes | ❌ Removed |
| **Button Manager** | ❌ No | ✅ Yes (enhanced) |
| **Test Suite** | Minimal | 76+ tests |
| **Web API** | Microweb (Flask) | FastAPI (planned) |
| **Active Development** | Stable (2021) | Active (2025+) |

---

## 🎯 Which Should You Use?

### Choose **Original LightShowPi** if you:
- Have a Pi 1 or Pi 2
- Need SMS control features
- Want Twitter integration
- Need FM broadcasting
- Prefer stable, battle-tested code
- Want official community support

**Get it here:** https://bitbucket.org/togiles/lightshowpi/

### Choose **LightShowPi Neo** if you:
- Have Pi 3, 4, or 5
- Want modern Python 3.9+ codebase
- Prefer pure PyPI dependencies
- Need physical button controls
- Want comprehensive testing
- Prefer active development
- Plan to contribute new features

**Get it here:** https://github.com/CheeseMochi/lightshowpi-neo

---

## 🚫 What We Removed and Why

### SMS Control (Removed)
**Why:** Outdated communication method, security concerns, carrier restrictions
**Alternative:** Planned web UI and API

### Twitter Integration (Removed)
**Why:** API changes, limited usefulness, maintenance burden
**Alternative:** Share via social media manually

### FM Broadcasting (Removed)
**Why:** Legal restrictions in many countries, liability concerns
**Alternative:** Direct audio output or web streaming

### Pi 1/2 Support (Removed)
**Why:** Insufficient CPU for real-time FFT processing
**Alternative:** Upgrade to Pi 3+ ($35-55)

### GPU FFT (Removed)
**Why:** Deprecated library, Pi 3+ CPU is fast enough
**Alternative:** numpy CPU-based FFT

---

## ➕ What We Added

### Enhanced Button Manager
- Configuration-based (no code changes)
- Skip, repeat, audio toggle controls
- Cooldown protection
- Auto-shutoff timers

### Comprehensive Test Suite
- 76+ unit and integration tests
- Platform detection tests
- GPIO adapter tests
- Configuration validation
- CI/CD ready

### Modern Dependencies
- All from PyPI (pip install)
- No git submodules or manual compilation
- Consistent versioning
- Security updates available

### Developer Experience
- pytest framework
- Code formatting (black)
- Type hints (mypy)
- Linting (flake8)
- GitHub Actions workflows

---

## 🔮 Future Direction

### LightShowPi Neo Roadmap:
1. **FastAPI Web Backend** - Modern REST API
2. **React Frontend** - Modern web UI
3. **WebSocket Support** - Real-time updates
4. **Docker Images** - Easy deployment
5. **Cloud Integration** - Remote control
6. **Mobile App** - iOS/Android control

**Note:** These may never appear in the original project, as it has different goals.

---

## 🤝 Can I Contribute to Both?

**Yes!** The projects serve different communities:

**Contribute to Original** if:
- Your change benefits Pi 1/2 users
- You're fixing core compatibility bugs
- You want widest user base

**Contribute to Neo** if:
- Your change requires Python 3.9+
- You're adding modern features
- You want to use latest tools/libraries

---

## 📜 License Compliance

### Original Copyright Preserved:
```
Copyright (c) 2013-2021, Todd Giles
```

### Neo Copyright Added:
```
Copyright (c) 2025, Steve Croce
```

### Both Under BSD 2-Clause License
See [LICENSE](LICENSE) for full terms.

---

## 💬 Communication

### About Original LightShowPi:
- **Website:** http://lightshowpi.org/
- **Community:** [Reddit](https://www.reddit.com/r/LightShowPi/)

### About LightShowPi Neo:
- **Repository:** https://github.com/CheeseMochi/lightshowpi-neo
- **Issues:** [GitHub Issues](https://github.com/CheeseMochi/lightshowpi-neo/issues)

---

## 🙏 Acknowledgments

LightShowPi Neo would not exist without:
- **Todd Giles** - For creating the original LightShowPi
- **Original Contributors** - Chris Usey, Tom Enos, Ken B, and 20+ others
- **Community** - Years of feedback, bug reports, and feature requests
- **Open Source** - The BSD license that makes forks possible

We stand on the shoulders of giants. Thank you! 🎄

---

*Last Updated: 2025-12-07*
*Document Version: 1.0*
