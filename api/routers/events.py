"""Event log and show analytics endpoints.

``GET /api/events`` pages through the event log (data/events.db, written by
the show, the button manager, cron scripts and the API). ``/api/analytics/*``
summarises song plays, skips and listening. All need a login.
"""

import logging
import sqlite3
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

import event_log
from api.core.auth import get_current_user
from api.services import event_analytics

log = logging.getLogger(__name__)

router = APIRouter(tags=["Events & Analytics"])

MAX_PAGE = 500
ANALYTICS_TYPES = ["song_start", "song_end"]
SUMMARY_TYPES = ANALYTICS_TYPES + ["show_start", "button_press", "error"]


def _epoch(value: Optional[datetime]) -> Optional[float]:
    """Datetime to epoch seconds; a naive value is the Pi's local time."""
    return value.timestamp() if value is not None else None


def _types(raw: Optional[List[str]]) -> Optional[List[str]]:
    """Accept ?type=a&type=b and ?type=a,b."""
    if not raw:
        return None
    types = [t.strip() for item in raw for t in item.split(",") if t.strip()]
    return types or None


def _fetch(**kwargs):
    try:
        return event_log.fetch_events(**kwargs)
    except sqlite3.Error as e:
        log.warning(f"Could not read the event log: {e}")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Event log is unavailable")


@router.get("/api/events")
async def list_events(
    type: Optional[List[str]] = Query(None, description="Event type(s), repeat or comma separate"),
    since: Optional[datetime] = Query(None, description="From (ISO time or epoch seconds)"),
    until: Optional[datetime] = Query(None, description="Before (ISO time or epoch seconds)"),
    limit: int = Query(100, ge=1, le=MAX_PAGE),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    """
    Event log, newest first.

    **Requires authentication.**

    Each event has `id`, `ts` (epoch seconds), `type`, `source` (show, cron,
    api, schedule, button), `song`, `variant` and `details` (user, reason,
    elapsed_s, ...).
    """
    events, total = _fetch(types=_types(type), since=_epoch(since), until=_epoch(until),
                           limit=limit, offset=offset)
    return {
        "events": events,
        "total": total,
        "limit": limit,
        "offset": offset,
        "enabled": event_log.is_enabled(),
    }


@router.get("/api/analytics/songs")
async def analytics_songs(
    since: Optional[datetime] = Query(None),
    until: Optional[datetime] = Query(None),
    by_variant: bool = Query(False, description="One row per song and light sequence variant"),
    current_user: dict = Depends(get_current_user),
):
    """
    Per song: plays, completions, skips, stops, completion and skip rate (0-1)
    and average listen % (0-100).

    **Requires authentication.**
    """
    events, _ = _fetch(types=ANALYTICS_TYPES, since=_epoch(since), until=_epoch(until),
                       newest_first=False)
    return {"songs": event_analytics.song_stats(events, by_variant=by_variant),
            "enabled": event_log.is_enabled()}


@router.get("/api/analytics/hours")
async def analytics_hours(
    since: Optional[datetime] = Query(None),
    until: Optional[datetime] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Plays and skips by hour of day and by day of week (the Pi's local time),
    plus a weekday x hour heatmap of plays.

    **Requires authentication.**
    """
    events, _ = _fetch(types=ANALYTICS_TYPES, since=_epoch(since), until=_epoch(until),
                       newest_first=False)
    result = event_analytics.hour_stats(events)
    result["enabled"] = event_log.is_enabled()
    return result


@router.get("/api/analytics/summary")
async def analytics_summary(
    since: Optional[datetime] = Query(None),
    until: Optional[datetime] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Headline numbers: plays, completion and skip rate, average listen %,
    hours listened, show nights, show starts, button presses, errors.

    **Requires authentication.**
    """
    events, _ = _fetch(types=SUMMARY_TYPES, since=_epoch(since), until=_epoch(until),
                       newest_first=False)
    result = event_analytics.summary(events)
    result["enabled"] = event_log.is_enabled()
    return result
