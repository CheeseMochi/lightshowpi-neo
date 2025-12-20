"""Button Manager - Physical button controls for lightshow interaction.

Manages physical button inputs for controlling the lightshow:
- Repeat mode (hold to enable continuous play)
- Skip to next song
- Toggle audio output relay

Can operate in two modes:
- API mode: Calls REST API endpoints (preferred)
- Direct mode: Uses state file (fallback if API unavailable)
"""

import argparse
import logging
import os
import signal
import sys
import time
import requests
from typing import Optional

from gpiozero import Button, LED
import configuration_manager

# Set up logging
log = logging.getLogger(__name__)

# Load configuration
cm = configuration_manager.Configuration()

# Get lightshow home directory from environment
lightshome = os.getenv("SYNCHRONIZED_LIGHTS_HOME")
if not lightshome:
    log.error("SYNCHRONIZED_LIGHTS_HOME environment variable not set")
    sys.exit(1)

# Load button configuration from defaults.cfg
try:
    ENABLED = cm.config.getboolean('buttons', 'enabled')
    REPEAT_PIN = cm.config.getint('buttons', 'repeat_pin')
    SKIP_PIN = cm.config.getint('buttons', 'skip_pin')
    AUDIO_PIN = cm.config.getint('buttons', 'audio_toggle_pin')
    OUTLET_PIN = cm.config.getint('buttons', 'outlet_relay_pin')
    DEFAULT_COOLDOWN = cm.config.getint('buttons', 'button_cooldown')
    DEFAULT_AUDIO_TIMEOUT = cm.config.getint('buttons', 'audio_auto_shutoff')
    REPEAT_MAX_ITERATIONS = cm.config.getint('buttons', 'repeat_max_iterations')
    REPEAT_HOLD_TIME = cm.config.getint('buttons', 'repeat_hold_time')
    LOG_BUTTON_PRESSES = cm.config.getboolean('buttons', 'log_button_presses')
except Exception as e:
    log.error(f"Failed to load button configuration: {e}")
    log.error("Using default values. Please check config/defaults.cfg [buttons] section")
    # Fallback to defaults
    ENABLED = False
    REPEAT_PIN = 20
    SKIP_PIN = 21
    AUDIO_PIN = 26
    OUTLET_PIN = 8
    DEFAULT_COOLDOWN = 5
    DEFAULT_AUDIO_TIMEOUT = 300
    REPEAT_MAX_ITERATIONS = 10
    REPEAT_HOLD_TIME = 5
    LOG_BUTTON_PRESSES = True

# State variables
audio_cooldown = 0
audio_turnoff = 0
repeat_mode = False

# API integration
API_ENABLED = False
API_BASE_URL = "http://localhost:5000/api"
API_TOKEN: Optional[str] = None
BUTTON_MODE = "auto"  # Can be: "api", "direct", or "auto"

# Set up shell environment for subprocess calls
shellenv = dict()
shellenv['SYNCHRONIZED_LIGHTS_HOME'] = lightshome


def init_api(mode: str) -> bool:
    """Initialize API connection and authenticate based on mode.

    Args:
        mode: Operating mode - "api", "direct", or "auto"

    Returns:
        True if API is available and authenticated, False otherwise

    Raises:
        SystemExit: If mode is "api" and API is unavailable
    """
    global API_ENABLED, API_TOKEN, BUTTON_MODE

    BUTTON_MODE = mode

    # If direct mode requested, skip API initialization
    if mode == "direct":
        log.info("Button manager mode: DIRECT (state file only)")
        log.info("API integration disabled by user - using state file for button actions")
        return False

    # Try to connect to API
    try:
        # Check if API is accessible
        log.debug(f"Checking API availability at {API_BASE_URL}/health")
        response = requests.get(f"{API_BASE_URL}/health", timeout=2)
        if response.status_code != 200:
            if mode == "api":
                log.error(f"API health check failed with status {response.status_code}")
                log.error("Button manager mode is set to 'api' but API is not available")
                log.error("Either start the API or use --mode direct/auto")
                sys.exit(1)
            else:
                log.warning("API health check failed - falling back to direct mode")
                return False

        # Try to authenticate (use default credentials for now)
        # TODO: Make credentials configurable
        log.debug("Authenticating with API...")
        auth_response = requests.post(
            f"{API_BASE_URL}/auth/login",
            json={"username": "admin", "password": "admin123"},
            timeout=2
        )

        if auth_response.status_code == 200:
            API_TOKEN = auth_response.json()["access_token"]
            API_ENABLED = True
            log.info("✓ Button manager mode: API")
            log.info("✓ API integration enabled - using REST API for button actions")
            return True
        else:
            if mode == "api":
                log.error(f"API authentication failed with status {auth_response.status_code}")
                log.error("Button manager mode is set to 'api' but authentication failed")
                log.error("Check API credentials or use --mode direct/auto")
                sys.exit(1)
            else:
                log.warning("API authentication failed - falling back to direct mode")
                log.warning(f"Auth failed with status {auth_response.status_code}")
                return False

    except requests.exceptions.RequestException as e:
        if mode == "api":
            log.error(f"Cannot connect to API: {e}")
            log.error("Button manager mode is set to 'api' but API is not available")
            log.error(f"Make sure API is running at {API_BASE_URL}")
            log.error("Either start the API or use --mode direct/auto")
            sys.exit(1)
        else:
            log.warning(f"API not available: {e}")
            log.info("⚠ Button manager mode: DIRECT (API unavailable, using state file)")
            return False


def call_api(endpoint: str, method: str = "POST") -> bool:
    """Call an API endpoint with authentication.

    Args:
        endpoint: API endpoint path (e.g., "/buttons/skip")
        method: HTTP method (GET or POST)

    Returns:
        True if successful, False otherwise
    """
    if not API_ENABLED or not API_TOKEN:
        return False

    try:
        headers = {"Authorization": f"Bearer {API_TOKEN}"}
        url = f"{API_BASE_URL}{endpoint}"

        if method == "POST":
            response = requests.post(url, headers=headers, timeout=2)
        else:
            response = requests.get(url, headers=headers, timeout=2)

        if response.status_code in [200, 201]:
            log.debug(f"API call successful: {method} {endpoint}")
            return True
        else:
            log.warning(f"API call failed: {response.status_code} - {response.text}")
            return False

    except requests.exceptions.RequestException as e:
        log.error(f"API call failed: {e}")
        return False

# Initialize buttons and output
repeat_button = Button(REPEAT_PIN)
repeat_button.hold_time = REPEAT_HOLD_TIME
repeat_button.hold_repeat = False
skip_button = Button(SKIP_PIN)
audio_button = Button(AUDIO_PIN)
outlet = LED(OUTLET_PIN)

def doCleanup():
    """Clean shutdown - close all GPIO resources and reset state."""
    log.info("Button manager shutting down...")
    cm.update_state('play_now', "0")
    outlet.off()
    outlet.close()
    repeat_button.close()
    skip_button.close()
    audio_button.close()
    time.sleep(1)
    log.info("Button manager shutdown complete")
    sys.exit(0)


def sigint_handler(signal, frame):
    """Handle Ctrl-C gracefully."""
    log.info("Interrupt signal received (Ctrl-C)")
    doCleanup()
    sys.exit(0)


signal.signal(signal.SIGINT, sigint_handler)

def audio_on():
    """Turn audio output on and set auto-shutoff timer."""
    global audio_cooldown, audio_turnoff
    audio_cooldown = time.time() + DEFAULT_COOLDOWN
    audio_turnoff = time.time() + DEFAULT_AUDIO_TIMEOUT
    log.info(f"Audio ON - auto-shutoff in {DEFAULT_AUDIO_TIMEOUT}s")
    outlet.on()


def audio_off():
    """Turn audio output off."""
    global audio_cooldown
    audio_cooldown = time.time() + DEFAULT_COOLDOWN
    log.info("Audio OFF")
    outlet.off()


def audio_toggle():
    """Toggle audio output with smart behavior."""
    global audio_cooldown, audio_turnoff
    audio_cooldown = time.time() + DEFAULT_COOLDOWN
    cm.load_state()

    if not outlet.is_lit:
        audio_turnoff = time.time() + DEFAULT_AUDIO_TIMEOUT
        # If no song is playing, start one
        if int(cm.get_state('now_playing', "1")) == 0:
            log.info("Audio toggle: Starting playback")
            playsong(-1)
            return

    outlet.toggle()
    status = "ON" if outlet.is_lit else "OFF"
    log.info(f"Audio toggled: {status}")


def playsong(songindex):
    """Queue a song to play (-1 = next song)."""
    log.info(f"Queueing song: {songindex}")

    # Try API first if enabled
    if API_ENABLED:
        if call_api("/buttons/skip"):
            log.debug("Skip triggered via API")
        else:
            log.error("⚠ API call failed for skip button - API may be down")
            if BUTTON_MODE == "auto":
                log.warning("Falling back to direct mode for this action")
                cm.update_state('play_now', songindex)
            # In "api" mode, we already exited if API wasn't available during init
    else:
        # Direct mode - use state file
        cm.update_state('play_now', songindex)
        log.debug("Skip triggered via state file (direct mode)")

    audio_on()


def songbutton(button):
    """Handle song button press (skip button)."""
    cm.load_state()
    if int(cm.get_state('play_now', "0")) != 0:
        log.debug("Skip button pressed but play_now already set, ignoring")
        return

    if button.pin.number == SKIP_PIN:
        log.info("Skip button pressed")
        playsong(-1)


def toggleRepeat():
    """Toggle repeat mode on/off."""
    global repeat_mode

    # Try API first if enabled
    if API_ENABLED:
        if call_api("/buttons/repeat/toggle"):
            log.debug("Repeat toggle triggered via API")
            # API handles the state change, just update local state
            repeat_mode = not repeat_mode
            if repeat_mode:
                log.info("Repeat mode ENABLED")
            else:
                log.info("Repeat mode DISABLED")
        else:
            log.error("⚠ API call failed for repeat toggle - API may be down")
            if BUTTON_MODE == "auto":
                log.warning("Falling back to direct mode for this action")
                # Fall through to direct mode below
            else:
                return  # In api mode, don't fall back

    # Direct mode or fallback
    if not API_ENABLED or (BUTTON_MODE == "auto" and not repeat_mode):
        if not repeat_mode:
            log.info("Repeat mode ENABLED")
            playsong(-1)
            repeat_mode = True
        else:
            log.info("Repeat mode DISABLED")
            repeat_mode = False
            audio_off()


def audiobutton():
    """Handle audio toggle button press with cooldown."""
    global audio_cooldown, audio_turnoff

    if time.time() > audio_cooldown:
        log.debug("Audio button pressed")

        # Try API first if enabled
        if API_ENABLED:
            if call_api("/buttons/audio/toggle"):
                log.debug("Audio toggle triggered via API")
                # Update local cooldown and state
                audio_cooldown = time.time() + DEFAULT_COOLDOWN
                if not outlet.is_lit:
                    audio_turnoff = time.time() + DEFAULT_AUDIO_TIMEOUT
                outlet.toggle()
            else:
                log.error("⚠ API call failed for audio toggle - API may be down")
                if BUTTON_MODE == "auto":
                    log.warning("Falling back to direct mode for this action")
                    audio_toggle()
                # In api mode, don't fall back
        else:
            # Direct mode
            audio_toggle()
    else:
        log.debug(f"Audio button on cooldown ({audio_cooldown - time.time():.1f}s remaining)")

def main():
    """Main button manager loop.

    Manages physical button inputs for lightshow control:
    - Repeat mode (hold repeat button for 5s)
    - Skip to next song
    - Toggle audio output
    """
    global repeat_mode

    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="LightShowPi Button Manager - Physical button controls"
    )
    parser.add_argument(
        '--cleanup',
        help="Clean up GPIO resources and exit",
        action="store_true"
    )
    parser.add_argument(
        '--log-level',
        help="Set logging level (DEBUG, INFO, WARNING, ERROR)",
        default="INFO"
    )
    parser.add_argument(
        '--mode',
        help="Button manager mode: 'api' (require API), 'direct' (state file only), 'auto' (try API, fall back to direct)",
        choices=['api', 'direct', 'auto'],
        default='auto'
    )

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Handle cleanup mode
    if args.cleanup:
        doCleanup()
        return

    # Initialize state
    repeat_mode = False
    repeat_songs = 0

    log.info("Button Manager starting...")
    log.info(f"GPIO Pins - Repeat: {REPEAT_PIN}, Skip: {SKIP_PIN}, "
             f"Audio: {AUDIO_PIN}, Outlet: {OUTLET_PIN}")

    # Initialize API integration based on mode
    log.info(f"Requested mode: {args.mode.upper()}")
    init_api(args.mode)

    # Set up button callbacks
    repeat_button.when_held = toggleRepeat
    skip_button.when_pressed = songbutton
    audio_button.when_pressed = audiobutton

    log.info("Button Manager ready. Press Ctrl-C to exit")

    # Main event loop
    try:
        while True:
            # Auto-shutoff check
            if outlet.is_lit and time.time() > audio_turnoff and not repeat_mode:
                log.info("Audio auto-shutoff timer reached")
                audio_off()

            # Repeat mode handling
            cm.load_state()
            if repeat_mode and int(cm.get_state('now_playing', "1")) == 0:
                repeat_songs += 1
                if repeat_songs > REPEAT_MAX_ITERATIONS:
                    log.warning(f"Repeat mode max iterations reached ({REPEAT_MAX_ITERATIONS}), disabling")
                    repeat_mode = False
                    repeat_songs = 0
                else:
                    log.debug(f"Repeat mode: Playing next song ({repeat_songs}/{REPEAT_MAX_ITERATIONS})")
                    playsong(-1)

            # Sleep to reduce CPU usage
            time.sleep(5)

    except KeyboardInterrupt:
        log.info("Keyboard interrupt received")
        doCleanup()
    except Exception as e:
        log.error(f"Unexpected error: {e}", exc_info=True)
        doCleanup()


if __name__ == "__main__":
    main()
