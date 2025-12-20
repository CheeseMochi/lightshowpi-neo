"""Button Manager API Router

Provides REST endpoints for physical and virtual button controls.
Allows web UI to trigger button actions and monitor button state.
"""

from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.core.auth import get_current_user
from api.services.button_manager import ButtonManagerService, ButtonAction

router = APIRouter(prefix="/buttons", tags=["buttons"])

# Global button manager service instance (set by main.py)
_button_manager: Optional[ButtonManagerService] = None


def set_button_manager(manager: ButtonManagerService):
    """Set the global button manager service instance."""
    global _button_manager
    _button_manager = manager


def get_button_manager() -> ButtonManagerService:
    """Dependency to get button manager service."""
    if _button_manager is None:
        raise HTTPException(status_code=503, detail="Button manager not initialized")
    return _button_manager


# Response Models
class ButtonStatusResponse(BaseModel):
    """Current button manager status."""
    enabled: bool
    repeat_mode: bool
    audio_on: bool
    last_action: Optional[str] = None
    last_action_time: Optional[datetime] = None


class ButtonHealthResponse(BaseModel):
    """Button manager health check."""
    healthy: bool
    stuck_button: Optional[str] = None
    stuck_duration: Optional[float] = None
    warning: Optional[str] = None


class ButtonActionResponse(BaseModel):
    """Response from button action."""
    success: bool
    action: str
    message: str


# Endpoints
@router.get("/status", response_model=ButtonStatusResponse)
async def get_button_status(
    manager: ButtonManagerService = Depends(get_button_manager),
    current_user: dict = Depends(get_current_user)
):
    """Get current button manager status.

    Returns:
        Current state including repeat mode, audio status, and last action
    """
    status = manager.get_status()
    return ButtonStatusResponse(**status)


@router.get("/health", response_model=ButtonHealthResponse)
async def get_button_health(
    manager: ButtonManagerService = Depends(get_button_manager),
    current_user: dict = Depends(get_current_user)
):
    """Check button manager health and detect stuck buttons.

    Returns:
        Health status with stuck button alerts
    """
    health = manager.check_health()
    return ButtonHealthResponse(**health)


@router.post("/skip", response_model=ButtonActionResponse)
async def skip_song(
    manager: ButtonManagerService = Depends(get_button_manager),
    current_user: dict = Depends(get_current_user)
):
    """Trigger skip button action (play next song).

    Virtual button press - queues the next song to play.
    """
    try:
        manager.handle_button_action(ButtonAction.SKIP)
        return ButtonActionResponse(
            success=True,
            action="skip",
            message="Skip triggered - next song queued"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Skip action failed: {str(e)}")


@router.post("/repeat/toggle", response_model=ButtonActionResponse)
async def toggle_repeat(
    manager: ButtonManagerService = Depends(get_button_manager),
    current_user: dict = Depends(get_current_user)
):
    """Toggle repeat mode on/off.

    Virtual button press - enables or disables continuous play mode.
    """
    try:
        new_state = manager.handle_button_action(ButtonAction.REPEAT_TOGGLE)
        return ButtonActionResponse(
            success=True,
            action="repeat_toggle",
            message=f"Repeat mode {'enabled' if new_state else 'disabled'}"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Repeat toggle failed: {str(e)}")


@router.post("/audio/toggle", response_model=ButtonActionResponse)
async def toggle_audio(
    manager: ButtonManagerService = Depends(get_button_manager),
    current_user: dict = Depends(get_current_user)
):
    """Toggle audio relay on/off.

    Virtual button press - turns audio output relay on or off with cooldown.
    """
    try:
        new_state = manager.handle_button_action(ButtonAction.AUDIO_TOGGLE)
        return ButtonActionResponse(
            success=True,
            action="audio_toggle",
            message=f"Audio {'on' if new_state else 'off'}"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Audio toggle failed: {str(e)}")
