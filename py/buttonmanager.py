"""Button Manager - Physical button controls for lightshow interaction.

Manages physical button inputs for controlling the lightshow:
- Repeat mode (hold to enable continuous play)
- Skip to next song
- Toggle audio output relay
"""

import argparse
import logging
import os
import signal
import sys
import time

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

# Set up shell environment for subprocess calls
shellenv = dict()
shellenv['SYNCHRONIZED_LIGHTS_HOME'] = lightshome

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
    cm.update_state('play_now', songindex)
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
    if time.time() > audio_cooldown:
        log.debug("Audio button pressed")
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
