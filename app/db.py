import datetime
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
                blank TEXT DEFAULT '0',
                clock_style TEXT DEFAULT 'analog',
                sleep_start TEXT DEFAULT '22:00',
                sleep_end TEXT DEFAULT '06:00',
                battery_display TEXT DEFAULT 'none',
                battery_position TEXT DEFAULT 'left'
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

        # Ensure 'clock_style' column exists for existing database files
        try:
            conn.execute(
                "ALTER TABLE user_settings ADD COLUMN clock_style TEXT DEFAULT 'analog'"
            )
        except sqlite3.OperationalError:
            pass  # Column already exists

        # Ensure newer columns exist for existing database files
        for column, default in (
            ("sleep_start", "22:00"), ("sleep_end", "06:00"),
            ("battery_display", "none"), ("battery_position", "left"),
        ):
            try:
                conn.execute(
                    f"ALTER TABLE user_settings ADD COLUMN {column} TEXT DEFAULT '{default}'"
                )
            except sqlite3.OperationalError:
                pass  # Column already exists

        # What the user's device last reported (no default: empty until it does)
        for column in ("device_fw", "device_seen"):
            try:
                conn.execute(f"ALTER TABLE user_settings ADD COLUMN {column} TEXT")
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
            "SELECT font, location, calendar, sleeptime, blank, clock_style, sleep_start, sleep_end, "
            "battery_display, battery_position "
            "FROM user_settings WHERE username = ?",
            (username.lower(),),
        ).fetchone()
        if row:
            return {
                "font": row["font"],
                "location": row["location"],
                "calendar": row["calendar"],
                "sleeptime": row["sleeptime"],
                "blank": str(row["blank"]) if "blank" in row.keys() else "0",
                "clock_style": row["clock_style"] or "analog",
                "sleep_start": row["sleep_start"] or "22:00",
                "sleep_end": row["sleep_end"] or "06:00",
                "battery_display": row["battery_display"] or "none",
                "battery_position": row["battery_position"] or "left",
            }
        return {
            "font": "DavidLibre-Bold",
            "location": "Haifa",
            "calendar": "gregorian",
            "sleeptime": "0",
            "blank": "0",
            "clock_style": "analog",
            "sleep_start": "22:00",
            "sleep_end": "06:00",
            "battery_display": "none",
            "battery_position": "left",
        }


def update_user_settings(
    username: str,
    font: str,
    location: str,
    calendar: str,
    sleeptime: str,
    blank: str = "0",
    clock_style: str = "analog",
    sleep_start: str = "22:00",
    sleep_end: str = "06:00",
    battery_display: str = "none",
    battery_position: str = "left",
):
    init_db()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO user_settings
                (username, font, location, calendar, sleeptime, blank, clock_style, sleep_start, sleep_end,
                 battery_display, battery_position)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                font=excluded.font,
                location=excluded.location,
                calendar=excluded.calendar,
                sleeptime=excluded.sleeptime,
                blank=excluded.blank,
                clock_style=excluded.clock_style,
                sleep_start=excluded.sleep_start,
                sleep_end=excluded.sleep_end,
                battery_display=excluded.battery_display,
                battery_position=excluded.battery_position
            """,
            (username.lower(), font, location, calendar, sleeptime, blank, clock_style,
             sleep_start, sleep_end, battery_display, battery_position),
        )


def record_device_firmware(username: str, version: str) -> None:
    """Stores the firmware version a user's device reported, and when."""
    init_db()
    seen = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with get_db() as conn:
        conn.execute(
            "UPDATE user_settings SET device_fw = ?, device_seen = ? WHERE username = ?",
            (version.strip()[:32], seen, username.lower()),
        )


def get_device_firmware(username: str) -> tuple[str | None, datetime.datetime | None]:
    """Returns the firmware version the user's device last reported and when (UTC)."""
    init_db()
    with get_db() as conn:
        row = conn.execute(
            "SELECT device_fw, device_seen FROM user_settings WHERE username = ?",
            (username.lower(),),
        ).fetchone()
    if not row or not row["device_fw"]:
        return None, None
    seen = datetime.datetime.fromisoformat(row["device_seen"]) if row["device_seen"] else None
    return row["device_fw"], seen
