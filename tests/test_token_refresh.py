"""
Unit tests for Ring Partner API token refresh and rate limiting.
Tests adherence to:
- Reactive 401 Unauthorized token refresh with retry
- Simultaneous storage of BOTH new access_token and new refresh_token
- Rate limiting 429 backoff with Retry-After header
"""

import time
import pytest
import requests
from unittest.mock import MagicMock, patch
from ring_client import RingClient, RingAPIError
import database

TEST_ACCOUNT_ID = "ring_acc_grandma_4481"

@pytest.fixture(autouse=True)
def setup_db():
    database.init_db()
    # Ensure fresh active token in DB
    now_ts = time.time()
    database.update_tokens(
        account_id=TEST_ACCOUNT_ID,
        new_access_token="initial_access_tok_111",
        new_refresh_token="initial_refresh_tok_111",
        expires_in=14400
    )

def test_refresh_token_stores_both_tokens(monkeypatch):
    """Verifies that refreshing tokens updates BOTH access_token and refresh_token in SQLite."""
    monkeypatch.setattr("ring_client.DEMO_MODE", False)
    monkeypatch.setattr("ring_client.RING_CLIENT_ID", "test_client_id")
    monkeypatch.setattr("ring_client.RING_CLIENT_SECRET", "test_client_secret")

    client = RingClient(account_id=TEST_ACCOUNT_ID)

    mock_oauth_response = {
        "access_token": "brand_new_access_tok_222",
        "refresh_token": "brand_new_refresh_tok_222",
        "expires_in": 14400,
        "token_type": "Bearer"
    }

    with patch.object(client.session, "post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_oauth_response
        mock_post.return_value = mock_resp

        result = client.refresh_access_token()

    assert result["access_token"] == "brand_new_access_tok_222"
    assert result["refresh_token"] == "brand_new_refresh_tok_222"

    # Verify both tokens are persisted in DB
    active = database.get_active_token(TEST_ACCOUNT_ID)
    assert active["access_token"] == "brand_new_access_tok_222"
    assert active["refresh_token"] == "brand_new_refresh_tok_222"

def test_reactive_401_refresh_and_retry():
    """
    Verifies that when a request returns 401 Unauthorized:
    1. refresh_access_token() is automatically invoked.
    2. The request is retried once with the new Bearer token.
    3. The successful retry response is returned.
    """
    client = RingClient(account_id=TEST_ACCOUNT_ID)

    resp_401 = MagicMock()
    resp_401.status_code = 401
    resp_401.text = "Unauthorized: Access token expired"

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = {"data": [{"id": "dev_front_door_01"}]}

    with patch.object(client.session, "request", side_effect=[resp_401, resp_200]) as mock_request:
        with patch.object(client, "refresh_access_token") as mock_refresh:
            mock_refresh.return_value = {
                "access_token": "refreshed_access_token_333",
                "refresh_token": "refreshed_refresh_token_333",
                "expires_in": 14400
            }

            resp = client._request_with_retry("GET", "/v1/devices")

            assert resp.status_code == 200
            # Ensure refresh was triggered reactively
            mock_refresh.assert_called_once()
            # Ensure session.request was called twice (initial + retry)
            assert mock_request.call_count == 2
            # Verify retry used new Authorization header
            retry_headers = mock_request.call_args_list[1][1]["headers"]
            assert retry_headers["Authorization"] == "Bearer refreshed_access_token_333"

def test_rate_limit_429_backoff_and_retry():
    """Verifies that HTTP 429 triggers backoff honoring Retry-After and retries successfully."""
    client = RingClient(account_id=TEST_ACCOUNT_ID)

    resp_429 = MagicMock()
    resp_429.status_code = 429
    resp_429.headers = {"Retry-After": "0.1"} # 100ms for fast test execution
    resp_429.text = "Too Many Requests"

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = {"status": "ok"}

    with patch.object(client.session, "request", side_effect=[resp_429, resp_200]) as mock_request:
        with patch("time.sleep") as mock_sleep:
            resp = client._request_with_retry("GET", "/v1/devices/status")
            assert resp.status_code == 200
            assert mock_request.call_count == 2
            mock_sleep.assert_called()
