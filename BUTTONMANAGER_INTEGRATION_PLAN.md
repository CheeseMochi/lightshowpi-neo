# Button Manager Integration Plan

## Current State Analysis

### What buttonmanager.py Does
- **Physical button inputs** using gpiozero library:
  - **Repeat button** (GPIO 20): Hold 5s to toggle continuous play mode
  - **Skip button** (GPIO 21): Skip to next song immediately
  - **Audio button** (GPIO 26): Toggle audio output relay with cooldown
  - **Outlet/relay** (GPIO 8): Controls external audio output device

- **Features**:
  - Button debouncing via cooldowns (5s default)
  - Auto-shutoff after 5 minutes of audio
  - Repeat mode (plays next song automatically, max 10 iterations)
  - State management via configuration_manager (play_now, now_playing)
  - Signal handling for clean shutdown

### Critical Issues Found

1. **❌ BROKEN: Line 24** - `Configuration(True)` passes SMS parameter that was just removed
   ```python
   cm = configuration_manager.Configuration(True)  # ERROR!
   ```

2. **Hardcoded paths** - Line 26 has user-specific path:
   ```python
   lightshome = "home/scroce/lightshowpi/"  # Should use env variable
   ```

3. **Dependency mismatch** - Uses `gpiozero` instead of `lgpio` (inconsistent with rest of app)

4. **No configuration integration** - GPIO pins, cooldowns, timeouts are hardcoded

5. **Separate process** - Runs independently, not integrated with main lightshow loop

6. **No logging** - Uses commented print statements instead of proper logging

---

## Integration Strategy

### Phase 1: Fix Critical Bugs & Modernize (Quick Win)

**Goal**: Make buttonmanager.py functional with modernized LightShowPi

#### Changes:
1. **Fix Configuration initialization**
   ```python
   # Before:
   cm = configuration_manager.Configuration(True)

   # After:
   cm = configuration_manager.Configuration()
   ```

2. **Use environment variable**
   ```python
   # Before:
   lightshome = "home/scroce/lightshowpi/"

   # After:
   import os
   lightshome = os.getenv("SYNCHRONIZED_LIGHTS_HOME")
   ```

3. **Add proper logging**
   ```python
   import logging
   log = logging.getLogger(__name__)
   # Replace all print statements with log.debug/info
   ```

4. **Keep gpiozero for now** (it works, migration can come later)

**Estimated effort**: 1-2 hours
**Testing**: Run buttonmanager.py standalone, verify buttons work

---

### Phase 2: Configuration Integration (Recommended)

**Goal**: Move all hardcoded values to configuration files

#### Add to `config/defaults.cfg`:

```ini
[buttons]
# Enable button manager (must be manually started as separate process)
enabled = True

# GPIO pin assignments (BCM numbering)
repeat_pin = 20
skip_pin = 21
audio_toggle_pin = 26
outlet_relay_pin = 8

# Timing settings
button_cooldown = 5
audio_auto_shutoff = 300
repeat_max_iterations = 10

# Repeat button configuration
repeat_hold_time = 5
```

#### Update buttonmanager.py to read config:
```python
def __init__(self):
    self.cm = configuration_manager.Configuration()

    # Read button config
    self.repeat_pin = self.cm.config.getint('buttons', 'repeat_pin')
    self.skip_pin = self.cm.config.getint('buttons', 'skip_pin')
    # ... etc
```

**Estimated effort**: 2-3 hours
**Testing**: Verify config loading, test with different pin configurations

---

### Phase 3: Migrate to lgpio (Optional - for consistency)

**Goal**: Use lgpio instead of gpiozero to match the rest of the codebase

#### Pros:
- Consistent GPIO library across entire app
- Already have gpio_adapter.py infrastructure
- One less dependency (can remove gpiozero from requirements.txt)

#### Cons:
- gpiozero has nicer button API (callbacks, hold detection, bounce time)
- More work to replicate gpiozero features
- If it ain't broke, don't fix it

#### Implementation approach:
1. Extend `gpio_adapter.py` with button/input helper functions
2. Add edge detection (rising/falling edge callbacks)
3. Add software button debouncing
4. Implement hold detection for repeat button

**Estimated effort**: 6-8 hours (building lgpio input helpers)
**Recommendation**: Skip for now - gpiozero works fine for buttons

---

### Phase 4: Threaded Integration (Advanced)

**Goal**: Run button manager as a background thread within synchronized_lights.py

#### Architecture:
```python
# In synchronized_lights.py

class Lightshow:
    def __init__(self):
        # ... existing code ...

        # Start button manager if enabled
        if cm.buttons.enabled:
            from buttonmanager import ButtonManager
            self.button_manager = ButtonManager(self)
            self.button_thread = threading.Thread(target=self.button_manager.run, daemon=True)
            self.button_thread.start()
```

#### Benefits:
- Single process to manage (no separate systemd service)
- Direct access to Lightshow state (no state file polling)
- Cleaner shutdown handling
- Can call Lightshow methods directly instead of state manipulation

#### Challenges:
- Thread safety concerns (need locks for shared state)
- More complex error handling
- Harder to debug than separate process

**Estimated effort**: 8-12 hours
**Recommendation**: Keep as separate process for now, consider for v2

---

## Recommended Implementation Plan

### Option A: Minimal (Recommended for now)
**Just fix what's broken, keep it separate**

1. Fix Configuration(True) bug ✅
2. Fix hardcoded path ✅
3. Add logging ✅
4. Add configuration section for GPIO pins and timings ✅
5. Update documentation ✅

**Time**: 3-4 hours
**Risk**: Low
**Benefit**: Button manager works with modernized codebase

---

### Option B: Full Integration (Future enhancement)
**Make it a first-class citizen**

1. Do everything in Option A ✅
2. Migrate to lgpio for consistency ⏰ (optional)
3. Create ButtonManager class with clean API ✅
4. Integrate as threaded component in synchronized_lights.py ✅
5. Add unit tests ✅
6. Add web UI controls (toggle buttons via API) ✅

**Time**: 15-20 hours
**Risk**: Medium
**Benefit**: Fully integrated, modern architecture

---

## Configuration Design Proposal

### New `[buttons]` section in defaults.cfg:

```ini
[buttons]
# =============================================================================
# Button Manager Configuration
# Physical button controls for lightshow interaction
# =============================================================================

# Enable button manager
# Set to True to enable physical button controls
# Note: Must run buttonmanager.py as separate process or integrate as thread
enabled = False

# GPIO Pin Assignments (BCM numbering)
# These pins are in addition to the pins used for lights (gpio_pins)
repeat_pin = 20
skip_pin = 21
audio_toggle_pin = 26
outlet_relay_pin = 8

# Button Timing (seconds)
button_cooldown = 5
audio_auto_shutoff = 300
repeat_max_iterations = 10
repeat_hold_time = 5

# Logging
log_button_presses = True
```

---

## Testing Strategy

### Unit Tests (if integrating fully):
```python
# tests/test_buttonmanager.py

@pytest.mark.gpio
class TestButtonManager:
    def test_config_loading(self):
        """Test button configuration loads from config file"""

    def test_cooldown_enforcement(self):
        """Test button cooldown prevents spam"""

    def test_audio_auto_shutoff(self):
        """Test audio turns off after timeout"""

    def test_repeat_mode(self):
        """Test repeat mode behavior"""
```

### Integration Testing:
1. Test each button function manually on Pi
2. Test cooldowns work correctly
3. Test auto-shutoff timer
4. Test repeat mode starts/stops correctly
5. Test clean shutdown (Ctrl-C handling)

---

## Migration Checklist

### Immediate (Phase 1):
- [ ] Fix `Configuration(True)` → `Configuration()`
- [ ] Fix hardcoded path to use `SYNCHRONIZED_LIGHTS_HOME`
- [ ] Replace print statements with logging
- [ ] Test on actual Pi hardware
- [ ] Update requirements.txt with `gpiozero>=1.6.0`

### Short-term (Phase 2):
- [ ] Add `[buttons]` section to defaults.cfg
- [ ] Update buttonmanager.py to read from config
- [ ] Add command-line argument for config file override
- [ ] Document button manager setup in README
- [ ] Create systemd service file for auto-start

### Long-term (Phase 3/4):
- [ ] Consider lgpio migration (optional)
- [ ] Consider threaded integration (optional)
- [ ] Add web UI button controls (via FastAPI backend)
- [ ] Add unit tests
- [ ] Add button manager status to web dashboard

---

## Questions to Answer

1. **Do you want buttons to work when lightshow is not running?**
   - Current: Buttons work anytime (separate process)
   - Integrated: Only work when lightshow is active

2. **Should button manager auto-start on boot?**
   - Need systemd service file
   - Or integrate into main service

3. **Do you want web UI controls to duplicate button functions?**
   - Skip song via web interface
   - Toggle audio via web interface
   - Enable/disable repeat mode via web

4. **Are the GPIO pins fixed or might they change?**
   - If fixed: Can hardcode in buttonmanager
   - If configurable: Definitely need config file integration

5. **Do you want button press history/logging?**
   - Track who pressed what button when
   - Could be useful for analytics

---

## Next Steps

**I recommend starting with Option A (Minimal):**

1. I'll fix the critical bugs right now (Configuration, path, logging)
2. Add configuration section for customization
3. Test it works with your hardware
4. Document setup in README

Then later, we can decide if full integration (Option B) is worth the effort when we build the FastAPI backend.

**Should I proceed with Option A fixes now?**
