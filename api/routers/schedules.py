"""
Schedule management API endpoints.

Provides REST API for creating, reading, updating, and deleting
lightshow schedules.
"""

import logging
from typing import List
from fastapi import APIRouter, HTTPException, Depends, status

from api.core.auth import require_admin, get_current_user
from api.models.schemas import (
    ScheduleResponse,
    ScheduleCreate,
    ScheduleUpdate,
    ScheduledEventResponse
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/schedules", tags=["schedules"])

# Dependency injection will be set in main.py
_scheduler_service = None


def get_scheduler():
    """Dependency to get scheduler service instance."""
    if _scheduler_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scheduler service not initialized"
        )
    return _scheduler_service


def set_scheduler(scheduler):
    """Set the scheduler service instance (called from main.py)."""
    global _scheduler_service
    _scheduler_service = scheduler


@router.get("", response_model=List[ScheduleResponse])
async def list_schedules(
    enabled_only: bool = False,
    current_user: dict = Depends(get_current_user)
):
    """
    List all schedules.

    Args:
        enabled_only: If true, only return enabled schedules

    Returns:
        List of schedules
    """
    scheduler = get_scheduler()
    schedules = scheduler.get_schedules(enabled_only=enabled_only)
    return schedules


@router.get("/{schedule_id}", response_model=ScheduleResponse)
async def get_schedule(
    schedule_id: int,
    current_user: dict = Depends(get_current_user)
):
    """
    Get a specific schedule by ID.

    Args:
        schedule_id: Schedule ID

    Returns:
        Schedule details
    """
    scheduler = get_scheduler()
    schedule = scheduler.get_schedule(schedule_id)

    if not schedule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schedule {schedule_id} not found"
        )

    return schedule


@router.post("", response_model=ScheduleResponse, status_code=status.HTTP_201_CREATED)
async def create_schedule(
    schedule_data: ScheduleCreate,
    current_user: dict = Depends(require_admin)
):
    """
    Create a new schedule.

    Args:
        schedule_data: Schedule configuration

    Returns:
        Created schedule with ID
    """
    scheduler = get_scheduler()

    try:
        schedule_id = scheduler.create_schedule(
            start_time=schedule_data.start_time,
            stop_time=schedule_data.stop_time,
            mode=schedule_data.mode.value,  # Convert enum to string
            days_of_week=schedule_data.days_of_week,
            enabled=schedule_data.enabled,
            updated_by=current_user.get("username")
        )

        # Return the created schedule
        schedule = scheduler.get_schedule(schedule_id)
        return schedule

    except Exception as e:
        log.error(f"Failed to create schedule: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create schedule: {str(e)}"
        )


@router.patch("/{schedule_id}", response_model=ScheduleResponse)
async def update_schedule(
    schedule_id: int,
    schedule_data: ScheduleUpdate,
    current_user: dict = Depends(require_admin)
):
    """
    Update an existing schedule.

    Args:
        schedule_id: Schedule ID to update
        schedule_data: Fields to update

    Returns:
        Updated schedule
    """
    scheduler = get_scheduler()

    # Check if schedule exists
    existing = scheduler.get_schedule(schedule_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schedule {schedule_id} not found"
        )

    try:
        # Update schedule (only provided fields)
        success = scheduler.update_schedule(
            schedule_id=schedule_id,
            start_time=schedule_data.start_time,
            stop_time=schedule_data.stop_time,
            mode=schedule_data.mode.value if schedule_data.mode else None,  # Convert enum to string
            days_of_week=schedule_data.days_of_week,
            enabled=schedule_data.enabled,
            updated_by=current_user.get("username")
        )

        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update schedule"
            )

        # Return updated schedule
        schedule = scheduler.get_schedule(schedule_id)
        return schedule

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Failed to update schedule {schedule_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update schedule: {str(e)}"
        )


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    schedule_id: int,
    current_user: dict = Depends(require_admin)
):
    """
    Delete a schedule.

    Args:
        schedule_id: Schedule ID to delete
    """
    scheduler = get_scheduler()

    success = scheduler.delete_schedule(schedule_id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schedule {schedule_id} not found"
        )

    return None


@router.get("/upcoming/events", response_model=List[ScheduledEventResponse])
async def get_upcoming_events(
    limit: int = 10,
    current_user: dict = Depends(get_current_user)
):
    """
    Get upcoming scheduled events (next start/stop times).

    Args:
        limit: Maximum number of events to return (default: 10)

    Returns:
        List of upcoming scheduled events
    """
    scheduler = get_scheduler()

    try:
        events = scheduler.get_next_scheduled_events(limit=limit)
        return events
    except Exception as e:
        log.error(f"Failed to get upcoming events: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get upcoming events: {str(e)}"
        )
