"""
Test mode router.

Individual channel control and test patterns for checking wiring.
Test mode is exclusive with the lightshow and exits automatically after
`[api] test_mode_timeout_minutes` without commands.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status

from api.core.auth import require_admin
from api.models.schemas import (
    ChannelControl,
    TestModeAll,
    TestModePatternRequest,
    TestModeStatus,
)
from api.services.test_mode_manager import (
    TestModeCommandError,
    TestModeConflict,
    TestModeError,
    TestModeManager,
    TestModeNotActive,
    get_test_mode_manager,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/test", tags=["Test Mode"])


def get_test_mode() -> TestModeManager:
    """Dependency returning the test mode manager."""
    manager = get_test_mode_manager()
    if manager is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Test mode not initialized"
        )
    return manager


def _run(fn):
    """Call a manager method, mapping test mode errors to HTTP errors."""
    try:
        return fn()
    except TestModeConflict as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except TestModeNotActive as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except TestModeCommandError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except TestModeError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


# Endpoints are sync (def) so FastAPI runs them in a threadpool:
# patterns block until they finish.

@router.get("/status", response_model=TestModeStatus)
def get_status(
    manager: TestModeManager = Depends(get_test_mode),
    current_user: dict = Depends(require_admin)
):
    """Get test mode status (active, channel levels, seconds until auto-exit)."""
    return manager.status()


@router.post("/enter", response_model=TestModeStatus)
def enter_test_mode(
    manager: TestModeManager = Depends(get_test_mode),
    current_user: dict = Depends(require_admin)
):
    """
    Enter test mode. All lights start off.

    Returns 409 if the lightshow is running. Scheduled starts are skipped while
    test mode is active.
    """
    result = _run(lambda: manager.enter(by=current_user.get("username")))
    log.info(f"Test mode entered by user: {current_user.get('username')}")
    return result


@router.post("/exit", response_model=TestModeStatus)
def exit_test_mode(
    manager: TestModeManager = Depends(get_test_mode),
    current_user: dict = Depends(require_admin)
):
    """Exit test mode: all lights off and GPIO released."""
    result = _run(lambda: manager.exit(reason=f"exited by {current_user.get('username')}"))
    log.info(f"Test mode exited by user: {current_user.get('username')}")
    return result


@router.post("/channel", response_model=TestModeStatus)
def set_channel(
    request: ChannelControl,
    manager: TestModeManager = Depends(get_test_mode),
    current_user: dict = Depends(require_admin)
):
    """
    Turn one channel on or off.

    `channel` is 0-based and must be below the configured channel count.
    `brightness` (0.0-1.0) applies to PWM channels; on/off channels go full on.
    """
    if request.client_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Client channel control is not supported yet"
        )

    if not manager.is_active():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Test mode is not active")

    count = manager.channel_count
    if count is not None and request.channel >= count:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Channel {request.channel} out of range (0-{count - 1})"
        )

    _run(lambda: manager.set_channel(request.channel, request.state, request.brightness))
    return manager.status()


@router.post("/all", response_model=TestModeStatus)
def set_all(
    request: TestModeAll,
    manager: TestModeManager = Depends(get_test_mode),
    current_user: dict = Depends(require_admin)
):
    """Turn all channels on or off."""
    _run(lambda: manager.set_all(request.on))
    return manager.status()


@router.post("/pattern", response_model=TestModeStatus)
def run_pattern(
    request: TestModePatternRequest,
    manager: TestModeManager = Depends(get_test_mode),
    current_user: dict = Depends(require_admin)
):
    """
    Run one cycle of a test pattern (flash, fade, cylon, step, dance, random_pattern).

    Blocks until the pattern finishes; lights are off afterwards.
    """
    _run(lambda: manager.run_pattern(request.name.value))
    return manager.status()
