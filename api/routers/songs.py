"""
Songs API: the playlist (order, names, enable/disable), per-song settings,
variant sequence CSVs (list, upload, delete) and "play now" (optionally
auditioning one variant for that play).

Every route needs a login; anything that changes files or the show needs an
admin. See api/services/songs.py for the file formats and locking.
"""

import logging
import os
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile as StarletteUploadFile

import event_log  # py/ is on sys.path (api/core/config.py, pytest pythonpath)
import sequence_select
from api.core.auth import get_current_user, require_admin
from api.services.lightshow_manager import LightshowManager, get_lightshow_manager
from api.services.songs import (MAX_VARIANT_BYTES, SETTINGS_KEYS, SongError, SongLibrary,
                                expected_channels)

log = logging.getLogger(__name__)

# Multipart boundaries and headers on top of the file itself
_MULTIPART_OVERHEAD = 64 * 1024

router = APIRouter(prefix="/api/songs", tags=["Songs"])

_config = None


def set_config(api_config) -> None:
    """Remember the APIConfig (called from main.py at startup)."""
    global _config
    _config = api_config


def get_library(_user: dict = Depends(get_current_user)) -> SongLibrary:
    """Dependency: the song library for the configured playlist.

    Depends on the login so an anonymous request gets 401 before anything else.
    """
    if _config is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Configuration not loaded")
    cm = _config.cm
    return SongLibrary(playlist_path=cm.lightshow.playlist_path,
                       home_dir=cm.home_dir or '',
                       sequences_dir=cm.lightshow.sequences_dir,
                       gpio_len=expected_channels(cm),
                       allow_audition=bool(getattr(cm.lightshow, 'allow_sequence_audition', True)))


def get_manager() -> LightshowManager:
    """Dependency: the lightshow manager (for play now)."""
    return get_lightshow_manager()


class SongOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order: List[int] = Field(..., description="Current song indices in their new order")


class SongUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = None
    enabled: Optional[bool] = None


class SongSettings(BaseModel):
    """Per-song [custom_lightshow] values; omit a key to keep it, '' to remove it."""
    model_config = ConfigDict(extra="forbid")
    sequence: Optional[str] = None
    always_on_channels: Optional[str] = None
    always_off_channels: Optional[str] = None
    invert_channels: Optional[str] = None
    network_always_on: Optional[str] = None
    network_always_off: Optional[str] = None
    network_invert: Optional[str] = None
    attenuate_pct: Optional[str] = None
    preshow_configuration: Optional[str] = None
    preshow_script: Optional[str] = None
    postshow_configuration: Optional[str] = None
    postshow_script: Optional[str] = None


assert set(SongSettings.model_fields) == set(SETTINGS_KEYS)


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except SongError as error:
        raise HTTPException(status_code=error.status, detail=str(error))


@router.get("")
def list_songs(library: SongLibrary = Depends(get_library),
               current_user: dict = Depends(get_current_user)):
    """The playlist in show order, plus disabled songs."""
    return _run(library.list)


@router.put("/order")
def reorder_songs(body: SongOrder, library: SongLibrary = Depends(get_library),
                  current_user: dict = Depends(require_admin)):
    """Reorder the playlist. ``order`` lists every current index once."""
    _run(library.reorder, body.order)
    return _run(library.list)


@router.patch("/{index}")
def update_song(index: int, body: SongUpdate, library: SongLibrary = Depends(get_library),
                current_user: dict = Depends(require_admin)):
    """Rename a playlist song, or disable it (``enabled: false``)."""
    _run(library.update, index, True, name=body.name, enabled=body.enabled)
    return _run(library.list)


@router.patch("/disabled/{index}")
def update_disabled_song(index: int, body: SongUpdate,
                         library: SongLibrary = Depends(get_library),
                         current_user: dict = Depends(require_admin)):
    """Rename a disabled song, or re-enable it (``enabled: true``, appended to the playlist)."""
    _run(library.update, index, False, name=body.name, enabled=body.enabled)
    return _run(library.list)


@router.get("/{index}/settings")
def get_song_settings(index: int, library: SongLibrary = Depends(get_library),
                      current_user: dict = Depends(get_current_user)):
    """The song's ``[custom_lightshow]`` settings and available sequences."""
    return _run(library.get_settings, index)


@router.put("/{index}/settings")
def put_song_settings(index: int, body: SongSettings,
                      library: SongLibrary = Depends(get_library),
                      current_user: dict = Depends(require_admin)):
    """Change settings; keys left out are kept, '' removes a key.

    Applies from the song's next play.
    """
    changes = body.model_dump(exclude_unset=True)
    result = _run(library.put_settings, index, changes)
    log.info("%s changed settings of song %d: %s",
             current_user.get("username"), index, sorted(changes))
    return result


def _record(event_type: str, user: dict, song: str, variant: str, **details) -> None:
    """Write a sequence_upload / sequence_delete event (never raises)."""
    try:
        event_log.record(event_type, source="api", song=song, variant=variant,
                         user=(user or {}).get("username"), **details)
    except Exception:  # the event log must never break a request
        log.debug("%s event not recorded", event_type, exc_info=True)


@router.get("/{index}/variants")
def list_song_variants(index: int, library: SongLibrary = Depends(get_library),
                       current_user: dict = Depends(get_current_user)):
    """The song's variant CSVs (name, size, rows, columns, mtime, problem) and ``gpio_len``."""
    return _run(library.list_variant_info, index)


_UPLOAD_BODY = {
    "requestBody": {
        "required": True,
        "content": {"multipart/form-data": {"schema": {
            "type": "object", "required": ["file"],
            "properties": {"file": {"type": "string", "format": "binary"}},
        }}},
    }
}


@router.put("/{index}/variants/{name}", openapi_extra=_UPLOAD_BODY)
async def upload_song_variant(index: int, name: str, request: Request, replace: bool = False,
                              library: SongLibrary = Depends(get_library),
                              current_user: dict = Depends(require_admin)):
    """Upload a variant CSV (multipart field ``file``, at most 5 MB).

    Every row must have ``gpio_len`` numeric columns. An existing variant
    is a 409 unless ``?replace=true``. Returns the variant's info.
    """
    _run(library.check_variant_name, name)
    # Refuse a big body before the multipart parser spools it
    length = request.headers.get("content-length")
    if length is None or not length.isdigit():
        raise HTTPException(status_code=status.HTTP_411_LENGTH_REQUIRED,
                            detail="Content-Length required")
    if int(length) > MAX_VARIANT_BYTES + _MULTIPART_OVERHEAD:
        raise HTTPException(status_code=422,
                            detail="File is larger than %d MB" % (MAX_VARIANT_BYTES // (1024 * 1024)))
    form = await request.form(max_files=1, max_fields=1)
    try:
        upload = form.get("file")
        if not isinstance(upload, StarletteUploadFile):
            raise HTTPException(status_code=422,
                                detail="Send the CSV as multipart form field 'file'")
        info = await run_in_threadpool(_run, library.save_variant, index, name,
                                       upload.file, replace)
    finally:
        await form.close()
    song = _run(library.row, index)[0]
    _record("sequence_upload", current_user, song, name, replaced=info["replaced"],
            rows=info["rows"], size=info["size"])
    log.info("%s uploaded sequence variant %s for song %d (%s)",
             current_user.get("username"), name, index, song)
    return info


@router.delete("/{index}/variants/{name}")
def delete_song_variant(index: int, name: str, library: SongLibrary = Depends(get_library),
                        current_user: dict = Depends(require_admin)):
    """Delete a variant CSV; 409 if the song's saved ``sequence`` setting uses it."""
    song = _run(library.row, index)[0]
    _run(library.delete_variant, index, name)
    _record("sequence_delete", current_user, song, name)
    log.info("%s deleted sequence variant %s of song %d (%s)",
             current_user.get("username"), name, index, song)
    return {"success": True, "index": index, "name": name}


class PlayRequest(BaseModel):
    """Optional body of play now: audition ``variant`` for this play only."""
    model_config = ConfigDict(extra="forbid")
    variant: Optional[str] = None


@router.post("/{index}/play")
def play_song_now(index: int, body: Optional[PlayRequest] = None,
                  library: SongLibrary = Depends(get_library),
                  manager: LightshowManager = Depends(get_manager),
                  current_user: dict = Depends(require_admin)):
    """Interrupt the running show and play this song next (state ``play_now``).

    With ``{"variant": name}`` that variant CSV is used for this one play
    (state ``sequence_once = <song stem>|<variant>``, written before
    ``play_now``); the saved ``sequence`` setting is unchanged.
    """
    row = _run(library.row, index)
    variant = (body.variant or '').strip() if body else ''
    once = _run(library.audition_value, index, variant) if variant else None
    if not manager.is_running():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="The show isn't running; start it first")
    # Only a playlist show reads play_now; in ambient/audio-in it would linger
    current = getattr(manager, "current_playlist", None)
    if not current:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="The show isn't playing a playlist")
    if os.path.realpath(os.path.expandvars(current)) != \
            os.path.realpath(library.playlist_path):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="The show is playing a different playlist")
    # sequence_once first: once the show sees play_now it also sees the variant
    if once:
        manager.config.update_state(sequence_select.ONCE_KEY, once)
    # The show plays songs[play_now - 1]
    manager.config.update_state('play_now', str(index + 1))
    log.info("%s asked to play song %d (%s) now%s", current_user.get("username"), index, row[0],
             " with variant %s" % variant if once else "")
    return {"success": True, "index": index, "name": row[0], "variant": variant or None}
