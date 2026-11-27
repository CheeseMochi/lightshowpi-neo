"""
Song details from audio file tags (title, artist, album, year) for the public page.

Tags are read with mutagen's "easy" interface, which uses the same key names
for MP3 (ID3), M4A/MP4, FLAC and Ogg files. Results are cached by path and
modification time, so polling the public page costs a ``stat()`` per song,
not a file parse. Reading never raises: a missing, unreadable or untagged
file gives empty details.

Only short display text leaves this module; never the file path.
"""

import logging
import os
import re
import threading
from typing import Dict, Optional, Tuple

log = logging.getLogger(__name__)

MAX_FIELD = 120
# Keep the cache bounded even if the playlist keeps changing
MAX_CACHE_ENTRIES = 2000
_YEAR_RE = re.compile(r"(1[89]\d\d|20\d\d)")

_cache: Dict[Tuple[str, int], Dict[str, Optional[str]]] = {}
_cache_lock = threading.Lock()

EMPTY: Dict[str, Optional[str]] = {"title": None, "artist": None, "album": None, "year": None}


def _first(tags, *keys) -> Optional[str]:
    """First non-blank value among ``keys`` in an easy-tags mapping, trimmed and capped."""
    for key in keys:
        try:
            values = tags.get(key)
        except Exception:
            values = None
        if not values:
            continue
        value = values[0] if isinstance(values, (list, tuple)) else values
        text = " ".join(str(value).split())
        if text:
            return text[:MAX_FIELD]
    return None


def year_from(value: Optional[str]) -> Optional[str]:
    """The four-digit year in a tag date ('2004-11-02' -> '2004'), or None."""
    match = _YEAR_RE.search(value or "")
    return match.group(1) if match else None


def _parse(path: str) -> Dict[str, Optional[str]]:
    import mutagen  # imported lazily so the API starts without it

    meta = mutagen.File(path, easy=True)
    if meta is None or not getattr(meta, "tags", None):
        return dict(EMPTY)
    tags = meta.tags
    return {
        "title": _first(tags, "title"),
        "artist": _first(tags, "artist", "albumartist"),
        "album": _first(tags, "album"),
        "year": year_from(_first(tags, "date", "originaldate", "year")),
    }


def read_tags(path: str) -> Dict[str, Optional[str]]:
    """``{title, artist, album, year}`` for the audio file at ``path``; values may be None."""
    if not path:
        return dict(EMPTY)
    try:
        key = (path, os.stat(path).st_mtime_ns)
    except OSError:
        return dict(EMPTY)
    with _cache_lock:
        hit = _cache.get(key)
    if hit is not None:
        return dict(hit)
    try:
        details = _parse(path)
    except Exception as e:
        log.debug(f"Could not read tags from {os.path.basename(path)}: {e}")
        details = dict(EMPTY)
    with _cache_lock:
        if len(_cache) >= MAX_CACHE_ENTRIES:
            _cache.clear()
        _cache[key] = details
    return dict(details)


def clear_cache() -> None:
    """Forget cached tags (tests)."""
    with _cache_lock:
        _cache.clear()
