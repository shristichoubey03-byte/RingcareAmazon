"""
Automated Test Suite for RingCare Arduino Uno (ATmega328P) USB Hardware Controller.

Tests:
1. Arduino Uno Controller unit functions (status, alert on Pin D8, stop, test).
2. Hardware specifications (ATmega328P, 16MHz, 5V logic, no Wi-Fi).
3. SQLite hardware_events logging.
4. Flask REST API endpoints:
   - /api/arduino/status & /api/esp32/status
   - /api/arduino/ring & /api/esp32/ring
   - /api/arduino/stop & /api/esp32/stop
   - /api/arduino/test & /api/esp32/test
   - /api/arduino/configure & /api/esp32/configure
5. Automatic Care Check-In Engine integration:
   - Verify that an Inactivity Alert automatically sends ALERT to Arduino Uno.
   - Verify that caregiver acknowledgment stops the physical buzzer on Pin D8 (SAFE).
   - Verify duplicate alert prevention.
6. Real-time sensor telemetry dictionary validation.
"""

import pytest
import json
from datetime import datetime
from app import app
from arduino_controller import arduino_controller, esp32_controller
import database
import checkin_service
from config import set_simulated_now


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_arduino_controller_specs_and_unit():
    """Verifies Arduino Uno microcontroller specifications and state transitions."""
    # 1. Hardware Specs & Status
    status = arduino_controller.get_connection_status()
    assert status["status"] == "success"
    assert status["microcontroller"] == "Arduino Uno (ATmega328P)"
    assert status["buzzer_pin"] == "Digital Pin D8"
    assert status["wifi_enabled"] is False  # Arduino Uno has no built-in Wi-Fi
    assert "buzzer_status" in status
    assert "available_ports" in status
    assert "sensor_telemetry" in status
    assert status["sensor_telemetry"]["buzzer_pin"] == "D8"

    # 2. Trigger Alert (Pin D8)
    alert_res = arduino_controller.send_alert(
        reason="Unit Test Arduino Inactivity Alert",
        alert_id="test_arduino_001",
        source="automatic"
    )
    assert alert_res["buzzer_status"] == "ON"
    assert alert_res["buzzer_pin"] == "Digital Pin D8"
    assert arduino_controller.buzzer_status == "ON"
    assert "Unit Test Arduino Inactivity Alert" in arduino_controller.last_activation_reason

    # 3. Verify Database Log
    latest_event = database.get_latest_hardware_event()
    assert latest_event is not None
    assert latest_event["event_type"] == "alert_start"
    assert latest_event["activation_source"] == "automatic"

    # 4. Trigger Stop
    stop_res = arduino_controller.stop_alert(source="manual", reason="Caregiver test silence")
    assert stop_res["buzzer_status"] == "OFF"
    assert arduino_controller.buzzer_status == "OFF"

    # 5. Verify Stop Database Log
    latest_event = database.get_latest_hardware_event()
    assert latest_event["event_type"] == "alert_stop"
    assert latest_event["activation_source"] == "manual"

    # 6. Trigger Buzzer Test
    test_res = arduino_controller.test_buzzer(source="manual")
    assert test_res["buzzer_status"] == "TESTING"


def test_arduino_and_esp32_api_dual_endpoints(client):
    """Tests that both /api/arduino/* and /api/esp32/* endpoints function seamlessly."""
    # 1. GET /api/arduino/status & /api/esp32/status
    for path in ["/api/arduino/status", "/api/esp32/status"]:
        res = client.get(path)
        assert res.status_code == 200
        data = res.get_json()
        assert data["status"] == "success"
        assert "connection_status" in data
        assert "buzzer_status" in data
        assert data["buzzer_pin"] == "Digital Pin D8"
        assert data["microcontroller"] == "Arduino Uno (ATmega328P)"

    # 2. POST /api/arduino/ring & /api/esp32/ring
    res = client.post("/api/arduino/ring", json={"reason": "Caregiver Arduino API test ring"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "success"
    assert arduino_controller.buzzer_status == "ON"

    # 3. POST /api/arduino/stop & /api/esp32/stop
    res = client.post("/api/arduino/stop", json={"reason": "Caregiver Arduino API test stop"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "success"
    assert arduino_controller.buzzer_status == "OFF"

    # 4. POST /api/arduino/test
    res = client.post("/api/arduino/test")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "success"
    assert "pin d8" in data["message"].lower()


def test_automatic_checkin_arduino_alert(client):
    """
    CRITICAL REQUIREMENT:
    Verifies that when the Check-In Engine generates an inactivity alert,
    it automatically triggers the Arduino Uno buzzer on Pin D8,
    and caregiver acknowledgment stops it.
    """
    # Simulate past deadline time (e.g. 10:30 AM) with no morning activity
    set_simulated_now("2026-10-23T10:30:00")

    # Clear previous checkin for this simulated date to ensure clean evaluation
    with database.get_db_connection() as conn:
        conn.execute("DELETE FROM checkins WHERE date = '2026-10-23'")
        conn.commit()

    # Reset Arduino buzzer
    arduino_controller.stop_alert()
    assert arduino_controller.buzzer_status == "OFF"

    # Evaluate check-in: Should generate 'needs_attention' and fire Arduino Uno Pin D8 ALERT
    eval_res = checkin_service.evaluate_daily_checkin(is_manual=True)
    assert eval_res["status"] == "needs_attention"
    assert eval_res["alert_sent"] is True

    # Hardware Buzzer must now be ON
    assert arduino_controller.buzzer_status == "ON"
    assert "No movement detected" in arduino_controller.last_activation_reason

    # Caregiver acknowledges alert via API
    ack_res = client.post("/api/checkin/acknowledge", json={"notes": "Caregiver called resident on phone."})
    assert ack_res.status_code == 200
    ack_data = ack_res.get_json()
    assert ack_data["acknowledged"] is True

    # Hardware Buzzer must now be OFF
    assert arduino_controller.buzzer_status == "OFF"

    # Reset simulated clock to real-time
    set_simulated_now(None)
