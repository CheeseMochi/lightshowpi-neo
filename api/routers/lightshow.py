"""
Lightshow Control API Endpoints

Provides REST API endpoints for controlling the lightshow.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, status
from typing import Optional

from api.core.auth import require_admin
from api.core.config import get_api_config, APIConfig
from api.services.lightshow_manager import (
    get_lightshow_manager,
    set_lightshow_manager,
    LightshowManager,
    LightshowState
)
from api.models.schemas import (
    LightshowStart,
    LightshowStop,
    LightshowStatus,
    StopMode
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/lightshow", tags=["Lightshow Control"])


# Dependency for getting lightshow manager
def get_manager() -> LightshowManager:
    """Dependency to get the lightshow manager instance."""
    return get_lightshow_manager()


# Module-level function to set manager (called during startup)
def set_manager(manager: LightshowManager) -> None:
    """Set the global lightshow manager instance."""
    set_lightshow_manager(manager)


@router.post("/start", status_code=status.HTTP_200_OK)
async def start_lightshow(
    request: LightshowStart,
    manager: LightshowManager = Depends(get_manager),
    current_user: dict = Depends(require_admin)
):
    """
    Start the lightshow.

    **Requires:** Admin authentication

    **Parameters:**
    - `resume_schedule`: If true, re-enables schedule after manual start

    **Returns:**
    - Success message and current status

    **Example:**
    ```json
    {
        "resume_schedule": true
    }
    ```
    """
    config = get_api_config()

    # Start the lightshow
    success = manager.start()

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start lightshow: {manager.error_message}"
        )

    # If resume_schedule, re-enable the schedule
    if request.resume_schedule:
        # TODO: Re-enable schedule in scheduler service
        log.info("Schedule resumed")

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
    current_user: dict = Depends(require_admin)
):
    """
    Stop the lightshow.

    **Requires:** Admin authentication

    **Parameters:**
    - `mode`: "pause" (stop until next schedule) or "stop" (disable schedule)

    **Modes:**
    - **pause**: Stops playback but will resume at next scheduled event
    - **stop**: Stops playback and disables schedule until manually started

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
        # Disable schedule completely
        # TODO: Disable schedule in scheduler service
        log.info("Schedule disabled (stop mode)")
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
    manager: LightshowManager = Depends(get_manager)
):
    """
    Get current lightshow status.

    **Does not require authentication** - allows public status checks

    **Returns:**
    - Current state (idle, playing, etc.)
    - Current song (if playing)
    - Current playlist
    - Schedule status
    - Connected clients

    **Example Response:**
    ```json
    {
        "state": "playing",
        "current_song": "Jingle Bells.mp3",
        "playlist": "Christmas 2024",
        "elapsed_time": 45.2,
        "total_time": 180.0,
        "schedule_enabled": true,
        "next_scheduled_event": "22:15",
        "clients": [
            {
                "id": "neighbor-1",
                "name": "Neighbor's House",
                "status": "online"
            }
        ]
    }
    ```
    """
    config = get_api_config()

    status_info = manager.get_status()

    # TODO: Get schedule info from scheduler service
    schedule_enabled = True
    next_event = None

    # TODO: Get connected clients info
    clients = []

    return {
        "state": status_info["state"],
        "current_song": status_info.get("current_song"),
        "current_song_id": None,  # TODO: Look up song ID from database
        "playlist": status_info.get("current_playlist"),
        "elapsed_time": None,  # TODO: Track elapsed time
        "total_time": None,  # TODO: Get song duration
        "schedule_enabled": schedule_enabled,
        "next_scheduled_event": next_event,
        "clients": clients
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
