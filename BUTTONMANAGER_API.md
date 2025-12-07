# Button Manager API Documentation

## Overview

This document defines the API surface that the web UI needs to interact with button manager functionality. When building the FastAPI backend, these endpoints should be implemented to allow web-based control equivalent to physical buttons.

## State Management

The button manager uses `configuration_manager` state file for communication:

**State Variables:**
- `play_now` - Queue a song to play (0 = ready, -1 = play next, >0 = specific song index)
- `now_playing` - Current playback status (0 = nothing playing, 1 = song playing)

## Button Manager Functions

These are the core functions in `buttonmanager.py` that the web UI should replicate:

### 1. Skip to Next Song

**Function:** `playsong(-1)`

**Behavior:**
- Queues the next song to play
- Turns audio output ON
- Sets auto-shutoff timer

**Web API Endpoint (Future):**
```http
POST /api/controls/skip
Content-Type: application/json

Response: 200 OK
{
  "status": "success",
  "action": "skip",
  "audio": "on"
}
```

**Implementation:**
```python
# In FastAPI backend
@app.post("/api/controls/skip")
async def skip_song():
    cm.update_state('play_now', -1)
    # Turn on audio outlet if available
    return {"status": "success", "action": "skip"}
```

---

### 2. Toggle Audio Output

**Function:** `audio_toggle()`

**Behavior:**
- Toggles audio output relay ON/OFF
- Enforces cooldown (5s default)
- If turning on while nothing playing, starts playback
- Sets auto-shutoff timer when turning on

**Web API Endpoint (Future):**
```http
POST /api/controls/audio/toggle
Content-Type: application/json

Response: 200 OK
{
  "status": "success",
  "audio": "on" | "off",
  "cooldown_remaining": 0
}

Response: 429 Too Many Requests (if on cooldown)
{
  "status": "error",
  "message": "Cooldown active",
  "cooldown_remaining": 3.5
}
```

**Implementation:**
```python
@app.post("/api/controls/audio/toggle")
async def toggle_audio():
    global audio_cooldown

    if time.time() < audio_cooldown:
        remaining = audio_cooldown - time.time()
        raise HTTPException(
            status_code=429,
            detail={"message": "Cooldown active", "remaining": remaining}
        )

    audio_cooldown = time.time() + DEFAULT_COOLDOWN
    # Toggle outlet state
    # ...
    return {"status": "success", "audio": outlet_state}
```

---

### 3. Enable/Disable Repeat Mode

**Function:** `toggleRepeat()`

**Behavior:**
- Enables repeat mode: Starts playing next song automatically after each song finishes
- Disables repeat mode: Turns off audio
- Max iterations: 10 (configurable)

**Web API Endpoint (Future):**
```http
POST /api/controls/repeat/enable
POST /api/controls/repeat/disable
GET /api/controls/repeat/status

Response: 200 OK
{
  "status": "success",
  "repeat_mode": true | false,
  "iterations": 3,
  "max_iterations": 10
}
```

**Implementation:**
```python
repeat_mode = False
repeat_iterations = 0

@app.post("/api/controls/repeat/enable")
async def enable_repeat():
    global repeat_mode
    repeat_mode = True
    # Start playback
    cm.update_state('play_now', -1)
    return {"status": "success", "repeat_mode": True}

@app.post("/api/controls/repeat/disable")
async def disable_repeat():
    global repeat_mode
    repeat_mode = False
    # Turn off audio
    return {"status": "success", "repeat_mode": False}

@app.get("/api/controls/repeat/status")
async def get_repeat_status():
    return {
        "repeat_mode": repeat_mode,
        "iterations": repeat_iterations,
        "max_iterations": REPEAT_MAX_ITERATIONS
    }
```

---

### 4. Audio Auto-Shutoff Control

**Behavior:**
- Audio automatically turns off after timeout (5 minutes default)
- Can be disabled in repeat mode
- Can be extended/reset via button press

**Web API Endpoint (Future):**
```http
GET /api/controls/audio/status

Response: 200 OK
{
  "audio": "on" | "off",
  "auto_shutoff_enabled": true,
  "shutoff_in": 247,  // seconds remaining
  "cooldown_remaining": 0
}

POST /api/controls/audio/extend
Response: 200 OK
{
  "status": "success",
  "shutoff_in": 300  // reset to default timeout
}
```

---

## Button Manager State API

The web UI should be able to monitor and control button manager state:

### Get Button Manager Status

```http
GET /api/buttonmanager/status

Response: 200 OK
{
  "enabled": true,
  "audio": {
    "state": "on" | "off",
    "cooldown_remaining": 0,
    "auto_shutoff_in": 247
  },
  "repeat": {
    "enabled": false,
    "iterations": 0,
    "max_iterations": 10
  },
  "playback": {
    "now_playing": 0 | 1,
    "play_now": 0
  }
}
```

### Disable Physical Buttons (Web UI Lock)

When using web controls, you may want to temporarily disable physical buttons:

```http
POST /api/buttonmanager/lock
POST /api/buttonmanager/unlock

Response: 200 OK
{
  "status": "success",
  "buttons_locked": true | false,
  "message": "Physical buttons disabled. Using web controls only."
}
```

**Implementation:**
- Add a state variable `buttons_locked` to configuration state
- Button manager checks this before processing physical button presses
- Web API can set/unset this flag
- Useful for preventing conflicts between physical and web controls

---

## Configuration API

The web UI should allow users to configure button behavior:

### Get Button Configuration

```http
GET /api/buttonmanager/config

Response: 200 OK
{
  "enabled": true,
  "gpio_pins": {
    "repeat": 20,
    "skip": 21,
    "audio_toggle": 26,
    "outlet_relay": 8
  },
  "timing": {
    "button_cooldown": 5,
    "audio_auto_shutoff": 300,
    "repeat_max_iterations": 10,
    "repeat_hold_time": 5
  },
  "logging": {
    "log_button_presses": true
  }
}
```

### Update Button Configuration

```http
PUT /api/buttonmanager/config
Content-Type: application/json

{
  "timing": {
    "audio_auto_shutoff": 600  // change to 10 minutes
  }
}

Response: 200 OK
{
  "status": "success",
  "message": "Configuration updated. Restart button manager to apply changes."
}
```

---

## WebSocket Real-time Updates

For real-time status updates without polling:

```javascript
// Client-side WebSocket connection
const ws = new WebSocket('ws://lightshowpi.local:8000/ws/buttonmanager');

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  /*
  {
    "type": "audio_change",
    "audio": "on",
    "triggered_by": "physical_button" | "web_ui" | "auto_shutoff"
  }
  */
};

// Events to broadcast:
// - audio_change (on/off)
// - repeat_mode_change (enabled/disabled)
// - song_skip
// - button_press (which button)
// - auto_shutoff_triggered
```

---

## Implementation Checklist

When building the FastAPI backend:

- [ ] Create `/api/controls/skip` endpoint
- [ ] Create `/api/controls/audio/toggle` endpoint
- [ ] Create `/api/controls/repeat/enable` endpoint
- [ ] Create `/api/controls/repeat/disable` endpoint
- [ ] Create `/api/controls/repeat/status` endpoint
- [ ] Create `/api/buttonmanager/status` endpoint for monitoring
- [ ] Create `/api/buttonmanager/config` endpoints (GET/PUT)
- [ ] Implement cooldown enforcement
- [ ] Implement auto-shutoff timer
- [ ] Add WebSocket support for real-time updates
- [ ] Add button lock/unlock mechanism
- [ ] Add authentication/authorization (optional but recommended)
- [ ] Add rate limiting to prevent API abuse

---

## Security Considerations

1. **Authentication**: Require authentication for control endpoints
2. **Rate Limiting**: Prevent API spam (similar to physical button cooldown)
3. **CORS**: Configure properly for web UI access
4. **Input Validation**: Validate all configuration updates
5. **State Conflicts**: Handle race conditions between physical buttons and web UI

---

## Testing Strategy

### Manual Testing:
1. Verify physical buttons work (run buttonmanager.py)
2. Verify web UI controls work (FastAPI endpoints)
3. Test physical + web simultaneously (check for conflicts)
4. Test cooldown enforcement
5. Test auto-shutoff behavior
6. Test repeat mode with max iterations

### Automated Testing:
- Unit tests for buttonmanager.py functions ✅ (done)
- Integration tests for FastAPI endpoints (future)
- WebSocket event tests (future)
- End-to-end tests with Playwright (future)

---

## Future Enhancements

1. **Button Press History**: Log all button presses with timestamps
2. **Custom Button Mapping**: Allow users to reassign button functions
3. **Multi-user Support**: Track which user triggered actions (physical vs. web)
4. **Schedules**: Auto-enable/disable buttons on schedule
5. **Geofencing**: Auto-disable buttons when user is away (via mobile app)
6. **Voice Control**: Integrate with Google Home/Alexa
7. **Button Profiles**: Different button behaviors for different times/events
