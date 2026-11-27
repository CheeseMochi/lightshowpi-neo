#!/usr/bin/env python
"""Event log: a small SQLite record of what the show did and who did it.

Show starts and stops, every song start and end (completed, skipped or
stopped), button presses, admin API actions and test mode are written to
``data/events.db`` (``[events] db_path``). The API reads it for the Activity
and Insights pages.

The show must never break or slow down because of this log, so:

- ``record()`` never raises. Every failure is caught and logged as a warning,
  at most once a minute.
- The SQLite busy timeout is short (``BUSY_TIMEOUT_S``), so a locked database
  costs the show at most that long, then the event is dropped.
- The database is in WAL mode, so readers (the API) don't block writers.
- ``[events] enabled = false`` turns every call into a no-op.

The file is separate from the API database (data/lightshowpi.db) so the two
never contend for the same lock. It lives in ``py/`` so the show, the button
manager and the API can all write to it without ``py/`` depending on ``api/``.

CLI (used by bin/start_music_and_lights and bin/stop_music_and_lights)::

    python py/event_log.py show_start --source cron
    python py/event_log.py show_stop --source cron --detail reason=manual

The CLI always exits 0.
"""

import argparse
import configparser
import json
import logging
import os
import sqlite3
import sys
import threading
import time

log = logging.getLogger(__name__)

# How long a write waits for a locked database before giving up (seconds)
BUSY_TIMEOUT_S = 0.5
# Minimum seconds between two "event log write failed" warnings
WARN_INTERVAL_S = 60.0

DEFAULT_DB_RELPATH = os.path.join("data", "events.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    type TEXT NOT NULL,
    source TEXT,
    song TEXT,
    variant TEXT,
    details TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_type_ts ON events(type, ts);
"""

# Event types written by this repo (documentation; record() accepts any)
EVENT_TYPES = (
    "show_start", "show_stop", "song_start", "song_end", "skip", "error",
    "button_press", "api_action", "config_change", "test_mode",
)

_FALSE = ("0", "false", "no", "off")

_lock = threading.Lock()
_settings = {"configured": False, "enabled": True, "db_path": None}
_ready_paths = set()
_last_warning = {"t": 0.0}


def _warn(message, *args):
    """Log a warning at most once per WARN_INTERVAL_S (never raises)."""
    try:
        now = time.monotonic()
        if now - _last_warning["t"] >= WARN_INTERVAL_S:
            _last_warning["t"] = now
            log.warning("event log: " + message, *args)
    except Exception:
        pass


def default_db_path(home=None):
    """``$SYNCHRONIZED_LIGHTS_HOME/data/events.db`` (or the cwd's data/ when unset)."""
    home = home or os.getenv("SYNCHRONIZED_LIGHTS_HOME") or os.getcwd()
    return os.path.join(home, DEFAULT_DB_RELPATH)


def _read_config_files(home=None):
    """Read defaults.cfg + overrides.cfg without the full Configuration class."""
    home = home or os.getenv("SYNCHRONIZED_LIGHTS_HOME")
    parser = configparser.RawConfigParser(allow_no_value=True, strict=False)
    if home:
        config_dir = os.path.join(home, "config")
        parser.read([os.path.join(config_dir, "defaults.cfg"),
                     os.path.join(config_dir, "overrides.cfg")])
    return parser


def configure(config=None, enabled=None, db_path=None, home=None):
    """Set up the event log. Never raises.

    Args:
        config: ConfigParser with an optional ``[events]`` section (the show's
            ``cm.config`` or the API's). None reads defaults.cfg + overrides.cfg.
        enabled: Override ``[events] enabled``.
        db_path: Override ``[events] db_path``.
        home: SYNCHRONIZED_LIGHTS_HOME override (default: the environment).
    """
    try:
        home = home or os.getenv("SYNCHRONIZED_LIGHTS_HOME")
        cfg_enabled = True
        cfg_path = ""
        try:
            if config is None:
                config = _read_config_files(home)
            if config.has_section("events"):
                raw = config.get("events", "enabled", fallback="true")
                cfg_enabled = str(raw if raw is not None else "true").strip().lower() not in _FALSE
                cfg_path = (config.get("events", "db_path", fallback="") or "").strip()
        except Exception as e:
            _warn("could not read [events] config (%s); using defaults", e)

        if cfg_path:
            cfg_path = cfg_path.replace("$SYNCHRONIZED_LIGHTS_HOME", home or "")
            cfg_path = os.path.expanduser(cfg_path)

        with _lock:
            _settings["enabled"] = cfg_enabled if enabled is None else bool(enabled)
            _settings["db_path"] = db_path or cfg_path or default_db_path(home)
            _settings["configured"] = True
    except Exception as e:
        _warn("configure failed: %s", e)


def _ensure_configured():
    if not _settings["configured"]:
        configure()


def is_enabled():
    """True when events are being recorded."""
    try:
        _ensure_configured()
        return bool(_settings["enabled"])
    except Exception:
        return False


def get_db_path():
    """Path of the events database (whether or not it exists yet)."""
    _ensure_configured()
    return _settings["db_path"]


def _match_owner(path, reference):
    """When running as root, give ``path`` the owner of ``reference``.

    The show and button manager run as root (sudo/cron) and the API as the
    normal user. SQLite gives the -wal/-shm files the owner and mode of the
    database file, so fixing the database file once is enough for both
    sides to keep writing.
    """
    try:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            st = os.stat(reference)
            os.chown(path, st.st_uid, st.st_gid)
    except Exception:
        pass


def _connect(db_path):
    """Open the database, creating file and schema on first use."""
    is_new = not os.path.exists(db_path)
    if is_new:
        parent = os.path.dirname(os.path.abspath(db_path))
        if not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
            _match_owner(parent, os.path.dirname(parent))

    conn = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_S, isolation_level=None)
    try:
        conn.execute("PRAGMA busy_timeout = %d" % int(BUSY_TIMEOUT_S * 1000))
        if is_new or db_path not in _ready_paths:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)
            if is_new:
                try:
                    os.chmod(db_path, 0o664)
                except Exception:
                    pass
                _match_owner(db_path, os.path.dirname(os.path.abspath(db_path)))
            _ready_paths.add(db_path)
        conn.execute("PRAGMA synchronous = NORMAL")
    except Exception:
        conn.close()
        raise
    return conn


def _clean(value):
    """Make a detail value JSON-safe."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    return str(value)


def record(event_type, source=None, song=None, variant=None, ts=None, **details):
    """Write one event. Never raises; returns True if it was stored.

    Args:
        event_type: e.g. ``song_start``, ``song_end``, ``show_start`` (see EVENT_TYPES)
        source: who caused it: ``show``, ``cron``, ``api``, ``schedule``, ``button``
        song: song title, for song events
        variant: light sequence variant, for song events ('' or None if none)
        ts: epoch seconds (default: now)
        **details: anything else, stored as JSON (user, reason, elapsed_s, ...)
    """
    try:
        _ensure_configured()
        if not _settings["enabled"]:
            return False
        db_path = _settings["db_path"]
        row = (
            float(ts if ts is not None else time.time()),
            str(event_type),
            None if source is None else str(source),
            None if song is None else str(song),
            variant or None,
            json.dumps(_clean(details), separators=(",", ":")) if details else None,
        )
        conn = _connect(db_path)
        try:
            conn.execute(
                "INSERT INTO events (ts, type, source, song, variant, details) "
                "VALUES (?, ?, ?, ?, ?, ?)", row)
        finally:
            conn.close()
        return True
    except Exception as e:
        _warn("could not record %r: %s", event_type, e)
        return False


def _row_to_dict(row):
    ts, etype, source, song, variant, details, rowid = row
    try:
        parsed = json.loads(details) if details else {}
        if not isinstance(parsed, dict):
            parsed = {"value": parsed}
    except (TypeError, ValueError):
        parsed = {}
    return {"id": rowid, "ts": ts, "type": etype, "source": source,
            "song": song, "variant": variant, "details": parsed}


def fetch_events(types=None, since=None, until=None, limit=None, offset=0,
                 newest_first=True, db_path=None, song=None):
    """Read events. Returns ``(events, total)``; ``([], 0)`` if there is no database.

    Args:
        types: iterable of event types to include (None = all)
        since/until: epoch seconds bounds (inclusive since, exclusive until)
        limit/offset: paging (limit None = all)
        newest_first: sort order by ts
        db_path: override the configured path
        song: only events for this exact song title (None = all songs)

    Raises:
        sqlite3.Error on a corrupt or unreadable database (callers in the API
        turn that into an HTTP error; the show never reads).
    """
    path = db_path or get_db_path()
    if not path or not os.path.exists(path):
        return [], 0

    where, params = [], []
    types = [t for t in (types or []) if t]
    if types:
        where.append("type IN (%s)" % ",".join("?" * len(types)))
        params.extend(types)
    if since is not None:
        where.append("ts >= ?")
        params.append(float(since))
    if until is not None:
        where.append("ts < ?")
        params.append(float(until))
    if song is not None:
        where.append("song = ?")
        params.append(str(song))
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_S)
    try:
        conn.execute("PRAGMA busy_timeout = %d" % int(BUSY_TIMEOUT_S * 1000))
        total = conn.execute("SELECT COUNT(*) FROM events" + clause, params).fetchone()[0]
        sql = ("SELECT ts, type, source, song, variant, details, id FROM events" + clause
               + " ORDER BY ts %s, id %s" % (("DESC",) * 2 if newest_first else ("ASC",) * 2))
        page_params = list(params)
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            page_params.extend([int(limit), int(offset or 0)])
        rows = conn.execute(sql, page_params).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows], total


class SongTracker:
    """Records the ``song_start`` and one ``song_end`` for the show process.

    ``end()`` records at most once per ``start()``, so the normal end of
    play_song() and the atexit handler (stopped mid-song) can both call it.
    Never raises.
    """

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._song = None

    @property
    def active(self):
        return self._song is not None

    def start(self, title, path=None, duration=None, mode=None, variant=None, source="show"):
        """Record ``song_start`` and remember it for ``end()``."""
        try:
            duration_s = _to_float(duration)
            self._song = {"title": title, "path": path, "duration_s": duration_s,
                          "variant": variant or None, "source": source,
                          "t0": self._clock()}
            record("song_start", source=source, song=title, variant=variant,
                   path=path, duration_s=duration_s, mode=mode)
        except Exception as e:
            _warn("song start failed: %s", e)

    def start_from_state(self, get_state, mode=None):
        """``start()`` with the song keys the show wrote to state.cfg.

        Args:
            get_state: ``cm.get_state`` (name, default) -> str
            mode: the show mode (playlist, ...)
        """
        try:
            path = get_state("song_path", "") or None
            title = get_state("song_title", "") or (os.path.basename(path) if path else None)
            self.start(title, path=path, duration=get_state("song_duration", ""),
                       mode=mode, variant=get_state("song_sequence", "") or None)
        except Exception as e:
            _warn("song start failed: %s", e)

    @staticmethod
    def end_reason(play_now):
        """``skipped`` when the play loop ended on a play_now request, else ``completed``."""
        return "skipped" if play_now else "completed"

    def end(self, reason):
        """Record ``song_end`` (reason completed|skipped|stopped) once."""
        try:
            song, self._song = self._song, None
            if song is None:
                return False
            elapsed = max(0.0, self._clock() - song["t0"])
            return record("song_end", source=song["source"], song=song["title"],
                          variant=song["variant"], reason=reason,
                          elapsed_s=round(elapsed, 1), duration_s=song["duration_s"],
                          path=song["path"])
        except Exception as e:
            _warn("song end failed: %s", e)
            return False


def _to_float(value):
    try:
        if value is None or str(value).strip() == "":
            return None
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def main(argv=None):
    """CLI entry point. Always returns 0 so shell scripts never fail on it."""
    try:
        parser = argparse.ArgumentParser(description="Record a LightShowPi event")
        parser.add_argument("type", help="event type, e.g. show_start or show_stop")
        parser.add_argument("--source", default="cli", help="who caused it (cron, cli, ...)")
        parser.add_argument("--song", default=None)
        parser.add_argument("--detail", action="append", default=[], metavar="KEY=VALUE",
                            help="extra detail (repeatable)")
        args = parser.parse_args(argv)
        details = {}
        for item in args.detail:
            key, _, value = item.partition("=")
            if key:
                details[key.strip()] = value
        user = os.getenv("SUDO_USER") or os.getenv("USER")
        if user and "user" not in details:
            details["user"] = user
        record(args.type, source=args.source, song=args.song, **details)
    except SystemExit:
        pass
    except Exception as e:
        _warn("CLI failed: %s", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
