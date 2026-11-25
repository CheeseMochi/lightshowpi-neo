"""Show analytics computed from event-log rows (see py/event_log.py).

Pure functions over the dicts ``event_log.fetch_events()`` returns, so the
maths is testable without a database.

Definitions:
- **play**: a ``song_start`` event (or a ``song_end`` whose start fell
  outside the time window, so a play is never counted as less than its end).
- **completion / skip / stop**: ``song_end`` with reason completed / skipped /
  stopped. A song killed without an end event (power cut, SIGKILL) is a play
  with no outcome, so the rates need not sum to 1.
- **listen %**: per ended song, elapsed / duration capped at 100%; a
  completed song counts as 100% (decoded length can differ slightly from the
  tag's duration). Songs with no known duration are left out of the average.
"""

from datetime import datetime
from typing import Dict, Iterable, List, Optional

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
SONG_TYPES = ("song_start", "song_end")
UNKNOWN_SONG = "(unknown)"


def _ratio(num: float, den: float) -> Optional[float]:
    return round(num / den, 4) if den else None


def listen_fraction(event: Dict) -> Optional[float]:
    """Fraction of the song heard for one ``song_end`` event (None if unknown)."""
    details = event.get("details") or {}
    if details.get("reason") == "completed":
        return 1.0
    try:
        elapsed = float(details.get("elapsed_s"))
        duration = float(details.get("duration_s"))
    except (TypeError, ValueError):
        return None
    if duration <= 0:
        return None
    return max(0.0, min(1.0, elapsed / duration))


class _Tally:
    __slots__ = ("starts", "completions", "skips", "stops", "ends",
                 "listen_sum", "listen_n", "listened_s", "last_ts")

    def __init__(self):
        self.starts = self.completions = self.skips = self.stops = self.ends = 0
        self.listen_sum = 0.0
        self.listen_n = 0
        self.listened_s = 0.0
        self.last_ts = None

    def add(self, event: Dict) -> None:
        ts = event.get("ts")
        if ts is not None and (self.last_ts is None or ts > self.last_ts):
            self.last_ts = ts
        if event["type"] == "song_start":
            self.starts += 1
            return
        self.ends += 1
        details = event.get("details") or {}
        reason = details.get("reason")
        if reason == "completed":
            self.completions += 1
        elif reason == "skipped":
            self.skips += 1
        elif reason == "stopped":
            self.stops += 1
        frac = listen_fraction(event)
        if frac is not None:
            self.listen_sum += frac
            self.listen_n += 1
        try:
            self.listened_s += max(0.0, float(details.get("elapsed_s") or 0))
        except (TypeError, ValueError):
            pass

    @property
    def plays(self) -> int:
        return max(self.starts, self.ends)

    def rates(self) -> Dict:
        return {
            "plays": self.plays,
            "completions": self.completions,
            "skips": self.skips,
            "stops": self.stops,
            "completion_rate": _ratio(self.completions, self.plays),
            "skip_rate": _ratio(self.skips, self.plays),
            "avg_listen_pct": (round(100.0 * self.listen_sum / self.listen_n, 1)
                               if self.listen_n else None),
        }


def song_stats(events: Iterable[Dict], by_variant: bool = False) -> List[Dict]:
    """Per-song plays, completions, skips, rates and average listen %.

    Args:
        events: event dicts (other types are ignored)
        by_variant: one row per (song, variant) instead of per song

    Returns:
        Rows sorted by plays (desc), then song title.
    """
    tallies: Dict[tuple, _Tally] = {}
    for event in events:
        if event.get("type") not in SONG_TYPES:
            continue
        song = event.get("song") or UNKNOWN_SONG
        variant = (event.get("variant") or None) if by_variant else None
        tallies.setdefault((song, variant), _Tally()).add(event)

    rows = []
    for (song, variant), tally in tallies.items():
        row = {"song": song}
        if by_variant:
            row["variant"] = variant
        row.update(tally.rates())
        row["last_played"] = tally.last_ts
        rows.append(row)
    rows.sort(key=lambda r: (-r["plays"], r["song"].lower(), r.get("variant") or ""))
    return rows


def _local(ts: float, tz=None) -> datetime:
    return datetime.fromtimestamp(ts, tz) if tz is not None else datetime.fromtimestamp(ts)


def hour_stats(events: Iterable[Dict], tz=None) -> Dict:
    """Plays and skips by hour of day and by day of week (show's local time).

    Returns:
        ``by_hour`` (24 rows), ``by_weekday`` (7 rows, Monday first) and
        ``heatmap`` (7 x 24 play counts, [weekday][hour]).
    """
    hours = [{"hour": h, "plays": 0, "skips": 0} for h in range(24)]
    days = [{"day": d, "name": WEEKDAYS[d], "plays": 0, "skips": 0} for d in range(7)]
    heatmap = [[0] * 24 for _ in range(7)]
    for event in events:
        etype = event.get("type")
        ts = event.get("ts")
        if ts is None or etype not in SONG_TYPES:
            continue
        is_play = etype == "song_start"
        is_skip = etype == "song_end" and (event.get("details") or {}).get("reason") == "skipped"
        if not (is_play or is_skip):
            continue
        when = _local(ts, tz)
        key = "plays" if is_play else "skips"
        hours[when.hour][key] += 1
        days[when.weekday()][key] += 1
        if is_play:
            heatmap[when.weekday()][when.hour] += 1
    return {"by_hour": hours, "by_weekday": days, "heatmap": heatmap}


def summary(events: Iterable[Dict], tz=None) -> Dict:
    """Headline numbers for the Insights page."""
    events = list(events)
    tally = _Tally()
    nights = set()
    show_starts = button_presses = errors = 0
    first_ts = last_ts = None
    for event in events:
        ts = event.get("ts")
        if ts is not None:
            first_ts = ts if first_ts is None else min(first_ts, ts)
            last_ts = ts if last_ts is None else max(last_ts, ts)
        etype = event.get("type")
        if etype in SONG_TYPES:
            tally.add(event)
            if etype == "song_start" and ts is not None:
                nights.add(_local(ts, tz).date())
        elif etype == "show_start":
            show_starts += 1
        elif etype == "button_press":
            button_presses += 1
        elif etype == "error":
            errors += 1

    songs = song_stats(events)
    most_skipped = [r for r in songs if r["skips"] > 0]
    most_skipped.sort(key=lambda r: (-(r["skip_rate"] or 0), -r["skips"], r["song"].lower()))
    result = tally.rates()
    result.update({
        "listened_hours": round(tally.listened_s / 3600.0, 2),
        "show_starts": show_starts,
        "button_presses": button_presses,
        "errors": errors,
        "nights": len(nights),
        "distinct_songs": len(songs),
        "first_event": first_ts,
        "last_event": last_ts,
        "top_song": songs[0]["song"] if songs else None,
        "most_skipped_song": most_skipped[0]["song"] if most_skipped else None,
    })
    return result
