"""
Songs API: the playlist (order, names, enable/disable), per-song settings and
"play now".

Every route needs a login; anything that changes files or the show needs an
admin. See api/services/songs.py for the file formats and locking.
"""

import logging
import os
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from api.core.auth import get_current_user, require_admin
from api.services.lightshow_manager import LightshowManager, get_lightshow_manager
from api.services.songs import SongError, SongLibrary, SETTINGS_KEYS

log = logging.getLogger(__name__)

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
                       gpio_len=int(cm.hardware.gpio_len))


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


@router.post("/{index}/play")
def play_song_now(index: int, library: SongLibrary = Depends(get_library),
                  manager: LightshowManager = Depends(get_manager),
                  current_user: dict = Depends(require_admin)):
    """Interrupt the running show and play this song next (state ``play_now``)."""
    row = _run(library.row, index)
    if not manager.is_running():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="The show isn't running; start it first")
    current = getattr(manager, "current_playlist", None)
    if current and os.path.realpath(os.path.expandvars(current)) != \
            os.path.realpath(library.playlist_path):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="The show is playing a different playlist")
    # The show plays songs[play_now - 1]
    manager.config.update_state('play_now', str(index + 1))
    log.info("%s asked to play song %d (%s) now", current_user.get("username"), index, row[0])
    return {"success": True, "index": index, "name": row[0]}
