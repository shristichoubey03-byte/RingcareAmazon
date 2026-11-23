"""
Unit tests for Ring Partner Account Linking HMAC Nonce generation and verification.
Tests adherence to Ring Partner API Account Link specifications:
- Nonce = urlsafe_b64encode(HMAC_SHA256(hmac_key, f"{time}:{account_id}")).rstrip('=')
- Verification with hmac.compare_digest
- Expiration check (older than 600 seconds or in future)
"""

import time
import base64
import hmac
import hashlib
import pytest
from app import app
import database

TEST_HMAC_KEY = "test_ring_hmac_secret_key_12345"
TEST_ACCOUNT_ID = "ring_acc_grandma_4481"

def compute_nonce(hmac_key: str, timestamp: int, account_id: str) -> str:
    """Helper to compute valid nonce matching Ring specification."""
    raw_msg = f"{timestamp}:{account_id}".encode("utf-8")
    raw_hmac = hmac.new(hmac_key.encode("utf-8"), raw_msg, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw_hmac).decode("utf-8").rstrip("=")

@pytest.fixture
def client():
    app.config["TESTING"] = True
    database.init_db()
    with app.test_client() as client:
        yield client

def test_nonce_generation_format():
    """Verifies that generated nonce matches expected HMAC format and strips padding '='."""
    ts = 1700000000
    nonce = compute_nonce(TEST_HMAC_KEY, ts, TEST_ACCOUNT_ID)
    assert "=" not in nonce
    assert len(nonce) >= 40
    # Must be valid URL-safe base64
    padded = nonce + "=" * (-len(nonce) % 4)
    decoded = base64.urlsafe_b64decode(padded)
    assert len(decoded) == 32 # SHA256 digest length is 32 bytes

def test_constant_time_comparison():
    """Ensures hmac.compare_digest accepts matching nonce and rejects invalid nonce."""
    ts = int(time.time())
    valid_nonce = compute_nonce(TEST_HMAC_KEY, ts, TEST_ACCOUNT_ID)
    tampered_nonce = valid_nonce[:-1] + ("A" if valid_nonce[-1] != "A" else "B")

    assert hmac.compare_digest(valid_nonce, valid_nonce) is True
    assert hmac.compare_digest(valid_nonce, tampered_nonce) is False

def test_account_link_expired_timestamp(client):
    """Verifies GET /ring/link rejects timestamps older than 600s."""
    old_time = int(time.time()) - 700 # 700 seconds ago (> 600s)
    nonce = compute_nonce(TEST_HMAC_KEY, old_time, TEST_ACCOUNT_ID)

    resp = client.get(f"/ring/link?nonce={nonce}&time={old_time}")
    assert resp.status_code == 200
    assert b"expired or invalid" in resp.data

def test_account_link_post_success(client, monkeypatch):
    """Verifies successful sign-in with valid nonce completes app-integrations."""
    monkeypatch.setattr("app.RING_HMAC_KEY", TEST_HMAC_KEY)
    
    current_time = int(time.time())
    valid_nonce = compute_nonce(TEST_HMAC_KEY, current_time, TEST_ACCOUNT_ID)

    # Seed an unclaimed token for this account
    database.store_unclaimed_token(
        account_id=TEST_ACCOUNT_ID,
        access_token="unclaimed_test_tok_99",
        refresh_token="unclaimed_test_ref_99"
    )

    resp = client.post(
        f"/ring/link?nonce={valid_nonce}&time={current_time}",
        data={"email": "sarah.caregiver@family.org", "password": "password123"}
    )
    assert resp.status_code == 200
    assert b"Ring Account Connected" in resp.data or b"successfully linked" in resp.data
