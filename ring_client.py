"""
Ring Partner API (Amazon Vision API) Client.

Hard Constraints & Implementation details:
1. Strict server-to-server calls to https://api.amazonvision.com and https://oauth.ring.com.
2. Bearer authentication: Authorization: Bearer <access_token>.
3. Reactive token refresh on 401 Unauthorized with single retry.
4. Token refresh saves BOTH new access_token and new refresh_token.
5. Rate limit management: 100 req/s, handles 429 Too Many Requests with exponential
   backoff and honors the 'Retry-After' header.
6. High-fidelity demo fallbacks when in DEMO_MODE or without active production credentials.
"""

import time
import json
import logging
import random
import requests
from typing import Dict, Any, Optional, Tuple
from urllib.parse import urljoin

from config import (
    RING_CLIENT_ID,
    RING_CLIENT_SECRET,
    RING_BASE_URL,
    RING_OAUTH_URL,
    DEMO_MODE,
    DEFAULT_BACKOFF_SECONDS,
    MAX_BACKOFF_SECONDS,
)
import database

logger = logging.getLogger("ringcare.client")

class RingAPIError(Exception):
    """Custom exception for Ring Partner API errors."""
    def __init__(self, message: str, status_code: Optional[int] = None, response_body: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body

class RingClient:
    """Server-to-server HTTP client for Ring Partner API / Amazon Vision API."""

    def __init__(self, account_id: Optional[str] = None):
        self.account_id = account_id
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "RingCare-Caregiver-App/1.0",
            "Accept": "application/vnd.api+json",
        })

    def _get_active_token(self) -> Optional[Dict[str, Any]]:
        """Retrieves active access & refresh tokens from the database."""
        return database.get_active_token(self.account_id)

    def exchange_code_for_token(self, code: str) -> Dict[str, Any]:
        """
        Exchanges authorization code for access and refresh tokens.
        Must be completed within 60 seconds of receiving the code.
        Endpoint: POST https://oauth.ring.com/oauth/token (x-www-form-urlencoded)
        """
        if DEMO_MODE and (not RING_CLIENT_ID or not RING_CLIENT_SECRET):
            logger.info("Demo Mode: Simulating Ring OAuth code exchange")
            mock_token_data = {
                "access_token": f"demo_acc_tok_{int(time.time())}",
                "refresh_token": f"demo_ref_tok_{int(time.time())}",
                "token_type": "Bearer",
                "expires_in": 14400, # 4 hours
                "scope": "client",
                "account_id": "ring_acc_grandma_4481"
            }
            return mock_token_data

        url = f"{RING_OAUTH_URL}/oauth/token"
        payload = {
            "grant_type": "authorization_code",
            "client_id": RING_CLIENT_ID,
            "client_secret": RING_CLIENT_SECRET,
            "code": code,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        response = self.session.post(url, data=payload, headers=headers, timeout=10)
        if response.status_code != 200:
            logger.error(f"OAuth code exchange failed [{response.status_code}]: {response.text}")
            raise RingAPIError(f"OAuth code exchange failed: {response.text}", response.status_code, response.text)

        return response.json()

    def refresh_access_token(self) -> Dict[str, Any]:
        """
        Refreshes access token using refresh_token.
        Endpoint: POST https://oauth.ring.com/oauth/token (x-www-form-urlencoded)
        CRITICAL: Refresh returns a NEW refresh token that invalidates the previous one.
        Both access_token and refresh_token MUST be stored.
        """
        token_info = self._get_active_token()
        if not token_info:
            raise RingAPIError("No active token found in database to refresh.")

        current_refresh_token = token_info["refresh_token"]
        account_id = token_info.get("account_id") or "ring_acc_grandma_4481"

        if DEMO_MODE and (not RING_CLIENT_ID or not RING_CLIENT_SECRET):
            logger.info("Demo Mode: Generating refreshed access and refresh token pair")
            new_access_token = f"demo_refreshed_acc_{int(time.time())}"
            new_refresh_token = f"demo_refreshed_ref_{int(time.time())}"
            database.update_tokens(account_id, new_access_token, new_refresh_token, expires_in=14400)
            return {
                "access_token": new_access_token,
                "refresh_token": new_refresh_token,
                "expires_in": 14400,
            }

        url = f"{RING_OAUTH_URL}/oauth/token"
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": current_refresh_token,
            "client_id": RING_CLIENT_ID,
            "client_secret": RING_CLIENT_SECRET,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        response = self.session.post(url, data=payload, headers=headers, timeout=10)
        if response.status_code != 200:
            logger.error(f"Token refresh failed [{response.status_code}]: {response.text}")
            raise RingAPIError(f"Token refresh failed: {response.text}", response.status_code, response.text)

        data = response.json()
        new_access = data.get("access_token")
        new_refresh = data.get("refresh_token")
        expires_in = data.get("expires_in", 14400)

        # Store BOTH new access token and new refresh token
        database.update_tokens(account_id, new_access, new_refresh, expires_in)
        logger.info(f"Successfully refreshed and stored new token pair for account {account_id}")
        return data

    def _request_with_retry(
        self,
        method: str,
        endpoint: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        data: Optional[Any] = None,
        allow_refresh_retry: bool = True,
        max_429_retries: int = 4
    ) -> requests.Response:
        """
        Executes HTTP requests to the Ring Partner API with:
        1. Bearer authorization token header.
        2. Reactive 401 Unauthorized handling (token refresh + retry once).
        3. 429 Too Many Requests exponential backoff honoring 'Retry-After'.
        """
        if headers is None:
            headers = {}

        if "Authorization" not in headers:
            token_info = self._get_active_token()
            if token_info and "access_token" in token_info:
                headers["Authorization"] = f"Bearer {token_info['access_token']}"

        url = urljoin(f"{RING_BASE_URL}/", endpoint.lstrip("/"))
        backoff = DEFAULT_BACKOFF_SECONDS
        retries_429 = 0

        while True:
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    json=json_data,
                    data=data,
                    timeout=15
                )

                # 1. Handle Rate Limiting (429 Too Many Requests)
                if response.status_code == 429:
                    retries_429 += 1
                    if retries_429 > max_429_retries:
                        raise RingAPIError("Rate limit exceeded; max retries reached", 429, response.text)

                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            sleep_time = float(retry_after)
                        except ValueError:
                            sleep_time = backoff
                    else:
                        sleep_time = backoff
                        backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)

                    # Add slight jitter
                    sleep_time += random.uniform(0.1, 0.5)
                    logger.warning(f"Ring API returned 429. Backing off for {sleep_time:.2f}s (retry {retries_429}/{max_429_retries})")
                    time.sleep(sleep_time)
                    continue

                # 2. Handle Reactive 401 Unauthorized (Expired or invalid token)
                if response.status_code == 401 and allow_refresh_retry:
                    logger.warning("Ring API returned 401 Unauthorized. Triggering reactive token refresh.")
                    try:
                        new_tokens = self.refresh_access_token()
                        headers["Authorization"] = f"Bearer {new_tokens['access_token']}"
                        # Retry the request once with new token (allow_refresh_retry=False prevents loops)
                        return self._request_with_retry(
                            method=method,
                            endpoint=endpoint,
                            headers=headers,
                            params=params,
                            json_data=json_data,
                            data=data,
                            allow_refresh_retry=False,
                            max_429_retries=max_429_retries
                        )
                    except Exception as e:
                        logger.error(f"Reactive token refresh failed during 401 recovery: {e}")
                        raise RingAPIError(f"Reactive refresh failed: {e}", 401, response.text)

                return response

            except requests.RequestException as exc:
                logger.error(f"Network error communicating with Ring API: {exc}")
                raise RingAPIError(f"Network error: {exc}")

    # =========================================================================
    # Ring Partner API Endpoints
    # =========================================================================

    def get_user_me(self, token: Optional[str] = None) -> Dict[str, Any]:
        """
        Retrieves user and account metadata for the authenticated token.
        Used during account linking to get the Ring Account ID.
        Endpoint: GET /v1/users/me
        """
        if DEMO_MODE and (not RING_CLIENT_ID or not RING_CLIENT_SECRET):
            return {
                "data": {
                    "type": "users",
                    "id": "user_demo_1001",
                    "attributes": {
                        "email": "grandma.helen@familycare.org",
                        "first_name": "Helen",
                        "last_name": "Caregiver",
                        "account_id": "ring_acc_grandma_4481"
                    }
                }
            }

        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        resp = self._request_with_retry("GET", "/v1/users/me", headers=headers)
        if resp.status_code != 200:
            raise RingAPIError(f"Failed to fetch user profile: {resp.text}", resp.status_code)
        return resp.json()

    def get_devices(self) -> Dict[str, Any]:
        """
        Fetches linked devices with status, capabilities, and locations.
        Endpoint: GET /v1/devices?include=status,capabilities,location,configurations
        """
        if DEMO_MODE and (not RING_CLIENT_ID or not RING_CLIENT_SECRET):
            devices_from_db = database.get_devices()
            return {
                "data": [
                    {
                        "type": "devices",
                        "id": d["id"],
                        "attributes": {
                            "description": d["name"],
                            "kind": d["kind"],
                            "location_name": d["location"],
                            "online": bool(d["online"]),
                            "battery_life": d["battery_level"],
                            "quiet_hours_enabled": bool(d["quiet_hours_enabled"]),
                            "sensitivity": d["sensitivity"]
                        }
                    }
                    for d in devices_from_db
                ]
            }

        resp = self._request_with_retry(
            "GET",
            "/v1/devices",
            params={"include": "status,capabilities,location,configurations"}
        )
        if resp.status_code != 200:
            raise RingAPIError(f"Failed to fetch devices: {resp.text}", resp.status_code)
        return resp.json()

    def get_device_status(self, device_id: str) -> Dict[str, Any]:
        """
        Fetches current status of a device.
        Endpoint: GET /v1/devices/{device_id}/status
        """
        if DEMO_MODE and (not RING_CLIENT_ID or not RING_CLIENT_SECRET):
            device = database.get_device(device_id)
            if not device:
                raise RingAPIError("Device not found", 404)
            return {
                "data": {
                    "type": "device_status",
                    "id": device_id,
                    "attributes": {
                        "online": bool(device["online"]),
                        "battery_percentage": device["battery_level"],
                        "rssi": -55,
                        "motion_detected": False
                    }
                }
            }

        resp = self._request_with_retry("GET", f"/v1/devices/{device_id}/status")
        if resp.status_code != 200:
            raise RingAPIError(f"Failed to get device status: {resp.text}", resp.status_code)
        return resp.json()

    def download_snapshot(self, device_id: str) -> bytes:
        """
        Downloads a recent snapshot JPEG for a device.
        Endpoint: POST /v1/devices/{device_id}/media/image/download
        """
        if DEMO_MODE and (not RING_CLIENT_ID or not RING_CLIENT_SECRET):
            # In demo mode, returns empty bytes or signals caller to use generated SVG snapshot
            return b""

        resp = self._request_with_retry(
            "POST",
            f"/v1/devices/{device_id}/media/image/download",
            headers={"Accept": "image/jpeg"}
        )
        if resp.status_code != 200:
            raise RingAPIError(f"Failed to download snapshot: {resp.text}", resp.status_code)
        return resp.content

    def download_video(self, device_id: str) -> bytes:
        """
        Downloads a short MP4 video clip.
        Endpoint: POST /v1/devices/{device_id}/media/video/download
        """
        if DEMO_MODE and (not RING_CLIENT_ID or not RING_CLIENT_SECRET):
            return b""

        resp = self._request_with_retry(
            "POST",
            f"/v1/devices/{device_id}/media/video/download",
            headers={"Accept": "video/mp4"}
        )
        if resp.status_code != 200:
            raise RingAPIError(f"Failed to download video clip: {resp.text}", resp.status_code)
        return resp.content

    def get_device_history(self, device_id: str, limit: int = 20) -> Dict[str, Any]:
        """
        Polls device event history (polling fallback / audit).
        Endpoint: GET /v1/history/devices/{device_id}/events
        """
        if DEMO_MODE and (not RING_CLIENT_ID or not RING_CLIENT_SECRET):
            events = database.get_events(limit=limit)
            return {"data": events}

        resp = self._request_with_retry(
            "GET",
            f"/v1/history/devices/{device_id}/events",
            params={"limit": limit}
        )
        if resp.status_code != 200:
            raise RingAPIError(f"Failed to fetch device event history: {resp.text}", resp.status_code)
        return resp.json()

    def play_audio_chime(self, device_id: str, audio_ref: str) -> Dict[str, Any]:
        """
        Plays an audio message or sound through the device chime/speaker.
        Endpoint: POST /v1/devices/{device_id}/media/audio/playback
        """
        logger.info(f"Triggering chime playback on device {device_id} with audio_ref '{audio_ref}'")
        if DEMO_MODE and (not RING_CLIENT_ID or not RING_CLIENT_SECRET):
            return {"status": "success", "playback_id": f"chime_pb_{int(time.time())}", "audio_ref": audio_ref}

        payload = {
            "data": {
                "type": "audio_playback",
                "attributes": {
                    "audio_ref": audio_ref
                }
            }
        }
        resp = self._request_with_retry(
            "POST",
            f"/v1/devices/{device_id}/media/audio/playback",
            json_data=payload
        )
        if resp.status_code not in (200, 201, 202):
            raise RingAPIError(f"Failed to play audio chime: {resp.text}", resp.status_code)
        return resp.json()

    def create_app_integration(self, account_identifier: str, nonce: str) -> Dict[str, Any]:
        """
        Registers integration with Ring account during Account Linking flow.
        Endpoint: POST /v1/accounts/me/app-integrations
        """
        logger.info(f"Registering app-integration for {account_identifier} with nonce={nonce}")
        if DEMO_MODE and (not RING_CLIENT_ID or not RING_CLIENT_SECRET):
            return {
                "data": {
                    "type": "app_integrations",
                    "id": "integ_demo_9876",
                    "attributes": {
                        "account_identifier": account_identifier,
                        "status": "pending_completion"
                    }
                }
            }

        payload = {
            "data": {
                "type": "app_integrations",
                "attributes": {
                    "account_identifier": account_identifier,
                    "nonce": nonce
                }
            }
        }
        resp = self._request_with_retry("POST", "/v1/accounts/me/app-integrations", json_data=payload)
        if resp.status_code not in (200, 201):
            raise RingAPIError(f"Failed to create app integration: {resp.text}", resp.status_code)
        return resp.json()

    def complete_app_integration(self, status: str = "completed") -> Dict[str, Any]:
        """
        Mandatory step: Completes app integration verification.
        Endpoint: PATCH /v1/accounts/me/app-integrations
        """
        logger.info("Completing app integration status='completed'")
        if DEMO_MODE and (not RING_CLIENT_ID or not RING_CLIENT_SECRET):
            return {
                "data": {
                    "type": "app_integrations",
                    "id": "integ_demo_9876",
                    "attributes": {
                        "status": "completed"
                    }
                }
            }

        payload = {
            "data": {
                "type": "app_integrations",
                "attributes": {
                    "status": status
                }
            }
        }
        resp = self._request_with_retry("PATCH", "/v1/accounts/me/app-integrations", json_data=payload)
        if resp.status_code not in (200, 204):
            raise RingAPIError(f"Failed to complete app integration: {resp.text}", resp.status_code)
        return resp.json() if resp.text else {"status": "completed"}
