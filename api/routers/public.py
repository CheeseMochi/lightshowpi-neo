"""
Public, read-only API for the show page (no login).

These endpoints are reachable from the internet through the tunnel's public
hostname, so they return only what a viewer on the street should see: whether
the show is on, the song title and tonight's times. No IPs, file paths, client
details or controls. Responses use explicit models (extra keys can't leak),
are cached for a few seconds and are rate-limited per client IP.

The song comes from config/state.cfg, which synchronized_lights.py writes
however the show was started (cron, CLI, buttons or the API).
"""

import logging
import os
import threading
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict

from api.core.rate_limit import RateLimiter, client_ip
from api.routers.schedules import get_optional_scheduler

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


# ---------------------------------------------------------------------------
# Wiring (set from main.py, overridable in tests)
# ---------------------------------------------------------------------------

_api_config = None


def set_config(api_config) -> None:
    """Give the router the APIConfig (state file access and [api] settings)."""
    global _api_config
    _api_config = api_config
    _cache.clear()


def get_state_reader() -> Optional[Callable[[], Dict[str, str]]]:
    """Dependency: a function returning the current show state, or None."""
    if _api_config is None:
        return None

    def read() -> Dict[str, str]:
        _api_config.reload_state()
        return {key: _api_config.get_state(key, "")
                for key in ("now_playing", "song_title", "song_duration", "song_started_at")}
    return read


def get_schedule_note() -> Optional[str]:
    """Dependency: [api] public_schedule, free text shown on the public page."""
    if _api_config is None:
        return None
    note = _api_config._get_str("api", "public_schedule", "").strip()
    return note or None


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


def build_schedule(schedules: List[Dict], note: Optional[str],
                   today: Optional[datetime] = None) -> PublicSchedule:
    """Enabled API schedules plus the configured note, with tonight's window picked out."""
    today = today or datetime.now()
    today_db = (today.weekday() + 1) % 7  # Python Monday=0 -> DB Sunday=0
    shows, tonight = [], None
    for schedule in schedules:
        if not schedule.get("enabled", True):
            continue
        try:
            show = _show_time(schedule)
        except (KeyError, TypeError, ValueError) as e:
            log.warning(f"Skipping malformed schedule in public view: {e}")
            continue
        shows.append(show)
        if tonight is None and DAY_NAMES[today_db] in show.days:
            tonight = show
    return PublicSchedule(note=note, tonight=tonight, shows=shows)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def _cache_headers(response: Response) -> None:
    response.headers["Cache-Control"] = f"public, max-age={int(CACHE_SECONDS)}"


@router.get("/now-playing", response_model=PublicNowPlaying, dependencies=[Depends(rate_limit)])
async def now_playing(response: Response, read_state=Depends(get_state_reader)):
    """Whether the show is on and the current song title. No login needed."""
    def compute():
        if read_state is None:
            return PublicNowPlaying(state="off")
        try:
            return build_now_playing(read_state())
        except Exception as e:
            log.warning(f"Public now-playing could not read the state file: {e}")
            return PublicNowPlaying(state="off")

    _cache_headers(response)
    return cached("now-playing", compute)


@router.get("/schedule", response_model=PublicSchedule, dependencies=[Depends(rate_limit)])
async def schedule(response: Response, scheduler=Depends(get_optional_scheduler),
                   note: Optional[str] = Depends(get_schedule_note)):
    """
    Show times: enabled API schedules, tonight's window, and the free-text
    `[api] public_schedule` note (use it when the show runs from cron).
    """
    def compute():
        schedules: List[Dict] = []
        if scheduler is not None:
            try:
                schedules = scheduler.get_schedules(enabled_only=True)
            except Exception as e:
                log.warning(f"Public schedule could not read schedules: {e}")
        return build_schedule(schedules, note)

    _cache_headers(response)
    return cached("schedule", compute)
