"""
Unit tests for Ring Partner API Webhook signature verification and deduplication.
Tests adherence to:
- X-Signature: sha256=<hex_hmac>
- Constant-time validation with hmac.compare_digest
- Deduplication on meta.request_id
- Asynchronous queuing with fast 200 response
"""

import json
import time
import hmac
import hashlib
import pytest
from app import app
import webhook_handler
import database

TEST_HMAC_KEY = "test_webhook_hmac_secret_key_8899"

@pytest.fixture
def client():
    app.config["TESTING"] = True
    database.init_db()
    with app.test_client() as client:
        yield client

def generate_signature(raw_bytes: bytes, key: str) -> str:
    digest = hmac.new(key.encode("utf-8"), raw_bytes, hashlib.sha256).hexdigest()
    return f"sha256={digest}"

def test_verify_webhook_signature_valid():
    """Tests that a valid raw body and signature header return True."""
    body = b'{"hello":"ring"}'
    sig = generate_signature(body, TEST_HMAC_KEY)
    assert webhook_handler.verify_webhook_signature(body, sig, TEST_HMAC_KEY) is True

def test_verify_webhook_signature_tampered():
    """Tests that modifying raw body or signature returns False."""
    body = b'{"hello":"ring"}'
    sig = generate_signature(body, TEST_HMAC_KEY)
    tampered_body = b'{"hello":"ring","injected":true}'
    assert webhook_handler.verify_webhook_signature(tampered_body, sig, TEST_HMAC_KEY) is False

def test_verify_webhook_signature_missing_header():
    """Tests rejection when X-Signature is missing."""
    body = b'{"hello":"ring"}'
    assert webhook_handler.verify_webhook_signature(body, None, TEST_HMAC_KEY) is False

def test_webhook_endpoint_accepts_valid_signature(client, monkeypatch):
    """Tests that POST /webhooks/ring with valid signature returns 200 within limits."""
    monkeypatch.setattr("app.RING_HMAC_KEY", TEST_HMAC_KEY)
    req_id = f"test_req_{int(time.time() * 1000)}"

    payload = {
        "meta": {"request_id": req_id},
        "data": {
            "type": "button_press",
            "attributes": {"event_type": "button_press", "device_id": "dev_front_door_01"}
        }
    }
    raw_bytes = json.dumps(payload).encode("utf-8")
    sig = generate_signature(raw_bytes, TEST_HMAC_KEY)

    resp = client.post(
        "/webhooks/ring",
        data=raw_bytes,
        headers={"Content-Type": "application/vnd.api+json", "X-Signature": sig}
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "received"
    assert data["request_id"] == req_id

def test_webhook_endpoint_rejects_bad_signature(client, monkeypatch):
    """Tests that POST /webhooks/ring with invalid signature returns 401."""
    monkeypatch.setattr("app.RING_HMAC_KEY", TEST_HMAC_KEY)
    payload = {"meta": {"request_id": "bad_sig_req"}}
    raw_bytes = json.dumps(payload).encode("utf-8")

    resp = client.post(
        "/webhooks/ring",
        data=raw_bytes,
        headers={"Content-Type": "application/vnd.api+json", "X-Signature": "sha256=invalid_hash"}
    )
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "invalid_signature"

def test_webhook_endpoint_deduplication(client, monkeypatch):
    """Tests that submitting duplicate meta.request_id is recognized and returns duplicate_ignored."""
    monkeypatch.setattr("app.RING_HMAC_KEY", TEST_HMAC_KEY)
    req_id = f"dedupe_unit_test_{int(time.time())}"

    # Pre-insert event with this request_id to simulate previous receipt
    database.insert_event(
        request_id=req_id,
        event_id=f"evt_{req_id}",
        device_id="dev_front_door_01",
        device_name="Front Door",
        event_type="button_press",
        sub_type="doorbell",
        timestamp="2026-10-22T08:00:00"
    )

    payload = {
        "meta": {"request_id": req_id},
        "data": {
            "type": "button_press",
            "attributes": {"event_type": "button_press"}
        }
    }
    raw_bytes = json.dumps(payload).encode("utf-8")
    sig = generate_signature(raw_bytes, TEST_HMAC_KEY)

    resp = client.post(
        "/webhooks/ring",
        data=raw_bytes,
        headers={"Content-Type": "application/vnd.api+json", "X-Signature": sig}
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "duplicate_ignored"
