import sqlite3
import re

from app.core.config import settings
from app.core.security import hash_password, is_hashed, verify_password

# DB file path
DB_PATH = settings.data_dir / "clock.db"


def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    # Automatically create tables if they do not exist
    with get_db() as conn:
        # Table for user authentication
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password TEXT NOT NULL
            )
        """
        )

        # Table for user configuration settings
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_settings (
                username TEXT PRIMARY KEY,
                font TEXT NOT NULL,
                location TEXT NOT NULL,
                calendar TEXT NOT NULL,
                sleeptime TEXT NOT NULL,
                blank TEXT DEFAULT '0'
            )
        """
        )

        # Ensure 'blank' column exists for existing database files
        try:
            conn.execute(
                "ALTER TABLE user_settings ADD COLUMN blank TEXT DEFAULT '0'"
            )
        except sqlite3.OperationalError:
            pass  # Column already exists

        # Hash any passwords left in plaintext by older versions
        for row in conn.execute("SELECT username, password FROM users").fetchall():
            if not is_hashed(row["password"]):
                conn.execute(
                    "UPDATE users SET password = ? WHERE username = ?",
                    (hash_password(row["password"]), row["username"]),
                )


def is_valid_username(username: str) -> bool:
    # Allow alphanumeric characters and underscores, length 3-30
    return bool(re.match(r"^[a-zA-Z0-9_]{3,30}$", username))


def register_user(username: str, password: str) -> bool:
    init_db()
    clean_uname = username.strip().lower()
    with get_db() as conn:
        try:
            conn.execute(
                "INSERT INTO users (username, password) VALUES (?, ?)",
                (clean_uname, hash_password(password)),
            )
            # Create default settings upon user creation
            conn.execute(
                """
                INSERT OR IGNORE INTO user_settings (username, font, location, calendar, sleeptime, blank)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (clean_uname, "DavidLibre-Bold", "Haifa", "gregorian", "0", "0"),
            )
            return True
        except sqlite3.IntegrityError:
            return False  # Username already exists


def authenticate_user(username: str, password: str) -> bool:
    init_db()
    clean_uname = username.strip().lower()
    with get_db() as conn:
        row = conn.execute(
            "SELECT password FROM users WHERE username = ?", (clean_uname,)
        ).fetchone()
        return bool(row) and verify_password(password, row["password"])


def get_user_settings(username: str) -> dict:
    init_db()
    with get_db() as conn:
        row = conn.execute(
            "SELECT font, location, calendar, sleeptime, blank FROM user_settings WHERE username = ?",
            (username.lower(),),
        ).fetchone()
        if row:
            return {
                "font": row["font"],
                "location": row["location"],
                "calendar": row["calendar"],
                "sleeptime": row["sleeptime"],
                "blank": str(row["blank"]) if "blank" in row.keys() else "0",
            }
        return {
            "font": "DavidLibre-Bold",
            "location": "Haifa",
            "calendar": "gregorian",
            "sleeptime": "0",
            "blank": "0",
        }


def update_user_settings(
    username: str,
    font: str,
    location: str,
    calendar: str,
    sleeptime: str,
    blank: str = "0",
):
    init_db()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO user_settings (username, font, location, calendar, sleeptime, blank)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                font=excluded.font,
                location=excluded.location,
                calendar=excluded.calendar,
                sleeptime=excluded.sleeptime,
                blank=excluded.blank
            """,
            (username.lower(), font, location, calendar, sleeptime, blank),
        )