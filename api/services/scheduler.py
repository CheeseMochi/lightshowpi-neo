"""
Scheduling service for LightShowPi Neo API.

This service uses APScheduler to automatically start and stop lightshows
based on user-defined schedules stored in the database.
"""

import logging
import json
from datetime import datetime, time
from typing import List, Dict, Optional
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR

from api.core.database import Database
from api.services.lightshow_manager import LightshowManager

log = logging.getLogger(__name__)


class SchedulerService:
    """Manages scheduled lightshow start/stop times using APScheduler."""

    def __init__(self, database: Database, lightshow_manager: LightshowManager):
        """
        Initialize the scheduler service.

        Args:
            database: Database instance for loading schedules
            lightshow_manager: LightshowManager instance for controlling lightshow
        """
        self.db = database
        self.lightshow = lightshow_manager
        self.scheduler = BackgroundScheduler()

        # Add event listeners
        self.scheduler.add_listener(
            self._job_executed,
            EVENT_JOB_EXECUTED | EVENT_JOB_ERROR
        )

        self.running = False

    def start(self):
        """Start the scheduler and load all enabled schedules."""
        if self.running:
            log.warning("Scheduler already running")
            return

        log.info("Starting scheduler service...")

        # Load and schedule all enabled schedules from database
        self.reload_schedules()

        # Start the scheduler
        self.scheduler.start()
        self.running = True

        log.info("Scheduler service started successfully")

    def stop(self):
        """Stop the scheduler."""
        if not self.running:
            return

        log.info("Stopping scheduler service...")
        self.scheduler.shutdown(wait=False)
        self.running = False
        log.info("Scheduler service stopped")

    def reload_schedules(self):
        """Reload all schedules from database and update APScheduler jobs."""
        log.info("Reloading schedules from database...")

        # Remove all existing jobs
        self.scheduler.remove_all_jobs()

        # Load enabled schedules from database
        schedules = self.get_schedules(enabled_only=True)

        # Create APScheduler jobs for each schedule
        for schedule in schedules:
            self._create_schedule_jobs(schedule)

        log.info(f"Loaded {len(schedules)} enabled schedule(s)")

    def _create_schedule_jobs(self, schedule: Dict):
        """
        Create APScheduler jobs for a schedule's start and stop times.

        Args:
            schedule: Schedule dict from database with start_time, stop_time, mode, days_of_week
        """
        schedule_id = schedule['id']
        start_time = schedule['start_time']  # Format: "HH:MM"
        stop_time = schedule['stop_time']    # Format: "HH:MM"
        mode = schedule.get('mode', 'playlist')  # Default to playlist mode
        days_of_week = schedule['days_of_week']  # Already parsed to list in get_schedules()

        # Parse start and stop times
        start_hour, start_minute = map(int, start_time.split(':'))
        stop_hour, stop_minute = map(int, stop_time.split(':'))

        # Convert days list to cron format (0=Monday in cron, 0=Sunday in our DB)
        # Our DB: 0=Sunday, 1=Monday, ..., 6=Saturday
        # Cron: 0=Monday, 1=Tuesday, ..., 6=Sunday
        cron_days = [(day - 1) % 7 if day > 0 else 6 for day in days_of_week]
        cron_days_str = ','.join(map(str, sorted(cron_days)))

        # Create start job
        start_trigger = CronTrigger(
            day_of_week=cron_days_str,
            hour=start_hour,
            minute=start_minute
        )

        self.scheduler.add_job(
            func=self._start_lightshow,
            trigger=start_trigger,
            args=[mode],  # Pass mode to start function
            id=f"schedule_{schedule_id}_start",
            name=f"Start lightshow (Schedule {schedule_id})",
            replace_existing=True
        )

        # Create stop job
        stop_trigger = CronTrigger(
            day_of_week=cron_days_str,
            hour=stop_hour,
            minute=stop_minute
        )

        self.scheduler.add_job(
            func=self._stop_lightshow,
            trigger=stop_trigger,
            id=f"schedule_{schedule_id}_stop",
            name=f"Stop lightshow (Schedule {schedule_id})",
            replace_existing=True
        )

        log.info(f"Created jobs for schedule {schedule_id}: "
                f"start at {start_time}, stop at {stop_time}, "
                f"days {days_of_week}")

    def _start_lightshow(self, mode: str = "playlist"):
        """Start the lightshow (called by scheduler).

        Args:
            mode: Lightshow mode to start (playlist, ambient, audio-in, stream-in)
        """
        log.info(f"Scheduled start triggered (mode: {mode})")
        try:
            success = self.lightshow.start(mode=mode)
            if success:
                log.info(f"Lightshow started successfully by scheduler in {mode} mode")
            else:
                log.warning("Failed to start lightshow (may already be running)")
        except Exception as e:
            log.error(f"Error starting lightshow from schedule: {e}")

    def _stop_lightshow(self):
        """Stop the lightshow (called by scheduler)."""
        log.info("Scheduled stop triggered")
        try:
            success = self.lightshow.stop(graceful=True)
            if success:
                log.info("Lightshow stopped successfully by scheduler")
            else:
                log.warning("Failed to stop lightshow (may not be running)")
        except Exception as e:
            log.error(f"Error stopping lightshow from schedule: {e}")

    def _job_executed(self, event):
        """Event listener for job execution (logging)."""
        if event.exception:
            log.error(f"Scheduled job {event.job_id} failed: {event.exception}")
        else:
            log.debug(f"Scheduled job {event.job_id} executed successfully")

    # Database CRUD operations for schedules

    def get_schedules(self, enabled_only: bool = False) -> List[Dict]:
        """
        Get all schedules from database.

        Args:
            enabled_only: If True, only return enabled schedules

        Returns:
            List of schedule dictionaries
        """
        with self.db.get_connection() as conn:
            cursor = conn.cursor()

            if enabled_only:
                cursor.execute("""
                    SELECT id, start_time, stop_time, mode, enabled, days_of_week,
                           updated_by, updated_at
                    FROM schedule
                    WHERE enabled = 1
                    ORDER BY start_time
                """)
            else:
                cursor.execute("""
                    SELECT id, start_time, stop_time, mode, enabled, days_of_week,
                           updated_by, updated_at
                    FROM schedule
                    ORDER BY start_time
                """)

            rows = cursor.fetchall()
            schedules = []
            for row in rows:
                schedule = dict(row)
                # Parse JSON string to list
                schedule['days_of_week'] = json.loads(schedule['days_of_week'])
                schedules.append(schedule)
            return schedules

    def get_schedule(self, schedule_id: int) -> Optional[Dict]:
        """
        Get a specific schedule by ID.

        Args:
            schedule_id: Schedule ID

        Returns:
            Schedule dict or None if not found
        """
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, start_time, stop_time, mode, enabled, days_of_week,
                       updated_by, updated_at
                FROM schedule
                WHERE id = ?
            """, (schedule_id,))

            row = cursor.fetchone()
            if row:
                schedule = dict(row)
                # Parse JSON string to list
                schedule['days_of_week'] = json.loads(schedule['days_of_week'])
                return schedule
            return None

    def create_schedule(
        self,
        start_time: str,
        stop_time: str,
        days_of_week: List[int],
        mode: str = "playlist",
        enabled: bool = True,
        updated_by: Optional[str] = None
    ) -> int:
        """
        Create a new schedule.

        Args:
            start_time: Start time in HH:MM format
            stop_time: Stop time in HH:MM format
            days_of_week: List of days (0=Sunday, 1=Monday, ..., 6=Saturday)
            mode: Lightshow mode (playlist, ambient, audio-in, stream-in)
            enabled: Whether schedule is enabled
            updated_by: Username who created the schedule

        Returns:
            ID of created schedule
        """
        days_json = json.dumps(days_of_week)

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO schedule (start_time, stop_time, mode, enabled, days_of_week, updated_by)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (start_time, stop_time, mode, enabled, days_json, updated_by))

            schedule_id = cursor.lastrowid
            log.info(f"Created schedule {schedule_id}")

        # Reload schedules to activate new schedule
        if enabled:
            self.reload_schedules()

        return schedule_id

    def update_schedule(
        self,
        schedule_id: int,
        start_time: Optional[str] = None,
        stop_time: Optional[str] = None,
        mode: Optional[str] = None,
        days_of_week: Optional[List[int]] = None,
        enabled: Optional[bool] = None,
        updated_by: Optional[str] = None
    ) -> bool:
        """
        Update an existing schedule.

        Args:
            schedule_id: ID of schedule to update
            start_time: New start time (optional)
            stop_time: New stop time (optional)
            mode: New mode (optional)
            days_of_week: New days list (optional)
            enabled: New enabled status (optional)
            updated_by: Username who updated the schedule

        Returns:
            True if schedule was updated, False if not found
        """
        # Build update query dynamically
        updates = []
        params = []

        if start_time is not None:
            updates.append("start_time = ?")
            params.append(start_time)

        if stop_time is not None:
            updates.append("stop_time = ?")
            params.append(stop_time)

        if mode is not None:
            updates.append("mode = ?")
            params.append(mode)

        if days_of_week is not None:
            updates.append("days_of_week = ?")
            params.append(json.dumps(days_of_week))

        if enabled is not None:
            updates.append("enabled = ?")
            params.append(enabled)

        if updated_by is not None:
            updates.append("updated_by = ?")
            params.append(updated_by)

        updates.append("updated_at = CURRENT_TIMESTAMP")

        if not updates:
            return False

        params.append(schedule_id)
        query = f"UPDATE schedule SET {', '.join(updates)} WHERE id = ?"

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)

            if cursor.rowcount == 0:
                return False

            log.info(f"Updated schedule {schedule_id}")

        # Reload schedules to apply changes
        self.reload_schedules()
        return True

    def delete_schedule(self, schedule_id: int) -> bool:
        """
        Delete a schedule.

        Args:
            schedule_id: ID of schedule to delete

        Returns:
            True if schedule was deleted, False if not found
        """
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM schedule WHERE id = ?", (schedule_id,))

            if cursor.rowcount == 0:
                return False

            log.info(f"Deleted schedule {schedule_id}")

        # Reload schedules to remove deleted schedule
        self.reload_schedules()
        return True

    def get_next_scheduled_events(self, limit: int = 10) -> List[Dict]:
        """
        Get the next scheduled events (start/stop times).

        Args:
            limit: Maximum number of events to return

        Returns:
            List of upcoming events with time and action
        """
        jobs = self.scheduler.get_jobs()
        events = []

        for job in jobs:
            next_run = job.next_run_time
            if next_run:
                action = "start" if "start" in job.id else "stop"
                schedule_id = int(job.id.split('_')[1])

                events.append({
                    "schedule_id": schedule_id,
                    "action": action,
                    "time": next_run.isoformat(),
                    "job_id": job.id
                })

        # Sort by time and limit
        events.sort(key=lambda x: x['time'])
        return events[:limit]
