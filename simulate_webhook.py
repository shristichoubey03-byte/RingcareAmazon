"""
RingCare Webhook Simulator CLI.

Generates authentic JSON:API event payloads, calculates exact HMAC-SHA256 signatures
using the shared RING_HMAC_KEY, and sends HTTP POST requests to the local or remote
RingCare /webhooks/ring endpoint.

Usage Examples:
    python simulate_webhook.py doorbell
    python simulate_webhook.py human
    python simulate_webhook.py pet
    python simulate_webhook.py offline
    python simulate_webhook.py online
    python simulate_webhook.py test-bad-sig
    python simulate_webhook.py test-dedupe
"""

import sys
import time
import json
import hmac
import hashlib
import argparse
import requests
from datetime import datetime

from config import RING_HMAC_KEY, PORT

DEFAULT_ENDPOINT = f"http://127.0.0.1:{PORT}/webhooks/ring"

def make_payload(event_type: str, sub_type: str = None, device_id: str = "dev_front_door_01", req_id: str = None, event_time: str = None):
    """Builds a JSON:API complaint webhook event payload."""
    if not req_id:
        req_id = f"sim_req_{int(time.time() * 1000)}"

    return {
        "meta": {
            "request_id": req_id,
            "version": "1.0"
        },
        "data": {
            "type": event_type,
            "id": f"evt_{int(time.time())}",
            "attributes": {
                "event_type": event_type,
                "sub_type": sub_type,
                "created_at": event_time or datetime.now().isoformat(),
                "device_id": device_id
            },
            "relationships": {
                "device": {
                    "data": {
                        "type": "devices",
                        "id": device_id
                    }
                }
            }
        }
    }

def sign_body(raw_bytes: bytes, key: str) -> str:
    """Calculates 'sha256=' + hex HMAC-SHA256 signature."""
    digest = hmac.new(key.encode("utf-8"), raw_bytes, hashlib.sha256).hexdigest()
    return f"sha256={digest}"

def send_webhook(endpoint: str, payload: dict, hmac_key: str, tamper_sig: bool = False):
    """Sends signed webhook POST request to target endpoint."""
    raw_bytes = json.dumps(payload).encode("utf-8")
    signature = sign_body(raw_bytes, hmac_key)

    if tamper_sig:
        signature = "sha256=tampered_invalid_hex_digest_0000000000000000"

    headers = {
        "Content-Type": "application/vnd.api+json",
        "X-Signature": signature,
        "User-Agent": "Amazon-Vision-Webhook-Service/1.0"
    }

    print(f"\n[Simulator] Target URL: {endpoint}")
    print(f"[Simulator] Event Type: {payload['data']['type']} (Sub-type: {payload['data']['attributes'].get('sub_type')})")
    print(f"[Simulator] Request ID: {payload['meta']['request_id']}")
    print(f"[Simulator] X-Signature: {signature}")

    try:
        response = requests.post(endpoint, data=raw_bytes, headers=headers, timeout=5)
        print(f"[Simulator] Response HTTP Status: {response.status_code}")
        print(f"[Simulator] Response Body: {response.text}")
        return response
    except requests.RequestException as e:
        print(f"[Simulator] ERROR connecting to endpoint: {e}")
        return None

def main():
    parser = argparse.ArgumentParser(description="Simulate HMAC-Signed Ring Webhooks")
    parser.add_argument(
        "action",
        choices=["doorbell", "human", "pet", "offline", "online", "test-bad-sig", "test-dedupe"],
        help="Event type or test scenario to simulate"
    )
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="Webhook receiver URL")
    parser.add_argument("--key", default=RING_HMAC_KEY, help="HMAC secret key")
    parser.add_argument("--device", default="dev_front_door_01", help="Device ID")
    parser.add_argument("--time", default=None, help="Custom ISO event timestamp e.g. 2026-10-22T08:15:00")

    args = parser.parse_args()

    if args.action == "doorbell":
        payload = make_payload("button_press", "doorbell", args.device, event_time=args.time)
        send_webhook(args.endpoint, payload, args.key)

    elif args.action == "human":
        payload = make_payload("motion_detected", "human", args.device, event_time=args.time)
        send_webhook(args.endpoint, payload, args.key)

    elif args.action == "pet":
        payload = make_payload("motion_detected", "pet", args.device, event_time=args.time)
        send_webhook(args.endpoint, payload, args.key)

    elif args.action == "offline":
        payload = make_payload("device_offline", None, args.device, event_time=args.time)
        send_webhook(args.endpoint, payload, args.key)

    elif args.action == "online":
        payload = make_payload("device_online", None, args.device, event_time=args.time)
        send_webhook(args.endpoint, payload, args.key)

    elif args.action == "test-bad-sig":
        print("\n--- Testing Webhook Signature Rejection (Expect 401) ---")
        payload = make_payload("button_press", "doorbell", args.device)
        send_webhook(args.endpoint, payload, args.key, tamper_sig=True)

    elif args.action == "test-dedupe":
        print("\n--- Testing Request Deduplication on meta.request_id ---")
        req_id = f"dedupe_test_{int(time.time())}"
        payload = make_payload("button_press", "doorbell", args.device, req_id=req_id)
        print("Sending initial webhook (Expect 200 received)...")
        send_webhook(args.endpoint, payload, args.key)
        print("\nSending duplicate webhook with same request_id (Expect 200 duplicate_ignored)...")
        send_webhook(args.endpoint, payload, args.key)

if __name__ == "__main__":
    main()
