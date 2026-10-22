"""
Unit tests for RingCare bug fixes:
1. Snapshot generation with Pillow (320x180 JPEG placeholder images).
2. Check-in window logic (06:00 to 10:00 AM), 01:07 AM exclusion, and get_now() overrides.
3. Offline device event handling (received_offline flag, no snapshot claim, demo mode online recovery).
4. Unified timestamp formatting helper.
"""

import os
from pathlib import Path
from datetime import datetime
from PIL import Image
import pytest

import config
import database
import snapshot_service
import webhook_handler
import checkin_service
from app import app


@pytest.fixture(autouse=True)
def setup_teardown():
    """Sets up a clean test database and resets simulated clock."""
    config.set_simulated_now(None)
    database.init_db()
    database.delete_all_user_data()
    # Ensure default devices exist
    database.seed_initial_data()
    yield
    config.set_simulated_now(None)


def test_unified_timestamp_formatting():
    """Tests unified YYYY-MM-DD hh:mm:ss AM/PM formatting."""
    # Morning ISO string
    ts_am = "2026-10-22T08:15:00"
    formatted_am = config.format_timestamp(ts_am)
    assert formatted_am == "2026-10-22 08:15:00 AM"

    # Afternoon ISO string
    ts_pm = "2026-10-22T14:30:45"
    formatted_pm = config.format_timestamp(ts_pm)
    assert formatted_pm == "2026-10-22 02:30:45 PM"

    # Python datetime object
    dt = datetime(2026, 10, 22, 1, 7, 0)
    assert config.format_timestamp(dt) == "2026-10-22 01:07:00 AM"


def test_get_now_and_simulated_clock_override():
    """Tests get_now() respecting set_simulated_now and DEMO_NOW."""
    # Normal behavior (returns current real time)
    now_real = config.get_now()
    assert isinstance(now_real, datetime)

    # Simulated override
    sim_target = "2026-10-22T08:15:00"
    config.set_simulated_now(sim_target)
    assert config.get_now() == datetime(2026, 10, 22, 8, 15, 0)

    # Clear override
    config.set_simulated_now(None)
    assert (datetime.now() - config.get_now()).total_seconds() < 2


def test_checkin_window_rejection_at_0107_am():
    """
    BUG 2 TEST:
    Activity at 01:07 AM must NOT trigger check-in because it falls outside
    the 06:00 AM - 10:00 AM morning check-in window.
    """
    # Simulate time at 01:07 AM
    config.set_simulated_now("2026-10-22T01:07:00")

    # Record activity at 01:07 AM
    recorded = database.record_checkin_activity("2026-10-22T01:07:00")
    assert recorded is False, "Activity at 01:07 AM should not count as morning check-in!"

    # Status before 06:00 AM should be 'waiting'
    status = database.get_today_checkin_status()
    assert status["status"] == "waiting"
    assert "window opens at" in status["message"].lower() or "waiting for morning activity" in status["message"].lower()


def test_checkin_window_accepted_at_0815_am():
    """
    BUG 2 TEST:
    Activity at 08:15 AM must successfully verify check-in because it falls
    inside the 06:00 AM - 10:00 AM morning window.
    """
    # Simulate time at 08:15 AM
    config.set_simulated_now("2026-10-22T08:15:00")

    # Record activity at 08:15 AM
    recorded = database.record_checkin_activity("2026-10-22T08:15:00")
    assert recorded is True, "Activity at 08:15 AM should count as valid morning check-in!"

    # Status should be 'checked_in'
    status = database.get_today_checkin_status()
    assert status["status"] == "checked_in"
    assert status["activity_time"] == "2026-10-22T08:15:00"
    assert "Morning movement verified" in status["message"]


def test_checkin_needs_attention_after_target_time():
    """
    BUG 2 TEST:
    After 10:00 AM target time without movement, status must be 'needs_attention'.
    """
    # Simulate time at 10:30 AM with no morning activity recorded
    config.set_simulated_now("2026-10-22T10:30:00")

    status = database.get_today_checkin_status()
    assert status["status"] == "needs_attention"
    assert "No motion or door activity detected before" in status["message"]

    # evaluate_daily_checkin should also return needs_attention
    eval_res = checkin_service.evaluate_daily_checkin(is_manual=True)
    assert eval_res["status"] == "needs_attention"


def test_offline_device_event_handling_and_recovery():
    """
    BUG 3 TEST:
    When a device is offline:
    1. Event is saved with received_offline = 1.
    2. snapshot_url is None (do not claim snapshot was captured).
    3. In demo mode, activity on offline device transitions it back to online.
    """
    device_id = "dev_front_door_01"

    # Manually mark device offline
    database.update_device_status(device_id, False)
    dev = database.get_device(device_id)
    assert dev["online"] == 0

    # Simulate an incoming motion event payload while offline
    req_id = "req_test_offline_001"
    payload = {
        "meta": {"request_id": req_id},
        "data": {
            "id": "evt_test_offline_001",
            "type": "button_press",
            "attributes": {
                "event_type": "button_press",
                "created_at": "2026-10-22T08:30:00",
                "device_id": device_id
            },
            "relationships": {
                "device": {"data": {"type": "devices", "id": device_id}}
            }
        }
    }

    # Process payload directly
    webhook_handler.process_webhook_payload(payload)

    # Check event in SQLite
    events = database.get_events(limit=1)
    assert len(events) == 1
    ev = events[0]

    assert ev["received_offline"] == 1
    assert ev["snapshot_url"] is None

    # In demo mode, device should transition to online after activity
    dev_after = database.get_device(device_id)
    assert dev_after["online"] == 1


def test_snapshot_generator_pillow():
    """
    BUG 1 TEST:
    Pillow generates valid 320x180 JPEG images with camera name and timestamp.
    """
    event_id = "unit_test_snap_99"
    device_name = "Front Doorbell Camera"
    timestamp = "2026-10-22T08:15:00"

    url = snapshot_service.create_event_snapshot(
        device_name=device_name,
        timestamp_str=timestamp,
        event_type="button_press",
        sub_type="doorbell",
        event_id=event_id
    )

    assert url.startswith("/static/snapshots/")
    filename = url.replace("/static/snapshots/", "")
    file_path = config.SNAPSHOTS_DIR / filename

    assert file_path.exists(), f"Snapshot file {file_path} was not created!"

    with Image.open(file_path) as img:
        assert img.size == (320, 180)
        assert img.format == "JPEG"


def test_api_demo_time_override_endpoint():
    """Tests the /api/demo/time endpoint for simulated time overrides."""
    with app.test_client() as client:
        # Override to 04:30 AM (before morning window)
        res = client.post("/api/demo/time", json={"simulated_now": "2026-10-22T04:30:00"})
        assert res.status_code == 200
        data = res.get_json()
        assert data["status"] == "success"
        assert "04:30:00 AM" in data["effective_time"]
        assert data["checkin"]["status"] == "waiting"

        # Override to 08:15 AM
        res2 = client.post("/api/demo/time", json={"simulated_now": "2026-10-22T08:15:00"})
        assert res2.status_code == 200
        data2 = res2.get_json()
        assert "08:15:00 AM" in data2["effective_time"]

        # Reset to live
        res3 = client.post("/api/demo/time", json={"simulated_now": ""})
        assert res3.status_code == 200
        data3 = res3.get_json()
        assert data3["status"] == "success"
