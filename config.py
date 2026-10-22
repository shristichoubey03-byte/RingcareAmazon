"""
Configuration settings for RingCare.
Loads environment variables from .env file or system environment.
"""

import os
from datetime import datetime, date
from pathlib import Path
from dotenv import load_dotenv

# Base directory of the project
BASE_DIR = Path(__file__).resolve().parent

# Load .env file if it exists
load_dotenv(BASE_DIR / ".env")

# Ring Partner API & OAuth Credentials
RING_CLIENT_ID = os.getenv("RING_CLIENT_ID", "")
RING_CLIENT_SECRET = os.getenv("RING_CLIENT_SECRET", "")
# Default HMAC key for demo/local testing if not provided in env
RING_HMAC_KEY = os.getenv("RING_HMAC_KEY", "ringcare_default_hmac_secret_key_demo_2026")

# API Base URLs
RING_BASE_URL = os.getenv("RING_BASE_URL", "https://api.amazonvision.com").rstrip("/")
RING_OAUTH_URL = os.getenv("RING_OAUTH_URL", "https://oauth.ring.com").rstrip("/")

# Operational Mode
# If credentials are empty or DEMO_MODE is explicitly true, default to True
_demo_env = os.getenv("DEMO_MODE", "").lower()
if _demo_env in ("true", "1", "yes"):
    DEMO_MODE = True
elif _demo_env in ("false", "0", "no"):
    DEMO_MODE = False
else:
    # Auto-detect: if no client ID is provided, enable demo mode
    DEMO_MODE = not bool(RING_CLIENT_ID and RING_CLIENT_SECRET)

# Server & Database Settings
PORT = int(os.getenv("PORT", "5000"))
HOST = os.getenv("HOST", "0.0.0.0")
SECRET_KEY = os.getenv("SECRET_KEY", "ringcare-secret-key-caregiver-portal-2026")
DATABASE_PATH = os.getenv("DATABASE_PATH", str(BASE_DIR / "ringcare.db"))

# Caregiver check-in time configuration (HH:MM in 24h format)
CHECKIN_WINDOW_START = os.getenv("CHECKIN_WINDOW_START", "06:00")
CHECKIN_TARGET = os.getenv("CHECKIN_TARGET", os.getenv("CHECKIN_TARGET_TIME", "10:00"))
CHECKIN_TARGET_TIME = CHECKIN_TARGET
CAREGIVER_NOTIFICATION_EMAIL = os.getenv("CAREGIVER_NOTIFICATION_EMAIL", "caregiver@family.org")

# Demo Time Override
DEMO_NOW = os.getenv("DEMO_NOW", "")
_simulated_now = None

def get_now() -> datetime:
    """Returns the effective current time, respecting simulated time overrides."""
    global _simulated_now
    if _simulated_now is not None:
        return _simulated_now
    if DEMO_NOW:
        try:
            return datetime.fromisoformat(DEMO_NOW.replace("Z", ""))
        except Exception:
            pass
    return datetime.now()

def set_simulated_now(dt_or_str):
    """Sets or clears the simulated demo clock."""
    global _simulated_now
    if not dt_or_str:
        _simulated_now = None
    elif isinstance(dt_or_str, datetime):
        _simulated_now = dt_or_str
    else:
        try:
            _simulated_now = datetime.fromisoformat(str(dt_or_str).replace("Z", ""))
        except Exception:
            _simulated_now = None
    return get_now()

def format_timestamp(dt_or_str) -> str:
    """Single unified time formatting helper across the entire application."""
    if not dt_or_str:
        return ""
    if isinstance(dt_or_str, datetime):
        dt = dt_or_str
    elif isinstance(dt_or_str, date):
        return dt_or_str.isoformat()
    else:
        try:
            clean_str = str(dt_or_str).replace("Z", "").split(".")[0]
            dt = datetime.fromisoformat(clean_str)
        except Exception:
            return str(dt_or_str)
    return dt.strftime("%Y-%m-%d %I:%M:%S %p")

# Snapshots storage directory
SNAPSHOTS_DIR = BASE_DIR / "static" / "snapshots"

# Rate limit configuration (Ring API is 100 req/sec)
RING_RATE_LIMIT_RPS = 100
DEFAULT_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 16.0
