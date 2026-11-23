"""
Database module for RingCare.
Handles SQLite connection, schema creation, migrations, and CRUD operations.
"""

import sqlite3
import datetime
import json
import logging
from typing import Optional, List, Dict, Any
from pathlib import Path
from config import DATABASE_PATH, DEMO_MODE, get_now, CHECKIN_WINDOW_START, CHECKIN_TARGET, format_timestamp
import snapshot_service

logger = logging.getLogger("ringcare.db")

def get_db_connection() -> sqlite3.Connection:
    """Creates a new SQLite database connection with row factory enabled."""
    conn = sqlite3.connect(DATABASE_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn

def init_db():
    """Initializes database schema and runs initial seeding if needed."""
    db_file = Path(DATABASE_PATH)
    is_new = not db_file.exists() or db_file.stat().st_size == 0

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Tokens table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT,
                access_token TEXT NOT NULL,
                refresh_token TEXT NOT NULL,
                expires_at REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'unclaimed',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                account_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Devices table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY,
                account_id TEXT,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                location TEXT,
                online INTEGER NOT NULL DEFAULT 1,
                battery_level INTEGER,
                quiet_hours_enabled INTEGER NOT NULL DEFAULT 0,
                quiet_hours_start TEXT DEFAULT '22:00',
                quiet_hours_end TEXT DEFAULT '07:00',
                sensitivity TEXT DEFAULT 'medium',
                capabilities TEXT,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Events table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT UNIQUE NOT NULL,
                event_id TEXT,
                device_id TEXT NOT NULL,
                device_name TEXT NOT NULL,
                event_type TEXT NOT NULL,
                sub_type TEXT,
                timestamp TIMESTAMP NOT NULL,
                snapshot_url TEXT,
                video_url TEXT,
                visitor_tag TEXT DEFAULT 'unassigned',
                visitor_notes TEXT DEFAULT '',
                processed INTEGER NOT NULL DEFAULT 1,
                received_offline INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Safe migration if table exists without received_offline
        try:
            cursor.execute("ALTER TABLE events ADD COLUMN received_offline INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass

        # Care Check-in table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS checkins (
                date TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending',
                activity_time TIMESTAMP,
                alert_sent INTEGER NOT NULL DEFAULT 0,
                notes TEXT DEFAULT '',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Settings table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        conn.commit()

    if is_new or DEMO_MODE:
        seed_initial_data()

def seed_initial_data():
    """Seeds demo user, mock devices, and demo events if not already present."""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Seed Demo User
        cursor.execute("SELECT COUNT(*) FROM users")
        if cursor.fetchone()[0] == 0:
            cursor.execute("""
                INSERT INTO users (email, name, account_id)
                VALUES (?, ?, ?)
            """, ("caregiver@family.org", "Sarah (Caregiver)", "ring_acc_grandma_4481"))

        # Seed Demo Token
        cursor.execute("SELECT COUNT(*) FROM tokens")
        if cursor.fetchone()[0] == 0:
            now_ts = datetime.datetime.now().timestamp()
            cursor.execute("""
                INSERT INTO tokens (account_id, access_token, refresh_token, expires_at, status)
                VALUES (?, ?, ?, ?, ?)
            """, (
                "ring_acc_grandma_4481",
                "demo_access_token_ring_caregiver_secure",
                "demo_refresh_token_ring_caregiver_30days",
                now_ts + (4 * 3600),
                "claimed"
            ))

        # Seed Demo Devices
        cursor.execute("SELECT COUNT(*) FROM devices")
        if cursor.fetchone()[0] == 0:
            devices_data = [
                (
                    "dev_front_door_01",
                    "ring_acc_grandma_4481",
                    "Front Door Video Doorbell Pro 2",
                    "doorbell",
                    "Front Porch",
                    1,
                    94,
                    0,
                    "22:00",
                    "07:00",
                    "high",
                    json.dumps(["video", "snapshot", "chime", "whep", "two_way_audio"])
                ),
                (
                    "dev_hallway_cam_02",
                    "ring_acc_grandma_4481",
                    "Hallway Stick Up Cam",
                    "stickup_cam",
                    "Living Room / Hallway",
                    1,
                    88,
                    0,
                    "23:00",
                    "06:30",
                    "medium",
                    json.dumps(["video", "snapshot", "motion_detect"])
                ),
                (
                    "dev_kitchen_sensor_03",
                    "ring_acc_grandma_4481",
                    "Kitchen Motion & Door Contact",
                    "motion_sensor",
                    "Kitchen",
                    1,
                    100,
                    0,
                    "22:00",
                    "07:00",
                    "high",
                    json.dumps(["motion_detect", "contact_sensor"])
                ),
            ]
            cursor.executemany("""
                INSERT INTO devices (
                    id, account_id, name, kind, location, online, battery_level,
                    quiet_hours_enabled, quiet_hours_start, quiet_hours_end, sensitivity, capabilities
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, devices_data)

        # Seed Today's Check-in
        snapshot_service.init_snapshots()
        now = get_now()
        today_str = now.date().isoformat()
        cursor.execute("SELECT COUNT(*) FROM checkins WHERE date = ?", (today_str,))
        if cursor.fetchone()[0] == 0:
            cursor.execute("""
                INSERT INTO checkins (date, status, activity_time, alert_sent, notes)
                VALUES (?, ?, ?, ?, ?)
            """, (
                today_str,
                "waiting",
                None,
                0,
                f"Waiting for morning activity (window opens at {CHECKIN_WINDOW_START})."
            ))

        # Seed Demo Events if empty
        cursor.execute("SELECT COUNT(*) FROM events")
        if cursor.fetchone()[0] == 0:
            # Seed events earlier in the night/morning
            sample_events = [
                (
                    "req_seed_01",
                    "evt_seed_01",
                    "dev_front_door_01",
                    "Front Door Video Doorbell Pro 2",
                    "button_press",
                    "doorbell",
                    (now - datetime.timedelta(hours=5)).isoformat(),
                    "/static/snapshots/placeholder_doorbell.jpg",
                    "known",
                    "Nurse Martha scheduled wellness check-in",
                    1,
                    0
                ),
                (
                    "req_seed_02",
                    "evt_seed_02",
                    "dev_hallway_cam_02",
                    "Hallway Stick Up Cam",
                    "motion_detected",
                    "human",
                    (now - datetime.timedelta(hours=6)).isoformat(),
                    "/static/snapshots/placeholder_human.jpg",
                    "known",
                    "Movement detected in hallway",
                    1,
                    0
                ),
                (
                    "req_seed_03",
                    "evt_seed_03",
                    "dev_front_door_01",
                    "Front Door Video Doorbell Pro 2",
                    "motion_detected",
                    "vehicle",
                    (now - datetime.timedelta(hours=8)).isoformat(),
                    "/static/snapshots/placeholder_motion.jpg",
                    "unknown",
                    "USPS mail van drove past driveway",
                    1,
                    0
                )
            ]
            cursor.executemany("""
                INSERT INTO events (
                    request_id, event_id, device_id, device_name, event_type,
                    sub_type, timestamp, snapshot_url, visitor_tag, visitor_notes, processed, received_offline
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, sample_events)

        conn.commit()

# --- Token Management ---

def store_unclaimed_token(account_id: Optional[str], access_token: str, refresh_token: str, expires_in: int = 14400) -> int:
    """Stores a newly exchanged OAuth token in 'unclaimed' state."""
    expires_at = datetime.datetime.now().timestamp() + expires_in
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO tokens (account_id, access_token, refresh_token, expires_at, status)
            VALUES (?, ?, ?, ?, 'unclaimed')
        """, (account_id, access_token, refresh_token, expires_at))
        conn.commit()
        return cursor.lastrowid

def get_unclaimed_tokens() -> List[Dict[str, Any]]:
    """Retrieves all tokens that have not yet been claimed by an authenticated user."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tokens WHERE status = 'unclaimed' ORDER BY id DESC")
        return [dict(row) for row in cursor.fetchall()]

def claim_token(token_id: int, account_id: str, user_email: str):
    """Marks an unclaimed token as claimed and associates it with a user account."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE tokens 
            SET status = 'claimed', account_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (account_id, token_id))
        
        cursor.execute("""
            UPDATE users SET account_id = ? WHERE email = ?
        """, (account_id, user_email))
        conn.commit()

def get_active_token(account_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Fetches the latest active (claimed) token for an account or default claimed token."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if account_id:
            cursor.execute("""
                SELECT * FROM tokens 
                WHERE account_id = ? AND status = 'claimed'
                ORDER BY id DESC LIMIT 1
            """, (account_id,))
        else:
            cursor.execute("""
                SELECT * FROM tokens 
                WHERE status = 'claimed'
                ORDER BY id DESC LIMIT 1
            """)
        row = cursor.fetchone()
        return dict(row) if row else None

def update_tokens(account_id: str, new_access_token: str, new_refresh_token: str, expires_in: int = 14400):
    """
    CRITICAL: Updates BOTH access_token and refresh_token.
    Ring refreshes return a new refresh token that invalidates the old one.
    """
    expires_at = datetime.datetime.now().timestamp() + expires_in
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE tokens
            SET access_token = ?, refresh_token = ?, expires_at = ?, updated_at = CURRENT_TIMESTAMP
            WHERE account_id = ? AND status = 'claimed'
        """, (new_access_token, new_refresh_token, expires_at, account_id))
        conn.commit()
        logger.info(f"Updated tokens for account {account_id}")

# --- Webhook & Event Operations ---

def is_duplicate_request(request_id: str) -> bool:
    """Checks if a request_id has already been processed to deduplicate incoming webhooks."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM events WHERE request_id = ? LIMIT 1", (request_id,))
        return cursor.fetchone() is not None

def insert_event(
    request_id: str,
    event_id: str,
    device_id: str,
    device_name: str,
    event_type: str,
    sub_type: Optional[str],
    timestamp: str,
    snapshot_url: Optional[str] = None,
    visitor_tag: str = "unassigned",
    visitor_notes: str = "",
    received_offline: int = 0
) -> int:
    """Inserts a verified webhook event into the database."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO events (
                request_id, event_id, device_id, device_name, event_type,
                sub_type, timestamp, snapshot_url, visitor_tag, visitor_notes, received_offline
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            request_id, event_id, device_id, device_name, event_type,
            sub_type, timestamp, snapshot_url, visitor_tag, visitor_notes, received_offline
        ))
        conn.commit()
        return cursor.lastrowid

def get_events(limit: int = 50, filter_type: Optional[str] = None, visitor_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieves recent events with optional filtering."""
    query = "SELECT * FROM events WHERE 1=1"
    params = []

    if filter_type:
        query += " AND event_type = ?"
        params.append(filter_type)
    if visitor_filter:
        query += " AND visitor_tag = ?"
        params.append(visitor_filter)

    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

def update_visitor_status(event_id: int, visitor_tag: str, visitor_notes: Optional[str] = None):
    """Updates visitor categorization (known/unknown) and caregiver notes."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if visitor_notes is not None:
            cursor.execute("""
                UPDATE events 
                SET visitor_tag = ?, visitor_notes = ?
                WHERE id = ?
            """, (visitor_tag, visitor_notes, event_id))
        else:
            cursor.execute("""
                UPDATE events 
                SET visitor_tag = ?
                WHERE id = ?
            """, (visitor_tag, event_id))
        conn.commit()

# --- Device Operations ---

def get_devices() -> List[Dict[str, Any]]:
    """Returns all registered devices."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM devices ORDER BY name ASC")
        devices = []
        for row in cursor.fetchall():
            d = dict(row)
            if d.get("capabilities") and isinstance(d["capabilities"], str):
                try:
                    d["capabilities"] = json.loads(d["capabilities"])
                except Exception:
                    d["capabilities"] = []
            devices.append(d)
        return devices

def get_device(device_id: str) -> Optional[Dict[str, Any]]:
    """Gets a specific device by ID."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM devices WHERE id = ?", (device_id,))
        row = cursor.fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("capabilities") and isinstance(d["capabilities"], str):
            try:
                d["capabilities"] = json.loads(d["capabilities"])
            except Exception:
                d["capabilities"] = []
        return d

def update_device_status(device_id: str, online: bool):
    """Updates the online/offline status of a device."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE devices 
            SET online = ?, last_seen = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (1 if online else 0, device_id))
        conn.commit()

def update_device_settings(
    device_id: str,
    quiet_hours_enabled: bool,
    quiet_hours_start: str,
    quiet_hours_end: str,
    sensitivity: str
):
    """Updates quiet hours and sensitivity thresholds for a device."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE devices
            SET quiet_hours_enabled = ?, quiet_hours_start = ?,
                quiet_hours_end = ?, sensitivity = ?
            WHERE id = ?
        """, (1 if quiet_hours_enabled else 0, quiet_hours_start, quiet_hours_end, sensitivity, device_id))
        conn.commit()

# --- Care Check-in Operations ---

def is_within_checkin_window(timestamp_str: str) -> bool:
    """Checks whether an event timestamp falls within the morning check-in window."""
    try:
        clean_str = str(timestamp_str).replace("Z", "").split(".")[0]
        dt = datetime.datetime.fromisoformat(clean_str)
        start_parts = [int(p) for p in CHECKIN_WINDOW_START.split(":")]
        target_parts = [int(p) for p in CHECKIN_TARGET.split(":")]
        start_time = datetime.time(start_parts[0], start_parts[1])
        target_time = datetime.time(target_parts[0], target_parts[1])
        return start_time <= dt.time() <= target_time
    except Exception:
        return False

def get_today_checkin_status() -> Dict[str, Any]:
    """
    Gets or evaluates today's check-in status row using get_now() and the checkin window.
    States:
    - 'waiting': before CHECKIN_WINDOW_START if no event yet
    - 'pending': between CHECKIN_WINDOW_START and CHECKIN_TARGET if no event yet
    - 'checked_in': if motion or button_press occurred between window start and target
    - 'needs_attention': if current time >= CHECKIN_TARGET and no event occurred in window
    """
    now = get_now()
    today_str = now.date().isoformat()
    start_parts = [int(p) for p in CHECKIN_WINDOW_START.split(":")]
    target_parts = [int(p) for p in CHECKIN_TARGET.split(":")]
    start_time = datetime.time(start_parts[0], start_parts[1])
    target_time = datetime.time(target_parts[0], target_parts[1])

    window_start_dt = datetime.datetime.combine(now.date(), start_time)
    window_target_dt = datetime.datetime.combine(now.date(), target_time)

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Check existing row
        cursor.execute("SELECT * FROM checkins WHERE date = ?", (today_str,))
        existing_checkin = cursor.fetchone()

        # Check for any valid morning event inside the window
        cursor.execute("""
            SELECT * FROM events
            WHERE timestamp >= ? AND timestamp <= ? AND event_type IN ('motion_detected', 'button_press')
            ORDER BY timestamp ASC LIMIT 1
        """, (window_start_dt.isoformat(), window_target_dt.isoformat()))
        first_event = cursor.fetchone()

        if existing_checkin and existing_checkin["status"] == "checked_in" and existing_checkin["activity_time"]:
            status = "checked_in"
            activity_time = existing_checkin["activity_time"]
            formatted_time = format_timestamp(activity_time)
            notes = existing_checkin["notes"] or f"Morning movement verified at {formatted_time}."
            alert_sent = 0
        elif first_event:
            status = "checked_in"
            activity_time = first_event["timestamp"]
            formatted_time = format_timestamp(activity_time)
            notes = f"Morning movement verified at {formatted_time}."
            alert_sent = 0
        else:
            activity_time = None
            if now < window_start_dt:
                status = "waiting"
                notes = f"Waiting for morning activity (window opens at {CHECKIN_WINDOW_START} AM)."
                alert_sent = 0
            elif now < window_target_dt:
                status = "pending"
                notes = f"Monitoring morning activity before {CHECKIN_TARGET} AM deadline."
                alert_sent = 0
            else:
                status = "needs_attention"
                notes = f"⚠️ ALERT: No motion or door activity detected before {CHECKIN_TARGET} AM. Caregiver alert dispatched."
                alert_sent = 1

        cursor.execute("""
            INSERT INTO checkins (date, status, activity_time, alert_sent, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(date) DO UPDATE SET
                status = excluded.status,
                activity_time = excluded.activity_time,
                alert_sent = excluded.alert_sent,
                notes = excluded.notes,
                updated_at = CURRENT_TIMESTAMP
        """, (today_str, status, activity_time, alert_sent, notes))
        conn.commit()

        cursor.execute("SELECT * FROM checkins WHERE date = ?", (today_str,))
        res = dict(cursor.fetchone())
        res["message"] = res.get("notes", "")
        return res

def record_checkin_activity(timestamp_str: str) -> bool:
    """
    Marks today as checked in upon detecting morning movement,
    ONLY IF the movement is inside [CHECKIN_WINDOW_START, CHECKIN_TARGET].
    """
    if not is_within_checkin_window(timestamp_str):
        logger.info(f"Event at {timestamp_str} is outside checkin window ({CHECKIN_WINDOW_START}-{CHECKIN_TARGET}). Not counted as morning check-in.")
        return False

    now = get_now()
    today_str = now.date().isoformat()
    formatted_time = format_timestamp(timestamp_str)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO checkins (date, status, activity_time, alert_sent, notes, updated_at)
            VALUES (?, 'checked_in', ?, 0, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(date) DO UPDATE SET
                status = 'checked_in',
                activity_time = excluded.activity_time,
                notes = excluded.notes,
                updated_at = CURRENT_TIMESTAMP
        """, (today_str, timestamp_str, f"Morning movement verified at {formatted_time}."))
        conn.commit()
    return True

def set_checkin_status(status: str, notes: str, alert_sent: bool = False):
    """Sets check-in status explicitly (e.g. 'needs_attention' if threshold passed without motion)."""
    now = get_now()
    today_str = now.date().isoformat()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO checkins (date, status, activity_time, alert_sent, notes, updated_at)
            VALUES (?, ?, NULL, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(date) DO UPDATE SET
                status = excluded.status,
                alert_sent = excluded.alert_sent,
                notes = excluded.notes,
                updated_at = CURRENT_TIMESTAMP
        """, (today_str, status, 1 if alert_sent else 0, notes))
        conn.commit()

# --- Privacy Data Purge ---

def delete_all_user_data() -> Dict[str, int]:
    """Deletes all personal events, snapshots, and checkin logs (GDPR & privacy compliance)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM events")
        events_deleted = cursor.rowcount
        cursor.execute("DELETE FROM checkins")
        checkins_deleted = cursor.rowcount
        conn.commit()
        return {"events_deleted": events_deleted, "checkins_deleted": checkins_deleted}
