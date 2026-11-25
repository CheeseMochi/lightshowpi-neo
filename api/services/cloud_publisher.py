"""
Push the public show data to the cloud copy of the Home page.

When ``[api] cloud_url`` is set, a daemon thread POSTs the same public views
the Pi serves at /api/public/* (now-playing, schedule, playlist) to
``<cloud_url>/ingest`` with ``Authorization: Bearer <cloud_key>``. The
Cloudflare Worker in cloud/worker/ stores the latest copy and serves it to
visitors, so crowds on the street never reach the Pi.

It pushes when something a visitor would see changes (song, state, playlist,
show times) and otherwise sends a heartbeat every few minutes; the Worker
reports the show "off" when heartbeats stop. Elapsed time is not a change:
the Worker advances it itself. Each push is one KV write, which keeps a night
well inside Cloudflare's free tier.

The payload is built only from api/routers/public.py's snapshot functions, so
the cloud never gets more than the public endpoints return. Network trouble
is retried with backoff and logged at most every few minutes; nothing here
ever raises into the API or touches the show.
"""

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Callable, Dict, Optional
from urllib.parse import urlparse

from api.routers import public

log = logging.getLogger(__name__)

POLL_SECONDS = 3.0           # how often to look for changes
HEARTBEAT_SECONDS = 300.0    # push at least this often (the Worker's staleness is 15 min)
TIMEOUT_SECONDS = 5.0        # per HTTP request
BACKOFF_START_SECONDS = 5.0
BACKOFF_MAX_SECONDS = 300.0
LOG_EVERY_SECONDS = 600.0    # repeat a failure warning at most this often
USER_AGENT = "lightshowpi-neo-cloud-publisher"


def build_payload(api_config, scheduler=None, now: Optional[float] = None) -> Dict:
    """
    The public snapshot to push: ``{now_playing, song_started_at, schedule,
    playlist, sent_at}``. ``now_playing``, ``schedule`` and ``playlist`` are
    exactly the bodies of the matching /api/public endpoints.
    """
    now = time.time() if now is None else now
    try:
        state = public.read_state(api_config)
    except Exception as e:
        log.debug(f"Cloud publisher could not read the state file: {e}")
        state = None

    def read():
        return state

    now_playing = public.now_playing_snapshot(read if state is not None else None)
    # The Worker advances elapsed time from this (corrected by sent_at for clock skew)
    started = None
    if now_playing.state == "playing" and now_playing.elapsed_s is not None:
        started = public._float(state.get("song_started_at"))

    schedule = public.schedule_snapshot(scheduler, public.config_schedule_note(api_config),
                                        public.config_show_times(api_config))
    playlist = public.playlist_snapshot(lambda: public.read_playlist_entries(api_config),
                                        read if state is not None else None,
                                        public.config_home_dir(api_config))
    return {
        "now_playing": now_playing.model_dump(),
        "song_started_at": started,
        "schedule": schedule.model_dump(),
        "playlist": playlist.model_dump(),
        "sent_at": round(now, 3),
    }


def change_key(payload: Dict) -> str:
    """What counts as a change: everything except the moving clocks."""
    now_playing = dict(payload.get("now_playing") or {})
    now_playing.pop("elapsed_s", None)
    return json.dumps({
        "now_playing": now_playing,
        "song_started_at": payload.get("song_started_at"),
        "schedule": payload.get("schedule"),
        "playlist": payload.get("playlist"),
    }, sort_keys=True, default=str)


def http_post(url: str, key: str, payload: Dict, timeout: float = TIMEOUT_SECONDS) -> int:
    """POST ``payload`` as JSON with the bearer key; return the HTTP status or raise."""
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
        "User-Agent": USER_AGENT,
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 (configured https URL)
        return response.status


class CloudPublisher:
    """Background thread that pushes public snapshots to ``<cloud_url>/ingest``."""

    def __init__(self, cloud_url: str, cloud_key: str, collect: Callable[[], Dict],
                 post: Callable[[str, str, Dict], int] = http_post,
                 clock: Callable[[], float] = time.monotonic,
                 poll_s: float = POLL_SECONDS, heartbeat_s: float = HEARTBEAT_SECONDS):
        self.ingest_url = cloud_url.strip().rstrip("/") + "/ingest"
        self._key = cloud_key
        self._collect = collect
        self._post = post
        self._clock = clock
        self.poll_s = poll_s
        self.heartbeat_s = heartbeat_s

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_key: Optional[str] = None
        self._last_sent: Optional[float] = None
        self._backoff = 0.0
        self._retry_at = 0.0
        self._failing_since: Optional[float] = None
        self._last_warning = float("-inf")
        self.pushes = 0
        self.failures = 0

    # -- one step (tests drive this directly) --------------------------------

    def tick(self) -> bool:
        """Collect, and push if changed or due for a heartbeat. True if a push succeeded. Never raises."""
        try:
            return self._tick()
        except Exception as e:  # belt and braces: the thread must never die
            self._warn(f"Cloud publisher error: {e}")
            return False

    def _tick(self) -> bool:
        now = self._clock()
        if now < self._retry_at:
            return False
        try:
            payload = self._collect()
        except Exception as e:
            self._warn(f"Cloud publisher could not build the snapshot: {e}")
            return False

        key = change_key(payload)
        due = self._last_sent is None or now - self._last_sent >= self.heartbeat_s
        if key == self._last_key and not due:
            return False

        try:
            status = self._post(self.ingest_url, self._key, payload)
            if not 200 <= int(status) < 300:
                raise RuntimeError(f"HTTP {status}")
        except Exception as e:
            self._failed(now, e)
            return False

        if self._failing_since is not None:
            log.info(f"Cloud publisher reconnected to {self._host()}")
        self._failing_since = None
        self._backoff = 0.0
        self._retry_at = 0.0
        self._last_key = key
        self._last_sent = now
        self.pushes += 1
        return True

    def _failed(self, now: float, error: Exception) -> None:
        self.failures += 1
        self._backoff = min(BACKOFF_MAX_SECONDS,
                            self._backoff * 2 if self._backoff else BACKOFF_START_SECONDS)
        self._retry_at = now + self._backoff
        if self._failing_since is None:
            self._failing_since = now
        hint = ""
        if isinstance(error, urllib.error.HTTPError) and error.code in (401, 403):
            hint = " (check [api] cloud_key matches the Worker's INGEST_KEY)"
        self._warn(f"Cloud publisher could not reach {self._host()}: {error}{hint}; "
                   f"retrying in {int(self._backoff)}s")

    def _warn(self, message: str) -> None:
        now = self._clock()
        if now - self._last_warning >= LOG_EVERY_SECONDS:
            self._last_warning = now
            log.warning(message)
        else:
            log.debug(message)

    def _host(self) -> str:
        return urlparse(self.ingest_url).netloc or self.ingest_url

    # -- thread --------------------------------------------------------------

    def start(self) -> None:
        """Start the daemon thread (no-op if already running)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="cloud-publisher", daemon=True)
        self._thread.start()
        log.info(f"Cloud publisher started (pushing to {self._host()})")

    def stop(self, timeout: float = 2.0) -> None:
        """Ask the thread to stop and wait briefly; a hung request can't hold up shutdown."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(self.poll_s)


def start_cloud_publisher(api_config, get_scheduler: Callable[[], object] = lambda: None
                          ) -> Optional[CloudPublisher]:
    """
    Start pushing to ``[api] cloud_url`` if it is set; return the publisher or None.

    Blank ``cloud_url`` (the default) means off. A URL without ``cloud_key``
    is refused, since the Worker would reject every push anyway.
    """
    url = (getattr(api_config, "cloud_url", "") or "").strip()
    key = (getattr(api_config, "cloud_key", "") or "").strip()
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        log.warning("[api] cloud_url must be an http(s) URL; cloud publishing is off")
        return None
    if not key:
        log.warning("[api] cloud_url is set but cloud_key is blank; cloud publishing is off")
        return None
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1"):
        log.warning("[api] cloud_url is plain http: the cloud key is sent unencrypted")

    def collect():
        scheduler = None
        try:
            scheduler = get_scheduler()
        except Exception:
            pass
        return build_payload(api_config, scheduler)

    publisher = CloudPublisher(url, key, collect)
    publisher.start()
    return publisher
