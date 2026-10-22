"""
Webhook handling, HMAC-SHA256 verification, deduplication, and asynchronous event processing.

Hard Requirements from Ring Partner Docs:
1. Verify X-Signature = "sha256=" + hex HMAC-SHA256(raw_bytes, RING_HMAC_KEY) using constant-time compare.
2. Deduplicate on meta.request_id (instant idempotent 200 response).
3. Return 200 OK within 5 seconds. Processing is handed to an async worker thread.
4. Support events: motion_detected, button_press, device_added, device_removed,
   device_online, device_offline, app_integration_added, app_integration_removed.
"""

import hmac
import hashlib
import json
import logging
import queue
import threading
import datetime
from typing import Dict, Any, Optional, Tuple, List

from config import RING_HMAC_KEY, DEMO_MODE, get_now, format_timestamp
import database
import snapshot_service
from ring_client import RingClient

logger = logging.getLogger("ringcare.webhook")

# Asynchronous processing queue & SSE broadcaster
webhook_queue: queue.Queue = queue.Queue(maxsize=1000)
sse_subscribers: List[queue.Queue] = []
sse_lock = threading.Lock()

def verify_webhook_signature(raw_body: bytes, signature_header: Optional[str], hmac_key: Optional[str] = None) -> bool:
    """
    Validates X-Signature header using HMAC-SHA256 constant-time comparison.
    Format of header: 'sha256=<hex_digest>'
    """
    if not signature_header:
        logger.warning("Missing X-Signature header in webhook request.")
        return False

    key = hmac_key or RING_HMAC_KEY
    if not key:
        logger.error("RING_HMAC_KEY is not configured.")
        return False

    expected_signature = "sha256=" + hmac.new(
        key.encode("utf-8"),
        raw_body,
        hashlib.sha256
    ).hexdigest()

    is_valid = hmac.compare_digest(expected_signature, signature_header)
    if not is_valid:
        logger.warning(f"HMAC mismatch: received '{signature_header}', expected '{expected_signature}'")
    return is_valid

def subscribe_sse() -> queue.Queue:
    """Registers an SSE client connection queue."""
    q = queue.Queue(maxsize=50)
    with sse_lock:
        sse_subscribers.append(q)
    return q

def unsubscribe_sse(q: queue.Queue):
    """Unregisters an SSE client connection queue."""
    with sse_lock:
        if q in sse_subscribers:
            sse_subscribers.remove(q)

def broadcast_event(event_dict: Dict[str, Any]):
    """Pushes an event to all connected dashboard SSE streams."""
    with sse_lock:
        dead_queues = []
        for q in sse_subscribers:
            try:
                q.put_nowait(event_dict)
            except queue.Full:
                dead_queues.append(q)
        for dq in dead_queues:
            if dq in sse_subscribers:
                sse_subscribers.remove(dq)

def is_within_quiet_hours(start_str: str, end_str: str, current_time: Optional[datetime.time] = None) -> bool:
    """Checks whether the current time falls within device quiet hours."""
    if not start_str or not end_str:
        return False
    try:
        now_time = current_time or datetime.datetime.now().time()
        start = datetime.datetime.strptime(start_str, "%H:%M").time()
        end = datetime.datetime.strptime(end_str, "%H:%M").time()

        if start <= end:
            return start <= now_time <= end
        else:
            # Spans midnight (e.g. 22:00 to 07:00)
            return now_time >= start or now_time <= end
    except Exception as e:
        logger.error(f"Error parsing quiet hours: {e}")
        return False

def process_webhook_payload(payload: Dict[str, Any]):
    """
    Asynchronous event processor for Ring Webhook payloads.
    Parses JSON:API formatted event payloads.
    """
    try:
        now = get_now()
        meta = payload.get("meta", {})
        request_id = meta.get("request_id") or f"req_{int(now.timestamp() * 1000)}"

        data = payload.get("data", {})
        event_type = data.get("type") or data.get("attributes", {}).get("event_type", "unknown")
        attributes = data.get("attributes", {})
        relationships = data.get("relationships", {})

        # Extract device ID
        device_id = None
        device_data = relationships.get("device", {}).get("data")
        if isinstance(device_data, dict):
            device_id = device_data.get("id")
        elif "device_id" in attributes:
            device_id = attributes["device_id"]
        else:
            device_id = "dev_front_door_01"

        sub_type = attributes.get("sub_type") # e.g. "human", "vehicle", "pet", "all"
        timestamp = attributes.get("created_at") or now.isoformat()

        # Lookup device in DB
        device = database.get_device(device_id)
        device_name = device["name"] if device else "Ring Camera"
        was_offline = (device is not None and not bool(device.get("online", 1)))

        logger.info(f"Processing event: {event_type} (sub_type={sub_type}) on device {device_name} ({device_id}) [was_offline={was_offline}]")

        # Handle device status change events
        if event_type == "device_online":
            database.update_device_status(device_id, True)
            broadcast_event({
                "type": "device_status",
                "device_id": device_id,
                "online": True,
                "message": f"{device_name} is now online."
            })
            return

        if event_type == "device_offline":
            database.update_device_status(device_id, False)
            broadcast_event({
                "type": "device_status",
                "device_id": device_id,
                "online": False,
                "message": f"Alert: {device_name} went offline!"
            })
            return

        # Check quiet hours & sensitivity
        quiet_suppressed = False
        if device and device.get("quiet_hours_enabled"):
            if is_within_quiet_hours(device.get("quiet_hours_start", "22:00"), device.get("quiet_hours_end", "07:00"), current_time=now.time()):
                # During quiet hours, suppress non-human pet/vehicle motion alerts
                if event_type == "motion_detected" and sub_type in ("pet", "vehicle", "all"):
                    quiet_suppressed = True
                    logger.info(f"Quiet hours active: Non-critical {sub_type} motion muted.")

        # Offline Device Logic & Snapshot Resolution
        # If device was offline: do not claim snapshot was captured (snapshot fetch would fail in real life)
        if was_offline:
            received_offline = 1
            snapshot_url = None
            logger.warning(f"Device {device_name} is offline. Event recorded without snapshot capture.")
        else:
            received_offline = 0
            snapshot_url = None
            if event_type in ("button_press", "motion_detected"):
                try:
                    snapshot_url = snapshot_service.create_event_snapshot(
                        device_name=device_name,
                        timestamp_str=timestamp,
                        event_type=event_type,
                        sub_type=sub_type,
                        event_id=request_id
                    )
                except Exception as snap_err:
                    logger.error(f"Failed to generate event snapshot: {snap_err}")
                    snapshot_url = "/static/snapshots/default_snapshot.jpg"

        # Determine accessibility alert requirements
        is_accessible_alert = (not quiet_suppressed) and (
            event_type == "button_press" or (event_type == "motion_detected" and sub_type == "human")
        )

        visitor_tag = "unassigned" if event_type == "button_press" else "known" if sub_type == "human" else "unassigned"

        # Save to database
        event_db_id = database.insert_event(
            request_id=request_id,
            event_id=data.get("id", f"evt_{request_id}"),
            device_id=device_id,
            device_name=device_name,
            event_type=event_type,
            sub_type=sub_type,
            timestamp=timestamp,
            snapshot_url=snapshot_url,
            visitor_tag=visitor_tag,
            visitor_notes="",
            received_offline=received_offline
        )

        # In demo mode: when a device event arrives, set that device status to online,
        # because a device that sends events is online
        if DEMO_MODE and event_type in ("button_press", "motion_detected") and was_offline:
            database.update_device_status(device_id, True)
            broadcast_event({
                "type": "device_status",
                "device_id": device_id,
                "online": True,
                "message": f"{device_name} is now online (activity detected)."
            })

        # Update care check-in activity if motion or doorbell press is within morning window
        if event_type in ("button_press", "motion_detected"):
            checked_in = database.record_checkin_activity(timestamp)
            if checked_in:
                formatted_act = format_timestamp(timestamp)
                broadcast_event({
                    "type": "checkin_update",
                    "status": "checked_in",
                    "message": f"Morning movement verified at {formatted_act} on {device_name}.",
                    "activity_time": timestamp,
                    "formatted_time": formatted_act
                })

        # Build broadcast message
        alert_text = ""
        speech_text = ""
        if event_type == "button_press":
            alert_text = f"DOORBELL PRESSED at {device_name}"
            speech_text = f"Accessibility Alert: Someone is at the door. Doorbell pressed at {device_name}."
        elif event_type == "motion_detected" and sub_type == "human":
            alert_text = f"HUMAN MOTION DETECTED at {device_name}"
            speech_text = f"Accessibility Alert: Person detected at {device_name}."
        else:
            alert_text = f"{event_type.replace('_', ' ').title()} at {device_name}"
            speech_text = f"Activity detected: {alert_text}."

        if was_offline:
            alert_text = f"⚠️ {alert_text} (Device was offline)"
            speech_text = f"Warning: Event received while device was offline. {speech_text}"

        broadcast_payload = {
            "type": "event",
            "event_id": event_db_id,
            "request_id": request_id,
            "device_id": device_id,
            "device_name": device_name,
            "event_type": event_type,
            "sub_type": sub_type,
            "timestamp": timestamp,
            "formatted_time": format_timestamp(timestamp),
            "snapshot_url": snapshot_url,
            "visitor_tag": visitor_tag,
            "is_accessible_alert": is_accessible_alert,
            "alert_banner_text": alert_text,
            "speech_text": speech_text,
            "quiet_suppressed": quiet_suppressed,
            "received_offline": bool(received_offline)
        }

        broadcast_event(broadcast_payload)
        logger.info(f"Successfully processed and broadcasted event {event_db_id}")

    except Exception as e:
        logger.exception(f"Error processing webhook payload: {e}")

def _worker_loop():
    """Background queue consumer loop."""
    while True:
        try:
            payload = webhook_queue.get()
            if payload is None:
                break
            process_webhook_payload(payload)
            webhook_queue.task_done()
        except Exception as e:
            logger.error(f"Worker queue error: {e}")

# Start background async processing thread
worker_thread = threading.Thread(target=_worker_loop, daemon=True, name="RingCare-WebhookWorker")
worker_thread.start()
