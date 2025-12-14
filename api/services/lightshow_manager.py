"""
Lightshow Manager Service

Manages the synchronized_lights.py subprocess and provides
control methods for the lightshow.
"""

import os
import sys
import signal
import subprocess
import logging
import time
from typing import Optional, Dict, Any
from enum import Enum
from threading import Thread, Lock

# Add py directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..', 'py'))

from api.core.config import APIConfig

log = logging.getLogger(__name__)


class LightshowState(str, Enum):
    """Lightshow states."""
    IDLE = "idle"
    STARTING = "starting"
    PLAYING = "playing"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class LightshowManager:
    """
    Manages the lightshow subprocess.

    This class provides control over the synchronized_lights.py process
    while maintaining backward compatibility with traditional CLI usage.
    """

    def __init__(self, config: APIConfig):
        """
        Initialize lightshow manager.

        Args:
            config: API configuration instance
        """
        self.config = config
        self.process: Optional[subprocess.Popen] = None
        self.state = LightshowState.IDLE
        self.current_song: Optional[str] = None
        self.current_playlist: Optional[str] = None
        self.lock = Lock()
        self.output_thread: Optional[Thread] = None
        self.error_message: Optional[str] = None

        # Get paths
        self.home = os.getenv("SYNCHRONIZED_LIGHTS_HOME")
        if not self.home:
            raise ValueError("SYNCHRONIZED_LIGHTS_HOME not set")

        self.python_bin = sys.executable
        self.synchronized_lights_script = os.path.join(
            self.home, "py", "synchronized_lights.py"
        )

        log.info("LightshowManager initialized")

    def _read_output(self):
        """Read subprocess output in background thread."""
        if not self.process:
            return

        try:
            for line in iter(self.process.stdout.readline, b''):
                if line:
                    decoded = line.decode('utf-8').strip()
                    log.info(f"[lightshow] {decoded}")

                    # Parse output for current song info
                    if "Playing:" in decoded or "Now playing:" in decoded:
                        # Extract song name from output
                        parts = decoded.split(":", 1)
                        if len(parts) > 1:
                            self.current_song = parts[1].strip()
        except Exception as e:
            log.error(f"Error reading subprocess output: {e}")

    def start(self, playlist: Optional[str] = None, song: Optional[str] = None) -> bool:
        """
        Start the lightshow.

        Args:
            playlist: Optional path to playlist file
            song: Optional path to single song file

        Returns:
            bool: True if started successfully
        """
        with self.lock:
            if self.state in [LightshowState.PLAYING, LightshowState.STARTING]:
                log.warning("Lightshow already running")
                return False

            try:
                self.state = LightshowState.STARTING
                self.error_message = None

                # Build command
                cmd = [self.python_bin, self.synchronized_lights_script]

                if playlist:
                    playlist = os.path.expandvars(playlist)
                    if not os.path.exists(playlist):
                        log.error(f"Playlist file not found: {playlist}")
                        self.state = LightshowState.ERROR
                        self.error_message = f"Playlist file not found: {playlist}"
                        return False
                    cmd.extend(["--playlist", playlist])
                    self.current_playlist = playlist
                elif song:
                    song = os.path.expandvars(song)
                    if not os.path.exists(song):
                        log.error(f"Song file not found: {song}")
                        self.state = LightshowState.ERROR
                        self.error_message = f"Song file not found: {song}"
                        return False
                    cmd.extend(["--file", song])
                    self.current_song = song
                else:
                    # Use default playlist from config
                    try:
                        default_playlist = self.config.lightshow.playlist_path
                        if default_playlist:
                            # Expand environment variables in path
                            default_playlist = os.path.expandvars(default_playlist)
                            # Validate file exists
                            if not os.path.exists(default_playlist):
                                log.error(f"Default playlist file not found: {default_playlist}")
                                self.state = LightshowState.ERROR
                                self.error_message = f"Playlist file not found: {default_playlist}"
                                return False
                            cmd.extend(["--playlist", default_playlist])
                            self.current_playlist = default_playlist
                        else:
                            log.error("No playlist or song specified")
                            self.state = LightshowState.ERROR
                            self.error_message = "No playlist or song specified"
                            return False
                    except AttributeError:
                        log.error("No playlist configured in defaults.cfg")
                        self.state = LightshowState.ERROR
                        self.error_message = "No playlist configured"
                        return False

                log.info(f"Starting lightshow: {' '.join(cmd)}")

                # Start subprocess
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=self.home,
                    env=os.environ.copy()
                )

                # Start output reader thread
                self.output_thread = Thread(target=self._read_output, daemon=True)
                self.output_thread.start()

                # Give it a moment to start
                time.sleep(1)

                # Check if process is still running
                if self.process.poll() is not None:
                    log.error("Lightshow process terminated immediately")
                    self.state = LightshowState.ERROR
                    self.error_message = "Process terminated immediately"
                    return False

                self.state = LightshowState.PLAYING
                log.info("Lightshow started successfully")
                return True

            except Exception as e:
                log.error(f"Failed to start lightshow: {e}")
                self.state = LightshowState.ERROR
                self.error_message = str(e)
                return False

    def stop(self, graceful: bool = True) -> bool:
        """
        Stop the lightshow.

        Args:
            graceful: If True, send SIGTERM; if False, send SIGKILL

        Returns:
            bool: True if stopped successfully
        """
        with self.lock:
            if not self.process:
                log.warning("No lightshow process running")
                self.state = LightshowState.IDLE
                return True

            try:
                self.state = LightshowState.STOPPING
                log.info(f"Stopping lightshow (graceful={graceful})")

                if graceful:
                    # Send SIGTERM for graceful shutdown
                    self.process.terminate()

                    # Wait up to 5 seconds for graceful shutdown
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        log.warning("Graceful shutdown timed out, forcing kill")
                        self.process.kill()
                        self.process.wait()
                else:
                    # Force kill
                    self.process.kill()
                    self.process.wait()

                self.process = None
                self.current_song = None
                self.state = LightshowState.STOPPED
                log.info("Lightshow stopped successfully")
                return True

            except Exception as e:
                log.error(f"Failed to stop lightshow: {e}")
                self.state = LightshowState.ERROR
                self.error_message = str(e)
                return False

    def skip(self) -> bool:
        """
        Skip to next song.

        Uses the state file to signal skip request.

        Returns:
            bool: True if skip signal sent successfully
        """
        try:
            # Use configuration manager's update_state
            self.config.update_state('play_now', '-1')
            log.info("Skip signal sent via state file")
            return True
        except Exception as e:
            log.error(f"Failed to send skip signal: {e}")
            return False

    def is_running(self) -> bool:
        """
        Check if lightshow process is running.

        Returns:
            bool: True if process is running
        """
        if not self.process:
            return False

        # Check if process has terminated
        poll = self.process.poll()
        if poll is not None:
            # Process terminated
            self.process = None
            if self.state == LightshowState.PLAYING:
                self.state = LightshowState.IDLE
            return False

        return True

    def get_status(self) -> Dict[str, Any]:
        """
        Get current lightshow status.

        Returns:
            dict: Status information
        """
        # Update state based on process status
        running = self.is_running()

        if not running and self.state == LightshowState.PLAYING:
            self.state = LightshowState.IDLE

        # Check state file for current song
        try:
            self.config.reload_state()
            now_playing = self.config.get_state('now_playing', '')
            if now_playing and now_playing != '0':
                self.current_song = now_playing
        except Exception as e:
            log.warning(f"Could not read state file: {e}")

        return {
            "state": self.state.value,
            "is_running": running,
            "current_song": self.current_song,
            "current_playlist": self.current_playlist,
            "error_message": self.error_message,
            "pid": self.process.pid if self.process else None
        }

    def restart(self) -> bool:
        """
        Restart the lightshow with current settings.

        Returns:
            bool: True if restarted successfully
        """
        playlist = self.current_playlist

        if not self.stop():
            return False

        time.sleep(1)

        return self.start(playlist=playlist)

    def cleanup(self):
        """Clean up resources."""
        if self.process:
            self.stop(graceful=True)


# Global instance
_lightshow_manager: Optional[LightshowManager] = None
_manager_lock = Lock()


def get_lightshow_manager(config: Optional[APIConfig] = None) -> LightshowManager:
    """
    Get or create the global lightshow manager instance.

    Args:
        config: Optional API config (required on first call)

    Returns:
        LightshowManager: Global manager instance
    """
    global _lightshow_manager

    with _manager_lock:
        if _lightshow_manager is None:
            if config is None:
                from api.core.config import get_api_config
                config = get_api_config()
            _lightshow_manager = LightshowManager(config)

        return _lightshow_manager


def set_lightshow_manager(manager: LightshowManager) -> None:
    """
    Set the global lightshow manager instance.

    This is used during application startup to inject the manager instance.

    Args:
        manager: LightshowManager instance to set as global
    """
    global _lightshow_manager

    with _manager_lock:
        _lightshow_manager = manager
        log.info("Global lightshow manager set")


if __name__ == "__main__":
    # Test the manager
    logging.basicConfig(level=logging.INFO)

    from api.core.config import get_api_config

    config = get_api_config()
    manager = LightshowManager(config)

    print("Status:", manager.get_status())
    print("\nStarting lightshow...")
    manager.start()

    print("Status:", manager.get_status())
    time.sleep(5)

    print("\nSkipping song...")
    manager.skip()
    time.sleep(3)

    print("\nStopping lightshow...")
    manager.stop()
    print("Status:", manager.get_status())
