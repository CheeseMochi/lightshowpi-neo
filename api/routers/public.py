"""
Public, read-only API for the show page (no login).

These endpoints are reachable from the internet through the tunnel's public
hostname, so they return only what a viewer on the street should see: whether
the show is on, the song title, the playlist titles and tonight's times. No
IPs, file paths, client details or controls. Responses use explicit models
(extra keys can't leak), are cached for a few seconds and are rate-limited per
client IP.

The song comes from config/state.cfg, which synchronized_lights.py writes
however the show was started (cron, CLI, buttons or the API).

The ``*_snapshot`` functions build each view and never raise. The endpoints
and the cloud publisher (api/services/cloud_publisher.py) both use them, so
the cloud copy can never carry more than the Pi serves itself.
"""

import logging
import os
import re
import threading
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict

import api.core.config  # noqa: F401  (puts py/ on sys.path)
from api.core.rate_limit import RateLimiter, client_ip
from api.routers.schedules import get_optional_scheduler
from configuration_manager import read_playlist

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/public", tags=["Public"])

# Per-IP request budget for all public endpoints together
PUBLIC_REQUESTS_PER_MINUTE = 60
CACHE_SECONDS = 3.0
# A song whose start + duration is further in the past than this is stale
# (the show crashed or was killed without clearing the state file).
STALE_GRACE_SECONDS = 60.0
# song_title values that are a mode name, not a song
MODE_TITLES = {"audio-in", "stream-in", "playlist", "ambient", ""}
DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]  # DB order: 0 = Sunday
# Longest playlist the public page lists
MAX_PUBLIC_SONGS = 500
# State keys the public views read. song_path is used only to find the
# current playlist entry and is never returned.
STATE_KEYS = ("now_playing", "song_title", "song_duration", "song_started_at", "song_path")

public_limiter = RateLimiter(max_events=PUBLIC_REQUESTS_PER_MINUTE, window_s=60.0)


class PublicNowPlaying(BaseModel):
    """What's on right now."""
    model_config = ConfigDict(extra="forbid")

    state: str  # "playing", "ambient" or "off"
    title: Optional[str] = None
    elapsed_s: Optional[float] = None
    duration_s: Optional[float] = None


class PublicShowTime(BaseModel):
    """One scheduled show window."""
    model_config = ConfigDict(extra="forbid")

    days: List[str]
    start: str  # "HH:MM", Pi local time
    stop: str


class PublicSchedule(BaseModel):
    """The show schedule as viewers should see it."""
    model_config = ConfigDict(extra="forbid")

    note: Optional[str] = None
    tonight: Optional[PublicShowTime] = None
    shows: List[PublicShowTime] = []


class PublicPlaylist(BaseModel):
    """Song titles in playlist order; ``current`` is the index playing now."""
    model_config = ConfigDict(extra="forbid")

    songs: List[str] = []
    current: Optional[int] = None


# ---------------------------------------------------------------------------
# Wiring (set from main.py, overridable in tests)
# ---------------------------------------------------------------------------

_api_config = None

# reload_state() mutates the shared Configuration; the router and the cloud
# publisher thread both read through read_state() so they never interleave.
_state_lock = threading.Lock()


def set_config(api_config) -> None:
    """Give the router the APIConfig (state file access and [api] settings)."""
    global _api_config
    _api_config = api_config
    _cache.clear()


def read_state(api_config) -> Dict[str, str]:
    """Current show state from config/state.cfg (only the keys public views use)."""
    with _state_lock:
        api_config.reload_state()
        return {key: api_config.get_state(key, "") for key in STATE_KEYS}


def read_playlist_entries(api_config) -> List[list]:
    """Entries of ``[lightshow] playlist_path``; empty when unset or missing. Never exits."""
    path = getattr(getattr(api_config, "lightshow", None), "playlist_path", "") or ""
    if not path or not os.path.isfile(path):
        return []
    return read_playlist(path)


def config_home_dir(api_config) -> str:
    """$SYNCHRONIZED_LIGHTS_HOME as the config sees it (expands playlist paths)."""
    return getattr(getattr(api_config, "cm", None), "home_dir", "") or ""


def config_schedule_note(api_config) -> Optional[str]:
    """``[api] public_schedule``, free text shown on the public page."""
    note = (api_config._get_str("api", "public_schedule", "") or "").strip()
    return note or None


def config_show_times(api_config) -> Optional["PublicShowTime"]:
    """The ``[api] public_show_*`` window, or None when unset or invalid."""
    return parse_show_times(getattr(api_config, "public_show_start", ""),
                            getattr(api_config, "public_show_stop", ""),
                            getattr(api_config, "public_show_days", ""))


def get_state_reader() -> Optional[Callable[[], Dict[str, str]]]:
    """Dependency: a function returning the current show state, or None."""
    if _api_config is None:
        return None
    config = _api_config
    return lambda: read_state(config)


def get_playlist_reader() -> Optional[Callable[[], List[list]]]:
    """Dependency: a function returning the playlist entries, or None."""
    if _api_config is None:
        return None
    config = _api_config
    return lambda: read_playlist_entries(config)


def get_home_dir() -> str:
    """Dependency: the home dir used to expand playlist paths."""
    return "" if _api_config is None else config_home_dir(_api_config)


def get_schedule_note() -> Optional[str]:
    """Dependency: [api] public_schedule, free text shown on the public page."""
    return None if _api_config is None else config_schedule_note(_api_config)


def get_show_times() -> Optional["PublicShowTime"]:
    """Dependency: the ``[api] public_show_*`` fallback window, or None."""
    return None if _api_config is None else config_show_times(_api_config)


def rate_limit(request: Request) -> None:
    """Dependency: per-IP budget shared by all public endpoints."""
    ip = client_ip(request)
    if not public_limiter.hit(ip):
        retry = int(public_limiter.retry_after(ip)) + 1
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="Too many requests", headers={"Retry-After": str(retry)})


# ---------------------------------------------------------------------------
# Small TTL cache: many viewers polling cost one state-file read per few seconds
# ---------------------------------------------------------------------------

_cache: Dict[str, Tuple[float, object]] = {}
_cache_lock = threading.Lock()


def cached(key: str, compute: Callable[[], object], now: Optional[float] = None):
    """Return the cached value for ``key`` or compute and store it."""
    now = time.monotonic() if now is None else now
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    value = compute()
    with _cache_lock:
        _cache[key] = (now, value)
    return value


def _float(value) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def public_title(raw: str) -> Optional[str]:
    """Song title safe to show publicly: no directories, no file extension, no mode names."""
    title = os.path.basename((raw or "").strip())
    stem, ext = os.path.splitext(title)
    if ext.lower() in (".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma", ".mp4"):
        title = stem
    title = title.replace("_", " ").strip()
    if title.lower() in MODE_TITLES:
        return None
    return title[:120] or None


def build_now_playing(state: Optional[Dict[str, str]], now: Optional[float] = None) -> PublicNowPlaying:
    """Turn raw state-file values into the public now-playing view."""
    if not state or str(state.get("now_playing", "0")).strip() != "1":
        return PublicNowPlaying(state="off")

    now = time.time() if now is None else now
    raw_title = state.get("song_title", "") or ""
    started = _float(state.get("song_started_at"))
    duration = _float(state.get("song_duration"))

    if started is not None and duration is not None and now > started + duration + STALE_GRACE_SECONDS:
        return PublicNowPlaying(state="off")

    if raw_title.strip().lower() == "ambient":
        return PublicNowPlaying(state="ambient")

    elapsed = None
    if started is not None:
        elapsed = max(0.0, now - started)
        if duration is not None:
            elapsed = min(elapsed, duration)
    return PublicNowPlaying(state="playing", title=public_title(raw_title),
                            elapsed_s=round(elapsed, 1) if elapsed is not None else None,
                            duration_s=round(duration, 1) if duration is not None else None)


def _show_time(schedule: Dict) -> PublicShowTime:
    days = sorted(int(d) for d in schedule.get("days_of_week") or [] if 0 <= int(d) <= 6)
    return PublicShowTime(days=[DAY_NAMES[d] for d in days],
                          start=str(schedule["start_time"]), stop=str(schedule["stop_time"]))


_HHMM = re.compile(r"^(\d{1,2}):(\d{2})$")


def _hhmm(value: str) -> Optional[str]:
    match = _HHMM.match((value or "").strip())
    if not match:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    if hours > 23 or minutes > 59:
        return None
    return f"{hours:02d}:{minutes:02d}"


def parse_show_times(start: str, stop: str, days: str = "") -> Optional[PublicShowTime]:
    """
    The configured public show window (``[api] public_show_start/stop/days``).

    Blank start or stop means no window (None). Times are 24-hour HH:MM; days
    are comma separated names (Sun..Sat, any case, the first three letters
    count), blank for every day. Anything invalid logs a warning and gives
    None, so a typo hides the times instead of showing wrong ones.
    """
    if not (start or "").strip() or not (stop or "").strip():
        return None
    start_s, stop_s = _hhmm(start), _hhmm(stop)
    if start_s is None or stop_s is None:
        log.warning(f"Ignoring [api] public_show_start/stop: expected HH:MM, got {start!r}/{stop!r}")
        return None
    wanted = [d.strip()[:3].title() for d in (days or "").replace(" ", ",").split(",") if d.strip()]
    if any(d not in DAY_NAMES for d in wanted):
        log.warning(f"Ignoring [api] public_show_days: unknown day in {days!r}")
        return None
    day_list = [d for d in DAY_NAMES if d in wanted] if wanted else list(DAY_NAMES)
    return PublicShowTime(days=day_list, start=start_s, stop=stop_s)


def build_schedule(schedules: List[Dict], note: Optional[str],
                   today: Optional[datetime] = None,
                   fallback: Optional[PublicShowTime] = None) -> PublicSchedule:
    """
    Enabled API schedules plus the configured note, with tonight's window picked out.

    ``fallback`` (the ``[api] public_show_*`` window) is used only when there
    are no enabled API schedules, as when the show runs from cron.
    """
    today = today or datetime.now()
    today_db = (today.weekday() + 1) % 7  # Python Monday=0 -> DB Sunday=0
    shows: List[PublicShowTime] = []
    for schedule in schedules:
        if not schedule.get("enabled", True):
            continue
        try:
            shows.append(_show_time(schedule))
        except (KeyError, TypeError, ValueError) as e:
            log.warning(f"Skipping malformed schedule in public view: {e}")
    if not shows and fallback is not None:
        shows = [fallback]
    tonight = next((show for show in shows if DAY_NAMES[today_db] in show.days), None)
    return PublicSchedule(note=note, tonight=tonight, shows=shows)


def _resolve_song_path(path: str, home_dir: str) -> str:
    path = (path or "").strip().replace("$SYNCHRONIZED_LIGHTS_HOME", home_dir or "")
    return os.path.abspath(path) if path else ""


def build_playlist(entries: List[list], state: Optional[Dict[str, str]] = None,
                   home_dir: str = "", now: Optional[float] = None) -> PublicPlaylist:
    """
    Public titles of the playlist entries (never paths), and which one is playing.

    The current song is found by comparing the state file's ``song_path`` with
    each entry's path, because the now-playing title comes from the song's tags
    and needn't match the playlist name.
    """
    playing_path = ""
    if state and build_now_playing(state, now=now).state == "playing":
        playing_path = _resolve_song_path(state.get("song_path", ""), home_dir)
    songs: List[str] = []
    current = None
    for entry in entries or []:
        if len(songs) >= MAX_PUBLIC_SONGS:
            break
        try:
            title = public_title(entry[0])
        except (IndexError, TypeError, AttributeError):
            continue
        if not title:
            continue
        if (playing_path and current is None and len(entry) > 1
                and _resolve_song_path(entry[1], home_dir) == playing_path):
            current = len(songs)
        songs.append(title)
    return PublicPlaylist(songs=songs, current=current)


# ---------------------------------------------------------------------------
# Snapshots shared by the endpoints and the cloud publisher. They never raise:
# a broken state file or playlist shows as "off" or empty, not an error.
# ---------------------------------------------------------------------------

def now_playing_snapshot(read: Optional[Callable[[], Dict[str, str]]]) -> PublicNowPlaying:
    """Now-playing from a state reader; "off" when there is none or it fails."""
    if read is None:
        return PublicNowPlaying(state="off")
    try:
        return build_now_playing(read())
    except Exception as e:
        log.warning(f"Public now-playing could not read the state file: {e}")
        return PublicNowPlaying(state="off")


def schedule_snapshot(scheduler, note: Optional[str],
                      fallback: Optional[PublicShowTime] = None) -> PublicSchedule:
    """Public schedule from the scheduler (may be None), the note and the fallback window."""
    schedules: List[Dict] = []
    if scheduler is not None:
        try:
            schedules = scheduler.get_schedules(enabled_only=True)
        except Exception as e:
            log.warning(f"Public schedule could not read schedules: {e}")
    try:
        return build_schedule(schedules, note, fallback=fallback)
    except Exception as e:
        log.warning(f"Public schedule could not be built: {e}")
        return PublicSchedule(note=note)


def playlist_snapshot(read_entries: Optional[Callable[[], List[list]]],
                      read: Optional[Callable[[], Dict[str, str]]] = None,
                      home_dir: str = "") -> PublicPlaylist:
    """Public playlist; empty when there is no playlist or it can't be read."""
    if read_entries is None:
        return PublicPlaylist()
    try:
        entries = read_entries()
    except Exception as e:
        log.warning(f"Public playlist could not read the playlist: {e}")
        return PublicPlaylist()
    state = None
    if read is not None:
        try:
            state = read()
        except Exception as e:
            log.warning(f"Public playlist could not read the state file: {e}")
    try:
        return build_playlist(entries, state, home_dir)
    except Exception as e:
        log.warning(f"Public playlist could not be built: {e}")
        return PublicPlaylist()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def _cache_headers(response: Response) -> None:
    response.headers["Cache-Control"] = f"public, max-age={int(CACHE_SECONDS)}"


@router.get("/now-playing", response_model=PublicNowPlaying, dependencies=[Depends(rate_limit)])
async def now_playing(response: Response, read_state=Depends(get_state_reader)):
    """Whether the show is on and the current song title. No login needed."""
    _cache_headers(response)
    return cached("now-playing", lambda: now_playing_snapshot(read_state))


@router.get("/schedule", response_model=PublicSchedule, dependencies=[Depends(rate_limit)])
async def schedule(response: Response, scheduler=Depends(get_optional_scheduler),
                   note: Optional[str] = Depends(get_schedule_note),
                   show_times: Optional[PublicShowTime] = Depends(get_show_times)):
    """
    Show times: enabled API schedules, tonight's window, and the free-text
    `[api] public_schedule` note. When the show runs from cron (no API
    schedules), `[api] public_show_start/stop/days` give the window instead.
    """
    _cache_headers(response)
    return cached("schedule", lambda: schedule_snapshot(scheduler, note, show_times))


@router.get("/playlist", response_model=PublicPlaylist, dependencies=[Depends(rate_limit)])
async def playlist(response: Response, read_entries=Depends(get_playlist_reader),
                   read_state=Depends(get_state_reader),
                   home_dir: str = Depends(get_home_dir)):
    """Song titles in playlist order (no file paths) and the index playing now."""
    _cache_headers(response)
    return cached("playlist", lambda: playlist_snapshot(read_entries, read_state, home_dir))
