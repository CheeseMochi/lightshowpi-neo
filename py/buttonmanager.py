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
import event_log

# Set up logging
log = logging.getLogger(__name__)

# Configuration and constants - will be initialized in main() or set to defaults
cm = None
lightshome = None

# Default configuration values (used if config not loaded or for tests)
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
DEFAULT_API_PORT = 8000
API_BASE_URL = f"http://localhost:{DEFAULT_API_PORT}/api"
API_USERNAME = "admin"
API_PASSWORD: Optional[str] = None
API_TOKEN: Optional[str] = None
BUTTON_MODE = "auto"  # Can be: "api", "direct", or "auto"

# Environment variables that override [buttons] api_* settings
ENV_API_URL = "LIGHTSHOWPI_API_URL"
ENV_API_USERNAME = "LIGHTSHOWPI_API_USERNAME"
ENV_API_PASSWORD = "LIGHTSHOWPI_API_PASSWORD"

# Shell environment - will be set up in main()
shellenv = dict()


def _config_value(config, section: str, key: str) -> str:
    """Read a config value, returning '' when missing."""
    if config is None:
        return ""
    try:
        value = config.get(section, key, fallback="")
    except Exception:
        return ""
    return (value or "").strip()


def resolve_api_settings(config=None, environ=None):
    """Resolve API URL, username and password.

    Precedence for each value: environment variable -> [buttons] config -> default.
    The default URL is http://localhost:<[api] port>/api. There is no default password.

    Args:
        config: ConfigParser with [buttons] and [api] sections (optional)
        environ: Mapping to read environment variables from (defaults to os.environ)

    Returns:
        Tuple of (url, username, password); password is None if not configured.
    """
    if environ is None:
        environ = os.environ

    port = DEFAULT_API_PORT
    port_str = _config_value(config, 'api', 'port')
    if port_str:
        try:
            port = int(port_str)
        except ValueError:
            log.warning(f"Invalid [api] port '{port_str}', using {DEFAULT_API_PORT}")

    url = (environ.get(ENV_API_URL) or _config_value(config, 'buttons', 'api_url')
           or f"http://localhost:{port}/api")
    username = (environ.get(ENV_API_USERNAME) or _config_value(config, 'buttons', 'api_username')
                or "admin")
    password = environ.get(ENV_API_PASSWORD) or _config_value(config, 'buttons', 'api_password') or None

    return url.rstrip('/'), username, password


def configure_api(config=None, environ=None):
    """Load API connection settings into module globals."""
    global API_BASE_URL, API_USERNAME, API_PASSWORD
    API_BASE_URL, API_USERNAME, API_PASSWORD = resolve_api_settings(config, environ)
    log.info(f"API URL: {API_BASE_URL} (user: {API_USERNAME}, "
             f"password {'set' if API_PASSWORD else 'NOT set'})")


def login() -> Optional[int]:
    """Authenticate with the API and store the token.

    Returns:
        HTTP status code of the login request (200 on success), or None if
        no password is configured. Raises requests exceptions on connection errors.
    """
    global API_TOKEN

    if not API_PASSWORD:
        return None

    log.debug(f"Authenticating with API as {API_USERNAME}...")
    response = requests.post(
        f"{API_BASE_URL}/auth/login",
        json={"username": API_USERNAME, "password": API_PASSWORD},
        timeout=2
    )
    if response.status_code == 200:
        API_TOKEN = response.json()["access_token"]
    else:
        API_TOKEN = None
    return response.status_code


def init_api(mode: str) -> bool:
    """Initialize API connection and authenticate based on mode.

    Args:
        mode: Operating mode - "api", "direct", or "auto"

    Returns:
        True if API is available and authenticated, False otherwise

    Raises:
        SystemExit: If mode is "api" and API is unavailable
    """
    global API_ENABLED, BUTTON_MODE

    BUTTON_MODE = mode
    API_ENABLED = False

    # If direct mode requested, skip API initialization
    if mode == "direct":
        log.info("Button manager mode: DIRECT (state file only)")
        log.info("API integration disabled by user - using state file for button actions")
        return False

    if not API_PASSWORD:
        if mode == "api":
            log.error("Button manager mode is set to 'api' but no API password is configured")
            log.error(f"Set {ENV_API_PASSWORD} (e.g. in /etc/lightshowpi/buttons.env) "
                      "or [buttons] api_password in config/overrides.cfg")
            sys.exit(1)
        log.warning("No API password configured "
                    f"({ENV_API_PASSWORD} or [buttons] api_password) - falling back to direct mode")
        log.info("⚠ Button manager mode: DIRECT (no API credentials, using state file)")
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

        auth_status = login()

        if auth_status == 200:
            API_ENABLED = True
            log.info("✓ Button manager mode: API")
            log.info("✓ API integration enabled - using REST API for button actions")
            return True
        else:
            if mode == "api":
                log.error(f"API authentication failed with status {auth_status}")
                log.error("Button manager mode is set to 'api' but authentication failed")
                log.error("Check API credentials or use --mode direct/auto")
                sys.exit(1)
            else:
                log.warning("API authentication failed - falling back to direct mode")
                log.warning(f"Auth failed with status {auth_status}")
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


def _send(method: str, url: str):
    headers = {"Authorization": f"Bearer {API_TOKEN}"}
    if method == "POST":
        return requests.post(url, headers=headers, timeout=2)
    return requests.get(url, headers=headers, timeout=2)


def call_api(endpoint: str, method: str = "POST") -> bool:
    """Call an API endpoint with authentication.

    On a 401 (expired token, or API restarted with a new JWT secret) this
    logs in again once and retries the request.

    Args:
        endpoint: API endpoint path (e.g., "/buttons/skip")
        method: HTTP method (GET or POST)

    Returns:
        True if successful, False otherwise
    """
    if not API_ENABLED or not API_TOKEN:
        return False

    try:
        url = f"{API_BASE_URL}{endpoint}"
        response = _send(method, url)

        if response.status_code == 401:
            log.info("API token rejected (401) - logging in again")
            if login() == 200:
                response = _send(method, url)
            else:
                log.error("API re-login failed")
                return False

        if response.status_code in [200, 201]:
            log.debug(f"API call successful: {method} {endpoint}")
            return True
        else:
            log.warning(f"API call failed: {response.status_code} - {response.text}")
            return False

    except requests.exceptions.RequestException as e:
        log.error(f"API call failed: {e}")
        return False

# Button and LED objects - initialized in main() to avoid GPIO access at import time
repeat_button = None
skip_button = None
audio_button = None
outlet = None

def doCleanup():
    """Clean shutdown - close all GPIO resources and reset state."""
    log.info("Button manager shutting down...")
    cm.update_state('play_now', "0")

    # Only clean up if buttons were initialized
    if outlet is not None:
        outlet.off()
        outlet.close()
    if repeat_button is not None:
        repeat_button.close()
    if skip_button is not None:
        skip_button.close()
    if audio_button is not None:
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


def log_button_press(name: str, via_api: bool = False) -> None:
    """Record a physical button press in the event log.

    Skipped when ``log_button_presses`` is off, or when the press went to the
    API (``via_api``): the API logs those itself, so each press counts once.
    """
    if not LOG_BUTTON_PRESSES or via_api:
        return
    event_log.record("button_press", source="button", name=name, mode=BUTTON_MODE)


def playsong(songindex) -> bool:
    """Queue a song to play (-1 = next song).

    Returns:
        True if the API handled it, False if the state file was used.
    """
    log.info(f"Queueing song: {songindex}")
    via_api = False

    # Try API first if enabled
    if API_ENABLED:
        if call_api("/buttons/skip"):
            log.debug("Skip triggered via API")
            via_api = True
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
    return via_api


def songbutton(button):
    """Handle song button press (skip button)."""
    cm.load_state()
    if int(cm.get_state('play_now', "0")) != 0:
        log.debug("Skip button pressed but play_now already set, ignoring")
        return

    if button.pin.number == SKIP_PIN:
        log.info("Skip button pressed")
        via_api = playsong(-1)
        log_button_press("skip", via_api is True)


def toggleRepeat():
    """Toggle repeat mode on/off."""
    global repeat_mode

    if not API_ENABLED:
        log_button_press("repeat")

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
            log_button_press("repeat")
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
                log_button_press("audio")
                if BUTTON_MODE == "auto":
                    log.warning("Falling back to direct mode for this action")
                    audio_toggle()
                # In api mode, don't fall back
        else:
            # Direct mode
            log_button_press("audio")
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

    # Load configuration
    global cm, lightshome, shellenv
    global ENABLED, REPEAT_PIN, SKIP_PIN, AUDIO_PIN, OUTLET_PIN
    global DEFAULT_COOLDOWN, DEFAULT_AUDIO_TIMEOUT, REPEAT_MAX_ITERATIONS
    global REPEAT_HOLD_TIME, LOG_BUTTON_PRESSES

    cm = configuration_manager.Configuration()
    lightshome = os.getenv("SYNCHRONIZED_LIGHTS_HOME")
    if not lightshome:
        log.error("SYNCHRONIZED_LIGHTS_HOME environment variable not set")
        sys.exit(1)

    # Set up shell environment
    shellenv['SYNCHRONIZED_LIGHTS_HOME'] = lightshome

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
        log.debug("Configuration loaded successfully")
    except Exception as e:
        log.warning(f"Failed to load button configuration: {e}")
        log.warning("Using default values")
        # Keep the default values already set at module level

    # Event log settings ([events]); recording never raises
    event_log.configure(cm.config)

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
    configure_api(cm.config)
    init_api(args.mode)

    # Initialize GPIO hardware (buttons and output)
    global repeat_button, skip_button, audio_button, outlet
    log.debug("Initializing GPIO buttons and LED output...")
    repeat_button = Button(REPEAT_PIN)
    repeat_button.hold_time = REPEAT_HOLD_TIME
    repeat_button.hold_repeat = False
    skip_button = Button(SKIP_PIN)
    audio_button = Button(AUDIO_PIN)
    outlet = LED(OUTLET_PIN)
    log.debug("GPIO initialization complete")

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
