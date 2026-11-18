"""
Lightshow Control API Endpoints

Provides REST API endpoints for controlling the lightshow.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, status
from typing import Optional, List, Dict

from api.core.auth import get_current_user, require_admin
from api.core.database import Database
from api.services.lightshow_manager import (
    get_lightshow_manager,
    set_lightshow_manager,
    LightshowManager,
)
from api.services.scheduler import SchedulerService
from api.services.test_mode_manager import get_test_mode_manager
from api.routers.schedules import get_optional_scheduler
from api.models.schemas import (
    LightshowStart,
    LightshowStop,
    LightshowStatus,
    ClientState,
    StopMode
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/lightshow", tags=["Lightshow Control"])

# Database instance (set by main.py during startup)
_database: Optional[Database] = None


# Dependency for getting lightshow manager
def get_manager() -> LightshowManager:
    """Dependency to get the lightshow manager instance."""
    return get_lightshow_manager()


# Module-level function to set manager (called during startup)
def set_manager(manager: LightshowManager) -> None:
    """Set the global lightshow manager instance."""
    set_lightshow_manager(manager)


def set_database(database: Optional[Database]) -> None:
    """Set the database instance used for status lookups (called during startup)."""
    global _database
    _database = database


def get_optional_database() -> Optional[Database]:
    """Dependency returning the database, or None if not initialized."""
    return _database


def lookup_song_id(database: Optional[Database], song_path: Optional[str]) -> Optional[int]:
    """Find the songs.id for a file path (None until song upload populates the table)."""
    if database is None or not song_path:
        return None
    try:
        with database.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM songs WHERE file_path = ? LIMIT 1", (song_path,))
            row = cursor.fetchone()
            return row[0] if row else None
    except Exception as e:
        log.warning(f"Could not look up song id: {e}")
        return None


def list_clients(database: Optional[Database]) -> List[Dict]:
    """Return registered clients as ClientStatus dicts."""
    if database is None:
        return []
    try:
        with database.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, name, ip_address, status, last_seen
                FROM clients
                ORDER BY name
            """)
            clients = []
            for row in cursor.fetchall():
                client = dict(row)
                try:
                    client['status'] = ClientState(client.get('status') or 'offline')
                except ValueError:
                    client['status'] = ClientState.ERROR
                clients.append(client)
            return clients
    except Exception as e:
        log.warning(f"Could not list clients: {e}")
        return []


def get_optional_test_mode():
    """Dependency returning the TestModeManager, or None if not initialized."""
    return get_test_mode_manager()


@router.post("/start", status_code=status.HTTP_200_OK)
async def start_lightshow(
    request: LightshowStart,
    manager: LightshowManager = Depends(get_manager),
    scheduler: Optional[SchedulerService] = Depends(get_optional_scheduler),
    test_mode=Depends(get_optional_test_mode),
    current_user: dict = Depends(require_admin)
):
    """
    Start the lightshow.

    **Requires:** Admin authentication

    **Parameters:**
    - `mode`: Optional lightshow mode (playlist, ambient, audio-in, stream-in)
    - `playlist`: Optional playlist path
    - `resume_schedule`: If true, resumes the schedule if it was suspended by a "stop"

    **Returns:**
    - Success message and current status

    **Example:**
    ```json
    {
        "resume_schedule": true
    }
    ```
    """
    # Test mode and the show are exclusive
    if test_mode is not None and test_mode.is_active():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Test mode is active - exit test mode before starting the lightshow"
        )

    # Start the lightshow with optional mode and playlist
    success = manager.start(
        mode=request.mode.value if request.mode else None,
        playlist=request.playlist
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start lightshow: {manager.error_message}"
        )

    # If resume_schedule, clear the global suspend flag
    if request.resume_schedule and scheduler is not None:
        scheduler.resume(by=current_user.get('username'))

    # Log the action
    log.info(f"Lightshow started by user: {current_user['username']}")

    return {
        "message": "Lightshow started successfully",
        "status": manager.get_status()
    }


@router.post("/stop", status_code=status.HTTP_200_OK)
async def stop_lightshow(
    request: LightshowStop,
    manager: LightshowManager = Depends(get_manager),
    scheduler: Optional[SchedulerService] = Depends(get_optional_scheduler),
    current_user: dict = Depends(require_admin)
):
    """
    Stop the lightshow.

    **Requires:** Admin authentication

    **Parameters:**
    - `mode`: "pause" (stop until next schedule) or "stop" (suspend schedule)

    **Modes:**
    - **pause**: Stops playback but will resume at next scheduled event
    - **stop**: Stops playback and suspends all schedules until the show is
      started again with `resume_schedule=true`

    **Example:**
    ```json
    {
        "mode": "pause"
    }
    ```
    """

    # Stop the lightshow
    success = manager.stop(graceful=True)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to stop lightshow: {manager.error_message}"
        )

    # Handle schedule based on mode
    if request.mode == StopMode.STOP:
        if scheduler is not None:
            scheduler.suspend(by=current_user.get('username'))
        else:
            log.warning("Stop mode requested but scheduler is not available")
    else:
        # Pause mode - schedule stays enabled
        log.info("Lightshow paused (will resume at next schedule)")

    # Log the action
    log.info(f"Lightshow stopped ({request.mode.value}) by user: {current_user['username']}")

    return {
        "message": f"Lightshow stopped ({request.mode.value})",
        "status": manager.get_status()
    }


@router.post("/skip", status_code=status.HTTP_200_OK)
async def skip_song(
    manager: LightshowManager = Depends(get_manager),
    current_user: dict = Depends(require_admin)
):
    """
    Skip to the next song.

    **Requires:** Admin authentication

    **Returns:**
    - Success message

    **Note:**
    Uses the state file mechanism to signal skip, so there may be
    a delay of a few seconds before the skip takes effect.
    """

    if not manager.is_running():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lightshow is not running"
        )

    success = manager.skip()

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send skip signal"
        )

    # Log the action
    log.info(f"Song skipped by user: {current_user['username']}")

    return {
        "message": "Skip signal sent",
        "note": "Skip will take effect within a few seconds"
    }


@router.get("/status", response_model=LightshowStatus)
async def get_status(
    manager: LightshowManager = Depends(get_manager),
    scheduler: Optional[SchedulerService] = Depends(get_optional_scheduler),
    database: Optional[Database] = Depends(get_optional_database),
    current_user: dict = Depends(get_current_user)
):
    """
    Get current lightshow status.

    **Requires authentication.** It lists client IPs and file paths; the
    public, read-only view is `/api/public/now-playing`.

    **Returns:**
    - Current state (idle, starting, playing, ...) and mode
    - Current song title, elapsed and total time (seconds)
    - Current playlist
    - Schedule status (`schedule_enabled` is false while suspended) and the next event
    - Registered clients

    **Example Response:**
    ```json
    {
        "state": "playing",
        "mode": "playlist",
        "current_song": "Jingle Bells by Various",
        "playlist": "/home/pi/lightshowpi/music/.playlist",
        "elapsed_time": 45.2,
        "total_time": 180.0,
        "schedule_enabled": true,
        "next_scheduled_event": "2025-12-20T22:15:00-06:00",
        "next_scheduled_action": "stop",
        "clients": []
    }
    ```
    """
    status_info = manager.get_status()

    schedule_enabled = False
    next_event = None
    if scheduler is not None:
        try:
            schedule_enabled = not scheduler.is_suspended()
            next_event = scheduler.get_next_event()
        except Exception as e:
            log.warning(f"Could not read schedule status: {e}")

    return {
        "state": status_info["state"],
        "mode": status_info.get("mode"),
        "current_song": status_info.get("current_song"),
        "current_song_id": lookup_song_id(database, status_info.get("song_path")),
        "playlist": status_info.get("current_playlist"),
        "elapsed_time": status_info.get("elapsed_time"),
        "total_time": status_info.get("total_time"),
        "schedule_enabled": schedule_enabled,
        "next_scheduled_event": next_event["time"] if next_event else None,
        "next_scheduled_action": next_event["action"] if next_event else None,
        "clients": list_clients(database)
    }


@router.post("/restart", status_code=status.HTTP_200_OK)
async def restart_lightshow(
    manager: LightshowManager = Depends(get_manager),
    current_user: dict = Depends(require_admin)
):
    """
    Restart the lightshow.

    **Requires:** Admin authentication

    Stops and then starts the lightshow with the current playlist.
    Useful for recovering from errors.

    **Returns:**
    - Success message and status
    """

    success = manager.restart()

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to restart lightshow: {manager.error_message}"
        )

    # Log the action
    log.info(f"Lightshow restarted by user: {current_user['username']}")

    return {
        "message": "Lightshow restarted successfully",
        "status": manager.get_status()
    }
