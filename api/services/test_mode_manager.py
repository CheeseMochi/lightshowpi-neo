"""
Test Mode Manager Service

Owns a long-running `hardware_controller.py --serve` subprocess that lets an
admin switch individual channels on/off, all channels, or run test patterns.

Test mode is exclusive with the lightshow: it refuses to start while the show
is running, and LightshowManager / SchedulerService refuse to start the show
while test mode is active. After a period without commands it exits on its
own (watchdog) so lights are never left on indefinitely.
"""

import json
import logging
import os
import queue
import subprocess
import sys
import time
from threading import Event, Lock, Thread
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

# Seconds to wait for the serve process to report it is ready
DEFAULT_START_TIMEOUT = 20.0
# Seconds to wait for a reply to a normal command
DEFAULT_READ_TIMEOUT = 5.0
# Seconds to wait for a pattern to finish (patterns block until done)
DEFAULT_PATTERN_TIMEOUT = 300.0


class TestModeError(Exception):
    """Base error for test mode operations."""

    __test__ = False  # not a pytest test class, despite the name


class TestModeConflict(TestModeError):
    """Test mode cannot be entered (e.g. the lightshow is running)."""


class TestModeNotActive(TestModeError):
    """A command was sent while test mode is not active."""


class TestModeCommandError(TestModeError):
    """The serve process rejected a command (ok=false)."""


class TestModeManager:
    """Manages the hardware_controller --serve subprocess used for test mode."""

    # Not a pytest test class, despite the name
    __test__ = False

    def __init__(
        self,
        timeout_minutes: float = 10.0,
        lightshow_manager=None,
        command: Optional[List[str]] = None,
        channel_count: Optional[int] = None,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        pattern_timeout: float = DEFAULT_PATTERN_TIMEOUT,
        start_timeout: float = DEFAULT_START_TIMEOUT,
        watchdog_interval: Optional[float] = None,
        clock: Callable[[], float] = time.time,
    ):
        """
        Args:
            timeout_minutes: Auto-exit after this many minutes without commands
            lightshow_manager: LightshowManager used to refuse entry while the show runs
            command: Command used to spawn the serve process (injectable for tests)
            channel_count: Channel count to report before the serve process is ready
            read_timeout: Seconds to wait for a reply to a normal command
            pattern_timeout: Seconds to wait for a pattern to finish
            start_timeout: Seconds to wait for the ready line on enter()
            watchdog_interval: Seconds between watchdog checks (default derived from timeout)
            clock: Time source (injectable for tests)
        """
        self.timeout_seconds = max(1.0, float(timeout_minutes) * 60.0)
        self.lightshow_manager = lightshow_manager
        # Fall back to the repo root so the command path is never relative
        self.home = os.getenv("SYNCHRONIZED_LIGHTS_HOME") or os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", ".."))
        self.command = command or [
            sys.executable,
            os.path.join(self.home, "py", "hardware_controller.py"),
            "--serve",
        ]
        self.read_timeout = read_timeout
        self.pattern_timeout = pattern_timeout
        self.start_timeout = start_timeout
        self.clock = clock

        self.process: Optional[subprocess.Popen] = None
        self.channel_count: Optional[int] = channel_count
        self.channels: List[float] = []
        self.last_activity: Optional[float] = None
        self.entered_by: Optional[str] = None
        self.exit_reason: Optional[str] = None

        self._state_lock = Lock()   # enter/exit
        self._io_lock = Lock()      # one command at a time
        self._busy = False
        self._replies: "queue.Queue[Optional[str]]" = queue.Queue()

        if watchdog_interval is None:
            watchdog_interval = min(5.0, max(0.1, self.timeout_seconds / 10.0))
        self.watchdog_interval = watchdog_interval
        self._watchdog_stop = Event()
        self._watchdog: Optional[Thread] = None

    # ------------------------------------------------------------------ state

    def is_active(self) -> bool:
        """True while the serve process is running."""
        return self.process is not None and self.process.poll() is None

    def seconds_remaining(self) -> Optional[float]:
        """Seconds until the watchdog exits test mode (None when inactive)."""
        if not self.is_active() or self.last_activity is None:
            return None
        return max(0.0, self.timeout_seconds - (self.clock() - self.last_activity))

    def status(self) -> Dict[str, Any]:
        active = self.is_active()
        if not active and self.process is not None:
            # Process died on its own; tidy up
            self._reap(reason=self.exit_reason or "process exited")
        return {
            "active": active,
            "channel_count": self.channel_count,
            "channels": list(self.channels) if active else [],
            "timeout_seconds": self.timeout_seconds,
            "seconds_remaining": self.seconds_remaining(),
            "entered_by": self.entered_by if active else None,
            "exit_reason": None if active else self.exit_reason,
            "pid": self.process.pid if active and self.process else None,
        }

    # ------------------------------------------------------------ lifecycle

    def enter(self, by: Optional[str] = None) -> Dict[str, Any]:
        """Start test mode. Idempotent if already active.

        Raises:
            TestModeConflict: if the lightshow is running
            TestModeError: if the serve process fails to start
        """
        with self._state_lock:
            if self.is_active():
                self.last_activity = self.clock()
                return self.status()

            if self.lightshow_manager is not None and self.lightshow_manager.is_running():
                raise TestModeConflict("Lightshow is running - stop it before entering test mode")

            log.info(f"Entering test mode (by {by or 'unknown'}): {' '.join(self.command)}")
            self._replies = queue.Queue()
            try:
                self.process = subprocess.Popen(
                    self.command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    cwd=self.home or None,
                    env=os.environ.copy(),
                )
            except OSError as e:
                self.process = None
                raise TestModeError(f"Failed to start test mode process: {e}")

            Thread(target=self._read_stdout, args=(self.process,), daemon=True).start()
            Thread(target=self._read_stderr, args=(self.process,), daemon=True).start()

            try:
                ready = self._read_reply(self.start_timeout)
            except TestModeError as e:
                self._kill()
                raise TestModeError(f"Test mode process did not become ready: {e}")

            if not ready.get("ok") or not ready.get("ready"):
                self._kill()
                raise TestModeError(f"Test mode process failed to start: {ready.get('error')}")

            self.channel_count = ready.get("channel_count", self.channel_count)
            self.channels = list(ready.get("channels", []))
            self.entered_by = by
            self.exit_reason = None
            self.last_activity = self.clock()
            self._start_watchdog()
            log.info(f"Test mode active ({self.channel_count} channels)")
            return self.status()

    def exit(self, reason: str = "requested") -> Dict[str, Any]:
        """Stop test mode: lights off, GPIO released. Safe to call when inactive."""
        with self._state_lock:
            if self.process is None:
                return self.status()

            log.info(f"Exiting test mode ({reason})")
            if self.is_active():
                # Ask politely; skip if a long pattern holds the IO lock
                if self._io_lock.acquire(timeout=2):
                    try:
                        self._write({"cmd": "quit"})
                        try:
                            self._read_reply(self.read_timeout)
                        except TestModeError:
                            pass
                    finally:
                        self._io_lock.release()
                try:
                    self.process.stdin.close()
                except Exception:
                    pass
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    log.warning("Test mode process did not exit, terminating")
                    self._kill()

            self._reap(reason=reason)
            return self.status()

    def shutdown(self):
        """Exit test mode and stop the watchdog (API shutdown)."""
        self._watchdog_stop.set()
        self.exit(reason="api shutdown")

    # ------------------------------------------------------------- commands

    def send(self, cmd: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
        """Send one command and return the reply.

        Raises:
            TestModeNotActive: test mode is not running
            TestModeCommandError: the serve process replied ok=false
            TestModeError: no reply in time / process died (test mode is exited)
        """
        if timeout is None:
            timeout = self.pattern_timeout if cmd.get("cmd") == "pattern" else self.read_timeout

        with self._io_lock:
            if not self.is_active():
                raise TestModeNotActive("Test mode is not active")
            self._busy = True
            self.last_activity = self.clock()
            try:
                self._write(cmd)
                reply = self._read_reply(timeout)
            except TestModeError:
                self._busy = False
                # Protocol is out of sync or process is gone - bail out safely
                Thread(target=self.exit, kwargs={"reason": "command failed"}, daemon=True).start()
                raise
            finally:
                self._busy = False
                self.last_activity = self.clock()

        if "channels" in reply:
            self.channels = list(reply["channels"])
        if not reply.get("ok"):
            raise TestModeCommandError(reply.get("error") or "command failed")
        return reply

    def set_channel(self, channel: int, on: bool, brightness: Optional[float] = None):
        cmd = {"cmd": "set", "channel": int(channel), "on": bool(on)}
        if brightness is not None:
            cmd["brightness"] = float(brightness)
        return self.send(cmd)

    def set_all(self, on: bool):
        return self.send({"cmd": "all", "on": bool(on)})

    def run_pattern(self, name: str):
        return self.send({"cmd": "pattern", "name": name})

    # -------------------------------------------------------------- helpers

    def _write(self, cmd: Dict[str, Any]):
        try:
            self.process.stdin.write(json.dumps(cmd) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError, AttributeError) as e:
            raise TestModeError(f"Test mode process is not accepting commands: {e}")

    def _read_reply(self, timeout: float) -> Dict[str, Any]:
        try:
            line = self._replies.get(timeout=timeout)
        except queue.Empty:
            raise TestModeError(f"No reply from test mode process within {timeout:.0f}s")
        if line is None:
            raise TestModeError("Test mode process exited")
        try:
            return json.loads(line)
        except ValueError:
            raise TestModeError(f"Invalid reply from test mode process: {line!r}")

    def _read_stdout(self, process: subprocess.Popen):
        replies = self._replies
        try:
            for line in iter(process.stdout.readline, ""):
                line = line.strip()
                if line:
                    replies.put(line)
        except Exception as e:
            log.debug(f"Test mode stdout reader stopped: {e}")
        finally:
            replies.put(None)

    @staticmethod
    def _read_stderr(process: subprocess.Popen):
        try:
            for line in iter(process.stderr.readline, ""):
                if line.strip():
                    log.info(f"[test_mode] {line.rstrip()}")
        except Exception:
            pass

    def _kill(self):
        if self.process is None:
            return
        try:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
        except Exception as e:
            log.error(f"Error stopping test mode process: {e}")

    def _reap(self, reason: str):
        self.process = None
        self.channels = []
        self.last_activity = None
        self.entered_by = None
        self.exit_reason = reason

    # ------------------------------------------------------------- watchdog

    def _start_watchdog(self):
        if self._watchdog is not None and self._watchdog.is_alive():
            return
        self._watchdog_stop.clear()
        self._watchdog = Thread(target=self._watchdog_loop, daemon=True, name="test-mode-watchdog")
        self._watchdog.start()

    def _watchdog_loop(self):
        while not self._watchdog_stop.wait(self.watchdog_interval):
            if self.process is None:
                continue
            if not self.is_active():
                log.warning("Test mode process exited unexpectedly")
                with self._state_lock:
                    self._reap(reason="process exited")
                continue
            if self._busy or self.last_activity is None:
                continue
            if self.clock() - self.last_activity >= self.timeout_seconds:
                log.info(f"Test mode idle for {self.timeout_seconds:.0f}s - exiting")
                self.exit(reason="timeout")


# Global instance (set by main.py)
_test_mode_manager: Optional[TestModeManager] = None


def set_test_mode_manager(manager: Optional[TestModeManager]) -> None:
    global _test_mode_manager
    _test_mode_manager = manager


def get_test_mode_manager() -> Optional[TestModeManager]:
    return _test_mode_manager
