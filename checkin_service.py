"""
Care Check-in Service & Background Scheduler.

Features:
1. Care check-in monitor: Evaluates daily morning motion/doorbell activity.
   If no activity is detected by the configured time (e.g. 10:00 AM),
   triggers high-priority Caregiver Alerts and updates check-in status to 'Needs Attention'.
2. Ring OAuth Token 24h maintenance job (per hard constraints).
3. Immediate manual test trigger for demo/hackathon evaluations.
"""

import datetime
import logging
from typing import Dict, Any
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from config import (
    CHECKIN_TARGET_TIME,
    CHECKIN_WINDOW_START,
    CHECKIN_TARGET,
    CAREGIVER_NOTIFICATION_EMAIL,
    get_now,
    format_timestamp
)
import database
from webhook_handler import broadcast_event
from ring_client import RingClient

logger = logging.getLogger("ringcare.checkin")

scheduler = BackgroundScheduler(daemon=True)

def evaluate_daily_checkin(is_manual: bool = False) -> Dict[str, Any]:
    """
    Evaluates whether morning movement has been detected today within the configured
    window (CHECKIN_WINDOW_START to CHECKIN_TARGET).
    Respects get_now() simulated clock overrides.
    """
    now = get_now()
    logger.info(f"Executing care check-in evaluation at effective time: {format_timestamp(now)}")
    status_row = database.get_today_checkin_status()
    status = status_row.get("status", "waiting")
    activity_time = status_row.get("activity_time")

    if status == "checked_in":
        formatted_act_time = format_timestamp(activity_time)
        msg = f"Morning movement verified at {formatted_act_time}."
        res = {
            "status": "checked_in",
            "message": msg,
            "activity_time": activity_time,
            "formatted_time": formatted_act_time
        }
        broadcast_event({
            "type": "checkin_update",
            "status": "checked_in",
            "message": msg,
            "activity_time": activity_time,
            "formatted_time": formatted_act_time
        })
        return res

    elif status == "needs_attention":
        msg = f"CARE CHECK-IN ALERT: No movement detected between {CHECKIN_WINDOW_START} and {CHECKIN_TARGET} AM."
        logger.warning(f"CAREGIVER ALERT: {msg}")
        res = {
            "status": "needs_attention",
            "message": msg,
            "alert_sent": True
        }
        broadcast_event({
            "type": "checkin_alert",
            "status": "needs_attention",
            "message": msg,
            "target_time": CHECKIN_TARGET,
            "is_critical": True
        })
        return res

    elif status == "waiting":
        msg = f"Waiting for morning activity (window opens at {CHECKIN_WINDOW_START} AM)."
        res = {
            "status": "waiting",
            "message": msg
        }
        broadcast_event({
            "type": "checkin_update",
            "status": "waiting",
            "message": msg
        })
        return res

    else: # pending
        msg = f"Monitoring morning activity before {CHECKIN_TARGET} AM deadline."
        res = {
            "status": "pending",
            "message": msg
        }
        broadcast_event({
            "type": "checkin_update",
            "status": "pending",
            "message": msg
        })
        return res

def refresh_tokens_routine():
    """Background maintenance job to refresh Ring Partner API tokens every 24 hours."""
    logger.info("Executing scheduled 24-hour Ring OAuth token refresh...")
    try:
        client = RingClient()
        token_info = client._get_active_token()
        if token_info:
            client.refresh_access_token()
            logger.info("Scheduled token refresh completed successfully.")
        else:
            logger.info("No claimed Ring tokens present to refresh.")
    except Exception as e:
        logger.error(f"Error during scheduled token refresh: {e}")

def start_scheduler():
    """Configures and boots APScheduler background jobs."""
    try:
        parts = CHECKIN_TARGET_TIME.split(":")
        hour = int(parts[0]) if len(parts) > 0 else 10
        minute = int(parts[1]) if len(parts) > 1 else 0
    except Exception:
        hour, minute = 10, 0

    # 1. Daily morning care check-in evaluation
    scheduler.add_job(
        evaluate_daily_checkin,
        trigger=CronTrigger(hour=hour, minute=minute),
        id="care_daily_checkin",
        name="Daily Elderly Care Activity Check-in",
        replace_existing=True
    )

    # 2. Daily token refresh (every 24 hours at 03:00 AM)
    scheduler.add_job(
        refresh_tokens_routine,
        trigger=CronTrigger(hour=3, minute=0),
        id="ring_token_refresh",
        name="24h Proactive Ring OAuth Token Refresh",
        replace_existing=True
    )

    scheduler.start()
    logger.info(f"APScheduler initialized: Care Check-in set for {hour:02d}:{minute:02d} daily.")
