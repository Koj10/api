import logging
from datetime import datetime

from database import SQL_request
from utils import parse_db_datetime


def ensure_play_time_columns():
    columns = {
        row["name"]
        for row in (SQL_request("PRAGMA table_info(users)", fetch="all") or [])
    }
    if "play_time_minutes" not in columns:
        SQL_request(
            "ALTER TABLE users ADD COLUMN play_time_minutes INTEGER NOT NULL DEFAULT 0",
            fetch="none",
        )
    if "profile_public" not in columns:
        SQL_request(
            "ALTER TABLE users ADD COLUMN profile_public INTEGER NOT NULL DEFAULT 0",
            fetch="none",
        )


def ensure_play_sessions_table():
    SQL_request(
        """CREATE TABLE IF NOT EXISTS play_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id),
        computer_id INTEGER REFERENCES computers(id),
        started_at TEXT,
        ended_at TEXT NOT NULL,
        duration_minutes INTEGER NOT NULL,
        source TEXT
    )""",
        fetch="none",
    )


def calculate_played_minutes(computer, ended_at=None):
    user_id = computer.get("user_active")
    if not user_id:
        return 0

    ended_at = ended_at or datetime.now()
    started_at = parse_db_datetime(computer.get("session_started_at"))
    duration_limit = computer.get("session_duration_minutes")

    if started_at:
        elapsed = (ended_at - started_at).total_seconds() / 60
        played = max(0, int(round(elapsed)))
        if duration_limit is not None:
            played = min(played, int(duration_limit))
        return played

    time_active = parse_db_datetime(computer.get("time_active"))
    if time_active and time_active <= ended_at:
        return 0

    return 0


def credit_play_time(user_id, minutes, computer_id=None, source="session"):
    minutes = int(minutes or 0)
    if minutes <= 0 or not user_id:
        return

    ensure_play_time_columns()
    SQL_request(
        "UPDATE users SET play_time_minutes = COALESCE(play_time_minutes, 0) + ? WHERE id = ?",
        params=(minutes, user_id),
        fetch="none",
    )
    ensure_play_sessions_table()
    SQL_request(
        """
        INSERT INTO play_sessions (user_id, computer_id, ended_at, duration_minutes, source)
        VALUES (?, ?, datetime('now'), ?, ?)
        """,
        params=(user_id, computer_id, minutes, source),
        fetch="none",
    )


def finalize_computer_session(computer, source="session_end"):
    if not computer or computer.get("status") != "занят":
        return 0

    user_id = computer.get("user_active")
    if not user_id:
        return 0

    minutes = calculate_played_minutes(computer)
    if minutes > 0:
        try:
            credit_play_time(
                user_id,
                minutes,
                computer_id=computer.get("id"),
                source=source,
            )
        except Exception as error:
            logging.warning("finalize_computer_session: %s", error)
    return minutes
