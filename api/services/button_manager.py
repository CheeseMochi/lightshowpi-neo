"""Button Manager Service

Manages button state and actions for both physical and virtual buttons.
Coordinates with lightshow manager for playback control.
"""

import logging
import time
from datetime import datetime
from enum import Enum
from typing import Optional

log = logging.getLogger(__name__)


class ButtonAction(str, Enum):
    """Available button actions."""
    SKIP = "skip"
    REPEAT_TOGGLE = "repeat_toggle"
    AUDIO_TOGGLE = "audio_toggle"


class ButtonManagerService:
    """Service for managing button state and actions.

    Handles both physical button inputs (from buttonmanager.py) and
    virtual button presses (from web UI). Coordinates with lightshow
    manager for playback control.
    """

    def __init__(self, config: dict, lightshow_manager=None):
        """Initialize button manager service.

        Args:
            config: API configuration dictionary
            lightshow_manager: Reference to lightshow manager service
        """
        self.config = config
        self.lightshow_manager = lightshow_manager

        # Button state
        self.enabled = config.get('button_manager', {}).get('enabled', False)
        self.repeat_mode = False
        self.audio_on = False
        self.audio_cooldown_until = 0.0

        # Action tracking
        self.last_action: Optional[str] = None
        self.last_action_time: Optional[datetime] = None

        # Stuck button detection
        self.button_press_start: dict[str, float] = {}
        self.stuck_threshold = 30.0  # seconds

        # Audio settings
        self.audio_timeout = config.get('button_manager', {}).get('audio_timeout', 300)
        self.cooldown_duration = config.get('button_manager', {}).get('cooldown', 5)
        self.audio_shutoff_time: Optional[float] = None

        log.info(f"Button manager service initialized (enabled={self.enabled})")

    def set_lightshow_manager(self, manager):
        """Set lightshow manager reference after initialization."""
        self.lightshow_manager = manager
        log.debug("Lightshow manager reference set in button manager")

    def get_status(self) -> dict:
        """Get current button manager status.

        Returns:
            Dictionary with current state
        """
        return {
            'enabled': self.enabled,
            'repeat_mode': self.repeat_mode,
            'audio_on': self.audio_on,
            'last_action': self.last_action,
            'last_action_time': self.last_action_time
        }

    def check_health(self) -> dict:
        """Check button manager health and detect stuck buttons.

        Returns:
            Health status dictionary
        """
        current_time = time.time()
        stuck_button = None
        stuck_duration = None

        # Check for stuck buttons (exclude repeat button - it's meant to be held)
        for button_name, press_time in self.button_press_start.items():
            # Skip repeat button - holding it for 5s is normal operation
            if button_name == 'repeat':
                continue

            duration = current_time - press_time
            if duration > self.stuck_threshold:
                stuck_button = button_name
                stuck_duration = duration
                break

        healthy = stuck_button is None
        warning = None

        if not healthy:
            warning = f"Button '{stuck_button}' may be stuck ({stuck_duration:.1f}s)"
            log.warning(warning)

        return {
            'healthy': healthy,
            'stuck_button': stuck_button,
            'stuck_duration': stuck_duration,
            'warning': warning
        }

    def handle_button_action(self, action: ButtonAction) -> bool:
        """Handle a button action (physical or virtual).

        Args:
            action: The button action to perform

        Returns:
            New state value for toggle actions (True/False)

        Raises:
            Exception: If action fails
        """
        if not self.enabled:
            log.warning(f"Button action '{action}' ignored - button manager disabled")
            raise Exception("Button manager is disabled in configuration")

        # Record action
        self.last_action = action.value
        self.last_action_time = datetime.now()

        log.info(f"Handling button action: {action}")

        if action == ButtonAction.SKIP:
            return self._handle_skip()
        elif action == ButtonAction.REPEAT_TOGGLE:
            return self._handle_repeat_toggle()
        elif action == ButtonAction.AUDIO_TOGGLE:
            return self._handle_audio_toggle()
        else:
            raise ValueError(f"Unknown button action: {action}")

    def _handle_skip(self) -> bool:
        """Handle skip button - queue next song."""
        if self.lightshow_manager is None:
            raise Exception("Lightshow manager not available")

        # Turn audio on if it's off
        if not self.audio_on:
            self._audio_on()

        # Skip to next song (or start if not playing)
        if self.lightshow_manager.is_running():
            self.lightshow_manager.skip()
            log.info("Skip action: skipping to next song")
        else:
            self.lightshow_manager.start()
            log.info("Skip action: starting playback")
        return True

    def _handle_repeat_toggle(self) -> bool:
        """Handle repeat toggle - enable/disable continuous play."""
        self.repeat_mode = not self.repeat_mode

        if self.repeat_mode:
            log.info("Repeat mode ENABLED")
            # Start playing if not already
            if not self.audio_on:
                self._audio_on()
            if self.lightshow_manager:
                self.lightshow_manager.start()
        else:
            log.info("Repeat mode DISABLED")
            # Turn off audio when disabling repeat
            self._audio_off()

        return self.repeat_mode

    def _handle_audio_toggle(self) -> bool:
        """Handle audio toggle - turn relay on/off with cooldown."""
        current_time = time.time()

        # Check cooldown
        if current_time < self.audio_cooldown_until:
            remaining = self.audio_cooldown_until - current_time
            log.warning(f"Audio toggle on cooldown ({remaining:.1f}s remaining)")
            raise Exception(f"Audio toggle on cooldown ({remaining:.1f}s remaining)")

        # Toggle audio
        if self.audio_on:
            self._audio_off()
        else:
            self._audio_on()
            # If no song playing, start one
            if self.lightshow_manager and not self.lightshow_manager.is_running():
                self.lightshow_manager.start()

        return self.audio_on

    def _audio_on(self):
        """Turn audio on and set cooldown/shutoff timers."""
        current_time = time.time()
        self.audio_on = True
        self.audio_cooldown_until = current_time + self.cooldown_duration
        self.audio_shutoff_time = current_time + self.audio_timeout
        log.info(f"Audio ON - auto-shutoff in {self.audio_timeout}s")

    def _audio_off(self):
        """Turn audio off and set cooldown."""
        current_time = time.time()
        self.audio_on = False
        self.audio_cooldown_until = current_time + self.cooldown_duration
        self.audio_shutoff_time = None
        log.info("Audio OFF")

    def check_auto_shutoff(self):
        """Check if audio should auto-shutoff.

        Called periodically by external process (e.g., buttonmanager.py)
        """
        if not self.audio_on or self.repeat_mode:
            return

        current_time = time.time()
        if self.audio_shutoff_time and current_time > self.audio_shutoff_time:
            log.info("Audio auto-shutoff timer reached")
            self._audio_off()

    def update_from_physical(self, state: dict):
        """Update state from physical button manager.

        Called by buttonmanager.py to sync state changes from physical buttons.

        Args:
            state: State dictionary from physical button manager
        """
        self.repeat_mode = state.get('repeat_mode', self.repeat_mode)
        self.audio_on = state.get('audio_on', self.audio_on)

        # Update button press tracking for stuck button detection
        if 'button_pressed' in state:
            button_name = state['button_pressed']
            if button_name not in self.button_press_start:
                self.button_press_start[button_name] = time.time()

        if 'button_released' in state:
            button_name = state['button_released']
            if button_name in self.button_press_start:
                del self.button_press_start[button_name]
