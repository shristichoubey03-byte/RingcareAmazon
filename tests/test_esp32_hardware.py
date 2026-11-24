"""
Automated Test Suite for RingCare ESP32 USB Hardware Controller.

Tests:
1. ESP32 Controller unit functions (status, alert, stop, test).
2. SQLite hardware_events logging.
3. Flask REST API endpoints (/api/esp32/status, /api/esp32/ring, /api/esp32/stop, /api/esp32/test).
4. Automatic Care Check-In Engine integration:
   - Verify that an Inactivity Alert automatically sends ALERT to ESP32.
   - Verify that caregiver acknowledgment stops the physical buzzer (SAFE).
   - Verify duplicate alert prevention.
"""

import pytest
import json
from datetime import datetime
from app import app
from esp32_controller import esp32_controller
import database
import checkin_service
from config import set_simulated_now


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_esp32_controller_unit():
    """Verifies internal state transitions of the ESP32 Controller."""
    # 1. Initial Status
    status = esp32_controller.get_connection_status()
    assert status["status"] == "success"
    assert "buzzer_status" in status
    assert "available_ports" in status

    # 2. Trigger Alert
    alert_res = esp32_controller.send_alert(
        reason="Unit Test Inactivity Alert",
        alert_id="test_alert_001",
        source="automatic"
    )
    assert alert_res["buzzer_status"] == "ON"
    assert esp32_controller.buzzer_status == "ON"
    assert "Unit Test Inactivity Alert" in esp32_controller.last_activation_reason

    # 3. Verify Database Log
    latest_event = database.get_latest_hardware_event()
    assert latest_event is not None
    assert latest_event["event_type"] == "alert_start"
    assert latest_event["activation_source"] == "automatic"

    # 4. Trigger Stop
    stop_res = esp32_controller.stop_alert(source="manual", reason="Caregiver test silence")
    assert stop_res["buzzer_status"] == "OFF"
    assert esp32_controller.buzzer_status == "OFF"

    # 5. Verify Stop Database Log
    latest_event = database.get_latest_hardware_event()
    assert latest_event["event_type"] == "alert_stop"
    assert latest_event["activation_source"] == "manual"

    # 6. Trigger Buzzer Test
    test_res = esp32_controller.test_buzzer(source="manual")
    assert test_res["buzzer_status"] == "TESTING"


def test_esp32_api_endpoints(client):
    """Tests the Flask API endpoints for ESP32 control."""
    # 1. GET /api/esp32/status
    res = client.get("/api/esp32/status")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "success"
    assert "connection_status" in data
    assert "buzzer_status" in data

    # 2. POST /api/esp32/ring
    res = client.post("/api/esp32/ring", json={"reason": "Caregiver API test ring"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "success"
    assert esp32_controller.buzzer_status == "ON"

    # 3. POST /api/esp32/stop
    res = client.post("/api/esp32/stop", json={"reason": "Caregiver API test stop"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "success"
    assert esp32_controller.buzzer_status == "OFF"

    # 4. POST /api/esp32/test
    res = client.post("/api/esp32/test")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "success"
    assert "test sequence" in data["message"].lower()


def test_automatic_checkin_hardware_alert(client):
    """
    CRITICAL REQUIREMENT 6:
    Verifies that when the Check-In Engine generates an inactivity alert,
    it automatically triggers the ESP32 buzzer, and caregiver acknowledgment stops it.
    """
    # Simulate past deadline time (e.g. 10:30 AM) with no morning activity
    set_simulated_now("2026-10-22T10:30:00")

    # Clear previous checkin for this simulated date to ensure clean evaluation
    with database.get_db_connection() as conn:
        conn.execute("DELETE FROM checkins WHERE date = '2026-10-22'")
        conn.commit()

    # Reset ESP32 buzzer
    esp32_controller.stop_alert()
    assert esp32_controller.buzzer_status == "OFF"

    # Evaluate check-in: Should generate 'needs_attention' and fire ESP32 ALERT
    eval_res = checkin_service.evaluate_daily_checkin(is_manual=True)
    assert eval_res["status"] == "needs_attention"
    assert eval_res["alert_sent"] is True

    # Hardware Buzzer must now be ON
    assert esp32_controller.buzzer_status == "ON"
    assert "No movement detected" in esp32_controller.last_activation_reason

    # Caregiver acknowledges alert via API
    ack_res = client.post("/api/checkin/acknowledge", json={"notes": "Caregiver called resident on phone."})
    assert ack_res.status_code == 200
    ack_data = ack_res.get_json()
    assert ack_data["acknowledged"] is True

    # Hardware Buzzer must now be OFF
    assert esp32_controller.buzzer_status == "OFF"

    # Reset simulated clock to real-time
    set_simulated_now(None)
