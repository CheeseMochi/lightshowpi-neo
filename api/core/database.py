"""
Database configuration and schema for LightShowPi Neo API.

This module provides SQLite database initialization and table schemas
for the optional FastAPI backend. The database is only used when API
mode is enabled in configuration.
"""

import sqlite3
import os
import logging
from typing import Optional
from contextlib import contextmanager

log = logging.getLogger(__name__)


class Database:
    """SQLite database manager for LightShowPi Neo API."""

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize database connection.

        Args:
            db_path: Path to SQLite database file. If None, uses default location.
        """
        if db_path is None:
            # Default to SYNCHRONIZED_LIGHTS_HOME/data/lightshowpi.db
            home = os.getenv("SYNCHRONIZED_LIGHTS_HOME")
            if not home:
                raise ValueError("SYNCHRONIZED_LIGHTS_HOME environment variable not set")
            db_path = os.path.join(home, "data", "lightshowpi.db")

        # Ensure data directory exists
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        log.info(f"Database initialized at {db_path}")

    @contextmanager
    def get_connection(self):
        """
        Context manager for database connections.

        Yields:
            sqlite3.Connection: Database connection

        Example:
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users")
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Return rows as dictionaries
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            log.error(f"Database error: {e}")
            raise
        finally:
            conn.close()

    def initialize_schema(self):
        """Create all database tables if they don't exist."""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Users table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    allowed_ips TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Clients table (for multi-Pi support)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS clients (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    auth_key TEXT UNIQUE NOT NULL,
                    ip_address TEXT,
                    last_seen TIMESTAMP,
                    status TEXT DEFAULT 'offline',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Schedule table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS schedule (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_time TEXT NOT NULL,
                    stop_time TEXT NOT NULL,
                    mode TEXT DEFAULT 'playlist',
                    enabled BOOLEAN DEFAULT 1,
                    days_of_week TEXT DEFAULT '[0,1,2,3,4,5,6]',
                    updated_by TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Playlists table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS playlists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    file_path TEXT NOT NULL,
                    is_active BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Songs table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS songs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    playlist_id INTEGER,
                    file_path TEXT NOT NULL,
                    title TEXT,
                    artist TEXT,
                    duration REAL,
                    file_size INTEGER,
                    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE
                )
            """)

            # Song plays table (analytics)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS song_plays (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    song_id INTEGER,
                    client_id TEXT,
                    played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed BOOLEAN,
                    skip_time REAL,
                    FOREIGN KEY (song_id) REFERENCES songs(id) ON DELETE SET NULL,
                    FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE SET NULL
                )
            """)

            # System events table (monitoring)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS system_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    client_id TEXT,
                    details TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE SET NULL
                )
            """)

            # Settings table (key-value store)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create indexes for better query performance
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_song_plays_song_id
                ON song_plays(song_id)
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_song_plays_played_at
                ON song_plays(played_at)
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_system_events_timestamp
                ON system_events(timestamp)
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_system_events_event_type
                ON system_events(event_type)
            """)

            conn.commit()
            log.info("Database schema initialized successfully")

    def migrate_schema(self):
        """Apply database migrations for schema updates."""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Migration: Add mode column to schedule table if it doesn't exist
            cursor.execute("PRAGMA table_info(schedule)")
            columns = [col[1] for col in cursor.fetchall()]

            if 'mode' not in columns:
                log.info("Migrating schedule table: adding mode column")
                cursor.execute("""
                    ALTER TABLE schedule ADD COLUMN mode TEXT DEFAULT 'playlist'
                """)
                conn.commit()
                log.info("Migration completed: mode column added to schedule table")

    def insert_default_data(self):
        """Insert default data if tables are empty."""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Check if default schedule exists
            cursor.execute("SELECT COUNT(*) FROM schedule")
            if cursor.fetchone()[0] == 0:
                cursor.execute("""
                    INSERT INTO schedule (start_time, stop_time, enabled)
                    VALUES ('18:30', '22:15', 1)
                """)
                log.info("Inserted default schedule (6:30 PM - 10:15 PM)")

            # Check if default playlist exists
            cursor.execute("SELECT COUNT(*) FROM playlists")
            if cursor.fetchone()[0] == 0:
                home = os.getenv("SYNCHRONIZED_LIGHTS_HOME")
                default_playlist = os.path.join(home, "music", ".playlist")
                cursor.execute("""
                    INSERT INTO playlists (name, file_path, is_active)
                    VALUES ('Default', ?, 1)
                """, (default_playlist,))
                log.info("Inserted default playlist")

            conn.commit()


def init_database(db_path: Optional[str] = None) -> Database:
    """
    Initialize and return database instance.

    Args:
        db_path: Optional path to database file

    Returns:
        Database: Initialized database instance
    """
    db = Database(db_path)
    db.initialize_schema()
    db.migrate_schema()
    db.insert_default_data()
    return db


if __name__ == "__main__":
    # Allow running this module directly to initialize the database
    logging.basicConfig(level=logging.INFO)
    init_database()
    print("Database initialized successfully")
