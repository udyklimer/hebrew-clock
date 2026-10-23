import datetime
import json
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
                battery_position TEXT DEFAULT 'left',
                batch_minutes INTEGER DEFAULT 1,
                language TEXT DEFAULT 'he',
                grayscale TEXT DEFAULT '0',
                bottom_bar TEXT DEFAULT '1'
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
            ("language", "he"), ("grayscale", "0"), ("bottom_bar", "1"),
        ):
            try:
                conn.execute(
                    f"ALTER TABLE user_settings ADD COLUMN {column} TEXT DEFAULT '{default}'"
                )
            except sqlite3.OperationalError:
                pass  # Column already exists

        # Battery save mode: minutes of images fetched per connection
        try:
            conn.execute("ALTER TABLE user_settings ADD COLUMN batch_minutes INTEGER DEFAULT 1")
        except sqlite3.OperationalError:
            pass  # Column already exists

        # What the user's device last reported (no default: empty until it does)
        for column in ("device_fw", "device_seen"):
            try:
                conn.execute(f"ALTER TABLE user_settings ADD COLUMN {column} TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists

        # Devices: each one reports its own ID; settings is NULL (use the
        # user's settings) or a JSON object with the device's own settings
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS devices (
                device_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                name TEXT,
                fw TEXT,
                last_seen TEXT,
                battery_mv INTEGER,
                charging INTEGER,
                settings TEXT
            )
        """
        )

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
            "battery_display, battery_position, batch_minutes, language, grayscale, bottom_bar "
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
                "batch_minutes": int(row["batch_minutes"] or 1),
                "language": row["language"] or "he",
                "grayscale": row["grayscale"] or "0",
                "bottom_bar": row["bottom_bar"] or "1",
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
            "batch_minutes": 1,
            "language": "he",
            "grayscale": "0",
            "bottom_bar": "1",
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
    batch_minutes: int = 1,
    language: str = "he",
    grayscale: str = "0",
    bottom_bar: str = "1",
):
    init_db()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO user_settings
                (username, font, location, calendar, sleeptime, blank, clock_style, sleep_start, sleep_end,
                 battery_display, battery_position, batch_minutes, language, grayscale, bottom_bar)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                battery_position=excluded.battery_position,
                batch_minutes=excluded.batch_minutes,
                language=excluded.language,
                grayscale=excluded.grayscale,
                bottom_bar=excluded.bottom_bar
            """,
            (username.lower(), font, location, calendar, sleeptime, blank, clock_style,
             sleep_start, sleep_end, battery_display, battery_position, batch_minutes,
             language, grayscale, bottom_bar),
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


# ── Devices ───────────────────────────────────────────

MAX_DEVICES_PER_USER = 20
_DEVICE_ID = re.compile(r"[0-9a-z_-]{4,40}")
_DEVICE_NAME_MAX = 40


def clean_device_id(device_id: str | None) -> str | None:
    """A device ID in canonical form (lower case), or None if it is not valid."""
    value = (device_id or "").strip().lower()
    return value if _DEVICE_ID.fullmatch(value) else None


def _device_dict(row: sqlite3.Row) -> dict:
    return {
        "device_id": row["device_id"],
        "name": row["name"],
        "fw": row["fw"],
        "last_seen": datetime.datetime.fromisoformat(row["last_seen"]) if row["last_seen"] else None,
        "battery_mv": row["battery_mv"],
        "charging": None if row["charging"] is None else bool(row["charging"]),
        "settings": json.loads(row["settings"]) if row["settings"] else None,
    }


def record_device(username: str, device_id: str, fw: str | None,
                  battery_mv: int | None, charging: bool | None) -> None:
    """Stores what a device reported. Only for existing users.

    A device that moves to another user starts fresh there (no name, no own
    settings). A user keeps at most MAX_DEVICES_PER_USER; the device seen
    longest ago makes room for a new one.
    """
    init_db()
    username = username.lower()
    seen = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            return
        row = conn.execute("SELECT username FROM devices WHERE device_id = ?", (device_id,)).fetchone()
        if row is None:
            count = conn.execute("SELECT COUNT(*) FROM devices WHERE username = ?", (username,)).fetchone()[0]
            if count >= MAX_DEVICES_PER_USER:
                conn.execute(
                    "DELETE FROM devices WHERE device_id = (SELECT device_id FROM devices "
                    "WHERE username = ? ORDER BY last_seen LIMIT 1)", (username,))
            conn.execute("INSERT INTO devices (device_id, username) VALUES (?, ?)", (device_id, username))
        elif row["username"] != username:
            conn.execute("UPDATE devices SET username = ?, name = NULL, settings = NULL WHERE device_id = ?",
                         (username, device_id))
            # The old user's "last reported version" may have come from this
            # device; forget it so their page doesn't show a device they no
            # longer have (a device of theirs without an ID refills it)
            conn.execute("UPDATE user_settings SET device_fw = NULL, device_seen = NULL WHERE username = ?",
                         (row["username"],))
        conn.execute(
            "UPDATE devices SET fw = COALESCE(?, fw), last_seen = ?, battery_mv = COALESCE(?, battery_mv), "
            "charging = ? WHERE device_id = ?",
            ((fw or "").strip()[:32] or None, seen, battery_mv,
             None if charging is None else int(charging), device_id),
        )


def list_devices(username: str) -> list[dict]:
    """The user's devices, oldest first."""
    init_db()
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM devices WHERE username = ? ORDER BY rowid",
                            (username.lower(),)).fetchall()
    return [_device_dict(r) for r in rows]


def get_device(username: str, device_id: str) -> dict | None:
    """One of the user's devices, or None if it is not theirs."""
    init_db()
    with get_db() as conn:
        row = conn.execute("SELECT * FROM devices WHERE device_id = ? AND username = ?",
                           (device_id, username.lower())).fetchone()
    return _device_dict(row) if row else None


def rename_device(username: str, device_id: str, name: str) -> None:
    init_db()
    with get_db() as conn:
        conn.execute("UPDATE devices SET name = ? WHERE device_id = ? AND username = ?",
                     (name.strip()[:_DEVICE_NAME_MAX] or None, device_id, username.lower()))


def delete_device(username: str, device_id: str) -> None:
    init_db()
    with get_db() as conn:
        conn.execute("DELETE FROM devices WHERE device_id = ? AND username = ?",
                     (device_id, username.lower()))


def set_device_settings(username: str, device_id: str, values: dict | None) -> None:
    """Gives a device its own settings, or (None) makes it use the user's again."""
    init_db()
    with get_db() as conn:
        conn.execute("UPDATE devices SET settings = ? WHERE device_id = ? AND username = ?",
                     (json.dumps(values) if values is not None else None, device_id, username.lower()))


def get_effective_settings(username: str, device_id: str | None = None) -> dict:
    """The settings a device draws with: the user's, replaced by the device's own if it has them.

    A device's own settings count only while the user has more than one device;
    with a single device the settings page shows no device list, so that device
    always uses the user's settings.
    """
    values = get_user_settings(username)
    device = get_device(username, device_id) if device_id else None
    if device and device["settings"] and len(list_devices(username)) > 1:
        values.update(device["settings"])
    return values
