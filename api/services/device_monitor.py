"""
Background reachability checks for devices (``[devices]`` in the config).

Every ``probe_interval`` seconds a daemon thread probes each enabled device in a
small thread pool, so the API never waits on the network:

- ``pi``: TCP connect to its port (default 22, ssh)
- ``output`` with role ``nanoleaf``: TCP connect to port 16021 only. The
  auth token is never used or logged.
- ``output`` with role ``sacn_led`` (ESPixelStick and similar): HTTP GET
  ``http://<address>/``. Any HTTP answer, even an error status, counts as up.
- this host (``self``): always ok, with API version and uptimes
- relays and devices without an address: not probed

Timeouts are capped at 2 s. Only stdlib (socket, urllib) is used, so tests can
mock the network.
"""

import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, List, Optional, Tuple

log = logging.getLogger(__name__)

MAX_TIMEOUT_S = 2.0
MIN_INTERVAL_S = 10.0
MAX_WORKERS = 8

ProbeResult = Tuple[bool, Optional[str]]


def probe_tcp(host: str, port: int, timeout: float) -> ProbeResult:
    """True if a TCP connection to host:port opens within ``timeout``."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True, None
    except socket.timeout:
        return False, f"timeout connecting to port {port}"
    except OSError as e:
        return False, f"port {port}: {e.strerror or e.__class__.__name__}"


def probe_http(url: str, timeout: float) -> ProbeResult:
    """True if ``url`` answers HTTP at all (any status) within ``timeout``."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            resp.read(256)
        return True, None
    except urllib.error.HTTPError:
        return True, None  # it answered; the status doesn't matter for reachability
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        return False, f"http: {getattr(reason, 'strerror', None) or reason}"
    except (socket.timeout, TimeoutError):
        return False, "http: timeout"
    except OSError as e:
        return False, f"http: {e.strerror or e.__class__.__name__}"


def host_uptime_s() -> Optional[float]:
    """Seconds since boot (Linux /proc/uptime), or None elsewhere."""
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


class DeviceMonitor:
    """Periodic device probes on a daemon thread."""

    def __init__(self, store, interval_s: float = 60.0, timeout_s: float = 2.0,
                 enabled: bool = True, version: str = "", start_time: Optional[float] = None,
                 tcp: Callable = probe_tcp, http: Callable = probe_http):
        self.store = store
        self.interval_s = max(MIN_INTERVAL_S, float(interval_s or 60.0))
        self.timeout_s = min(MAX_TIMEOUT_S, max(0.1, float(timeout_s or MAX_TIMEOUT_S)))
        self.enabled = enabled
        self.version = version
        self.start_time = start_time or time.time()
        self._tcp = tcp
        self._http = http
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.last_run: Optional[float] = None

    # -- one device ------------------------------------------------------------

    def self_info(self) -> dict:
        return {
            "version": self.version,
            "api_uptime_s": round(time.time() - self.start_time),
            "host_uptime_s": host_uptime_s(),
            "pid": os.getpid(),
        }

    def probe(self, device: dict) -> Optional[Tuple[bool, Optional[str], Optional[dict]]]:
        """Probe one device: (ok, error, info), or None if it isn't probed."""
        if device.get("config_key") == "self":
            return True, None, self.self_info()
        address = (device.get("address") or "").strip()
        if device.get("kind") == "relay" or not address:
            return None
        role = (device.get("role") or "").lower()
        if device.get("kind") == "pi":
            ok, err = self._tcp(address, device.get("port") or 22, self.timeout_s)
        elif role == "sacn_led":
            ok, err = self._http(f"http://{address}/", self.timeout_s)
        elif role == "nanoleaf":
            ok, err = self._tcp(address, device.get("port") or 16021, self.timeout_s)
        elif device.get("port"):
            ok, err = self._tcp(address, device["port"], self.timeout_s)
        else:
            return None
        return ok, err, None

    # -- all devices -----------------------------------------------------------

    def probe_once(self) -> List[dict]:
        """Probe every enabled device now and store the results."""
        with self._lock:
            devices = [d for d in self.store.list() if d["enabled"]]

            def run(device):
                try:
                    return device, self.probe(device)
                except Exception as e:  # a probe bug must not kill the monitor
                    log.warning("Probe of %s failed: %s", device["name"], e)
                    return device, (False, "probe error", None)

            results = []
            if devices:
                with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(devices))) as pool:
                    outcomes = list(pool.map(run, devices))
                for device, outcome in outcomes:
                    if outcome is None:
                        continue
                    ok, err, info = outcome
                    self.store.record_status(device["id"], ok, err, info)
                    results.append({"id": device["id"], "name": device["name"],
                                    "ok": ok, "error": err})
            self.last_run = time.time()
            return results

    # -- thread ------------------------------------------------------------------

    def start(self) -> bool:
        """Start the background thread (no-op when disabled or already running)."""
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="device-monitor", daemon=True)
        self._thread.start()
        log.info("Device monitor started (every %.0fs, timeout %.1fs)",
                 self.interval_s, self.timeout_s)
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=MAX_TIMEOUT_S * 2)
            self._thread = None

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.probe_once()
            except Exception as e:
                log.warning("Device probe round failed: %s", e)
            self._stop.wait(self.interval_s)

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "running": self.is_running(),
            "interval_s": self.interval_s,
            "timeout_s": self.timeout_s,
            "last_run": (time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.last_run))
                         if self.last_run else None),
        }
