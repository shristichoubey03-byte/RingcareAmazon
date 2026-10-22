"""
RingCare Flask Application.

Endpoints implemented per Ring Partner API specifications:
1. POST /oauth/token (Token Exchange URL): Exchanges auth code, stores tokens as unclaimed, returns 200 quickly.
2. GET /ring/link (Account Link URL): Enforces sign-in form, validates timestamp (within 600s),
   verifies HMAC nonce against f"{time}:{account_id}", registers app-integrations (POST then PATCH completed).
3. POST /webhooks/ring (Webhook URL): Verifies X-Signature (HMAC-SHA256), dedupes on meta.request_id,
   returns 200 within 5s, processes asynchronously.
4. GET / (App Homepage URL): Accessible caregiver dashboard.
5. GET /api/events/stream: Server-Sent Events (SSE) for real-time visual alerts and TTS.
6. API endpoints for visitor tagging, device controls, chime playback, privacy data deletion.
"""

import os
import time
import json
import base64
import hmac
import hashlib
import logging
import uuid
from datetime import datetime, date
from typing import Dict, Any, Generator

from flask import (
    Flask,
    request,
    jsonify,
    render_template,
    Response,
    session,
    redirect,
    url_for,
    send_from_directory,
    send_file
)

from config import (
    PORT,
    HOST,
    SECRET_KEY,
    RING_HMAC_KEY,
    RING_CLIENT_ID,
    DEMO_MODE,
    CHECKIN_WINDOW_START,
    CHECKIN_TARGET,
    CHECKIN_TARGET_TIME,
    BASE_DIR,
    get_now,
    set_simulated_now,
    format_timestamp
)
import database
import snapshot_service
from ring_client import RingClient, RingAPIError
import webhook_handler
import checkin_service

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("ringcare.app")

# Initialize Flask app
app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = SECRET_KEY

# Register Jinja unified time format filter
@app.template_filter("format_time")
def jinja_format_time(val):
    return format_timestamp(val)

# Initialize Database, Snapshots and Scheduler on startup
with app.app_context():
    snapshot_service.init_snapshots()
    database.init_db()
    checkin_service.start_scheduler()

# Helper for masking emails (e.g. j***n@domain.com)
def mask_email(email: str) -> str:
    if "@" not in email:
        return email
    user, domain = email.split("@", 1)
    if len(user) <= 2:
        masked_user = user[0] + "*"
    else:
        masked_user = user[0] + "*" * (len(user) - 2) + user[-1]
    return f"{masked_user}@{domain}"

# =============================================================================
# Ring Partner API Registered URLs
# =============================================================================

@app.route("/oauth/token", methods=["POST"])
def oauth_token_exchange():
    """
    1. Token Exchange URL (Registered in Ring Developer Portal)
    Receives Ring authorization code from OAuth callback, exchanges it via
    POST https://oauth.ring.com/oauth/token within 60 seconds, stores tokens
    as 'unclaimed', and returns 200 OK immediately.
    """
    data = request.get_json(silent=True) or request.form.to_dict()
    code = data.get("code") or request.args.get("code")

    if not code:
        return jsonify({"error": "missing_code", "message": "Authorization code is required"}), 400

    logger.info("Received OAuth code on /oauth/token. Exchanging with Ring OAuth server...")
    client = RingClient()

    try:
        token_response = client.exchange_code_for_token(code)
        access_token = token_response.get("access_token")
        refresh_token = token_response.get("refresh_token")
        expires_in = token_response.get("expires_in", 14400)
        account_id = token_response.get("account_id")

        token_id = database.store_unclaimed_token(
            account_id=account_id,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in
        )

        logger.info(f"Successfully exchanged OAuth code. Stored as unclaimed token ID {token_id}")
        return jsonify({
            "status": "success",
            "message": "Token exchanged successfully and stored as unclaimed",
            "token_id": token_id
        }), 200

    except Exception as e:
        logger.error(f"Error exchanging OAuth code: {e}")
        return jsonify({"error": "exchange_failed", "message": str(e)}), 500


@app.route("/ring/link", methods=["GET", "POST"])
def account_link():
    """
    2. Account Link URL (Registered in Ring Developer Portal)
    Spec:
    - User arrives with ?nonce=...&time=...
    - Show a sign-in / create-account form first. Do NOT auto-verify the nonce without sign-in.
    - Reject if timestamp older than 600s or in the future.
    - After sign-in, call GET /v1/users/me with each unclaimed token to get its Account ID.
    - Compute nonce = urlsafe_b64encode(HMAC_SHA256(hmac_key, f"{time}:{account_id}")).rstrip('=')
      and match with hmac.compare_digest.
    - On match:
      POST /v1/accounts/me/app-integrations {account_identifier: <masked email>, nonce}
      then PATCH /v1/accounts/me/app-integrations {status:"completed"} (mandatory).
    """
    nonce = request.args.get("nonce", "")
    time_param = request.args.get("time", "")

    # GET: Display sign-in form or link page
    if request.method == "GET":
        # Validate timestamp presence
        time_valid = True
        time_error = None
        if time_param:
            try:
                link_time = int(time_param)
                now_ts = int(time.time())
                # Reject if older than 600 seconds or more than 60 seconds in future (clock skew tolerance)
                if (now_ts - link_time) > 600 or (link_time - now_ts) > 60:
                    time_valid = False
                    time_error = f"Timestamp expired or invalid. Nonce age: {now_ts - link_time}s (max allowed: 600s)."
            except ValueError:
                time_valid = False
                time_error = "Timestamp format is invalid."

        return render_template(
            "link.html",
            nonce=nonce,
            time_param=time_param,
            time_valid=time_valid,
            time_error=time_error,
            demo_mode=DEMO_MODE,
            demo_hmac_key=RING_HMAC_KEY
        )

    # POST: Process Caregiver Sign-in and Account Linking
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "").strip()

    if not email:
        return render_template(
            "link.html",
            nonce=nonce,
            time_param=time_param,
            error="Please enter your caregiver email address.",
            demo_mode=DEMO_MODE
        )

    # Verify time parameter
    try:
        link_time = int(time_param)
        now_ts = int(time.time())
        if (now_ts - link_time) > 600 or (link_time - now_ts) > 60:
            return render_template(
                "link.html",
                nonce=nonce,
                time_param=time_param,
                error="Account linking session expired (older than 600 seconds). Please restart from the Ring app.",
                demo_mode=DEMO_MODE
            ), 400
    except ValueError:
        return render_template(
            "link.html",
            nonce=nonce,
            time_param=time_param,
            error="Invalid time parameter format.",
            demo_mode=DEMO_MODE
        ), 400

    # Retrieve unclaimed tokens
    unclaimed = database.get_unclaimed_tokens()
    if not unclaimed and DEMO_MODE:
        # In demo mode, automatically create an unclaimed token for smooth testing
        demo_id = database.store_unclaimed_token(
            account_id="ring_acc_grandma_4481",
            access_token=f"demo_acc_tok_{now_ts}",
            refresh_token=f"demo_ref_tok_{now_ts}"
        )
        unclaimed = database.get_unclaimed_tokens()

    if not unclaimed:
        return render_template(
            "link.html",
            nonce=nonce,
            time_param=time_param,
            error="No pending Ring authorization found. Please initiate linking from your Ring account first.",
            demo_mode=DEMO_MODE
        ), 400

    matched_token = None
    client = RingClient()

    # Iterate through unclaimed tokens, fetch account_id via GET /v1/users/me, compute HMAC nonce
    for tok in unclaimed:
        try:
            user_me = client.get_user_me(token=tok["access_token"])
            acc_id = user_me.get("data", {}).get("attributes", {}).get("account_id") or tok.get("account_id")
            if not acc_id:
                continue

            # Compute expected nonce:
            # nonce = urlsafe_b64encode(HMAC_SHA256(hmac_key, f"{time}:{account_id}")).rstrip('=')
            raw_msg = f"{time_param}:{acc_id}".encode("utf-8")
            raw_hmac = hmac.new(RING_HMAC_KEY.encode("utf-8"), raw_msg, hashlib.sha256).digest()
            expected_nonce = base64.urlsafe_b64encode(raw_hmac).decode("utf-8").rstrip("=")

            # Constant-time comparison
            if hmac.compare_digest(expected_nonce, nonce):
                matched_token = tok
                matched_account_id = acc_id
                break
        except Exception as e:
            logger.warning(f"Error checking unclaimed token: {e}")

    # Fallback in demo mode if nonce is empty or demo test
    if not matched_token and DEMO_MODE and (not nonce or nonce == "demo_test"):
        matched_token = unclaimed[0]
        matched_account_id = "ring_acc_grandma_4481"
        logger.info("Demo Mode: Nonce bypassed for simulated account link test.")

    if not matched_token:
        logger.warning(f"Nonce mismatch for incoming nonce '{nonce}' at time '{time_param}'")
        return render_template(
            "link.html",
            nonce=nonce,
            time_param=time_param,
            error="HMAC Nonce verification failed. The link request could not be authenticated with Ring.",
            demo_mode=DEMO_MODE
        ), 403

    # On match:
    # 1. POST /v1/accounts/me/app-integrations {account_identifier: <masked email>, nonce}
    # 2. PATCH /v1/accounts/me/app-integrations {status: "completed"} (mandatory)
    masked = mask_email(email)
    try:
        client.create_app_integration(account_identifier=masked, nonce=nonce)
        client.complete_app_integration(status="completed")

        # Claim token in database
        database.claim_token(matched_token["id"], matched_account_id, email)
        session["user_email"] = email
        session["account_id"] = matched_account_id

        logger.info(f"Successfully verified and linked Ring account {matched_account_id} to {email}")
        return render_template(
            "link.html",
            success=True,
            masked_email=masked,
            account_id=matched_account_id
        )

    except Exception as e:
        logger.error(f"Error finalizing app integration: {e}")
        return render_template(
            "link.html",
            nonce=nonce,
            time_param=time_param,
            error=f"Ring App Integration handshake failed: {str(e)}",
            demo_mode=DEMO_MODE
        ), 500


@app.route("/webhooks/ring", methods=["POST"])
def ring_webhook():
    """
    3. Webhook URL (Registered in Ring Developer Portal)
    Spec:
    - Verify X-Signature header = "sha256=" + hex HMAC-SHA256 of RAW request body bytes
    - Dedupe on meta.request_id
    - Return 200 within 5 seconds
    - Process asynchronously via background queue
    """
    raw_body = request.get_data()
    signature_header = request.headers.get("X-Signature")

    # 1. Verify HMAC-SHA256 signature
    if not webhook_handler.verify_webhook_signature(raw_body, signature_header, RING_HMAC_KEY):
        logger.warning("Rejected webhook due to invalid X-Signature header.")
        return jsonify({"error": "invalid_signature", "message": "HMAC signature mismatch"}), 401

    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"error": "invalid_payload", "message": "Expected JSON payload"}), 400

    # 2. Deduplication on meta.request_id
    request_id = payload.get("meta", {}).get("request_id")
    if request_id and database.is_duplicate_request(request_id):
        logger.info(f"Duplicate webhook request_id '{request_id}' ignored (idempotent 200).")
        return jsonify({"status": "duplicate_ignored", "request_id": request_id}), 200

    # 3. Queue for asynchronous background processing
    try:
        webhook_handler.webhook_queue.put_nowait(payload)
    except Exception as e:
        logger.error(f"Failed to queue webhook payload: {e}")

    # Return 200 quickly (well within 5 seconds constraint)
    return jsonify({"status": "received", "request_id": request_id}), 200


# =============================================================================
# Dashboard & Core UI Routes
# =============================================================================

@app.route("/")
def dashboard():
    """
    4. App Homepage URL (Caregiver Dashboard)
    Serves the primary high-contrast, accessible caregiver dashboard.
    """
    devices = database.get_devices()
    checkin_status = database.get_today_checkin_status()
    recent_events = database.get_events(limit=25)
    now = get_now()

    return render_template(
        "index.html",
        devices=devices,
        checkin_status=checkin_status,
        events=recent_events,
        checkin_window_start=CHECKIN_WINDOW_START,
        checkin_target_time=CHECKIN_TARGET,
        demo_mode=DEMO_MODE,
        current_time=format_timestamp(now),
        demo_now=now.strftime("%Y-%m-%dT%H:%M:%S")
    )


@app.route("/privacy")
def privacy_page():
    """Privacy notice and GDPR-compliant data management page."""
    return render_template("privacy.html")


# =============================================================================
# API Endpoints for Dashboard Real-time Events & Controls
# =============================================================================

@app.route("/api/events/stream")
def sse_event_stream():
    """
    Server-Sent Events (SSE) stream for real-time dashboard alerts.
    Enables instant flashing visual alerts and speech synthesis for deaf/hard-of-hearing.
    """
    def event_stream():
        client_queue = webhook_handler.subscribe_sse()
        logger.info("SSE client connected to live alert stream.")
        try:
            # Send initial ping
            yield "event: ping\ndata: {\"status\":\"connected\"}\n\n"
            while True:
                msg = client_queue.get()
                yield f"event: message\ndata: {json.dumps(msg)}\n\n"
        except GeneratorExit:
            webhook_handler.unsubscribe_sse(client_queue)
            logger.info("SSE client disconnected from stream.")

    return Response(event_stream(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive"
    })


@app.route("/api/devices", methods=["GET"])
def api_get_devices():
    """Fetches list of registered devices and their online status."""
    return jsonify({"devices": database.get_devices()})


@app.route("/api/devices/<device_id>/settings", methods=["POST"])
def api_update_device_settings(device_id):
    """Updates device quiet hours and sensitivity preferences."""
    data = request.get_json() or {}
    database.update_device_settings(
        device_id=device_id,
        quiet_hours_enabled=data.get("quiet_hours_enabled", False),
        quiet_hours_start=data.get("quiet_hours_start", "22:00"),
        quiet_hours_end=data.get("quiet_hours_end", "07:00"),
        sensitivity=data.get("sensitivity", "medium")
    )
    return jsonify({"status": "success", "message": "Settings updated"})


@app.route("/api/events", methods=["GET"])
def api_get_events():
    """Fetches event history with optional filtering."""
    event_type = request.args.get("type")
    visitor_filter = request.args.get("visitor")
    limit = int(request.args.get("limit", 50))
    events = database.get_events(limit=limit, filter_type=event_type, visitor_filter=visitor_filter)
    return jsonify({"events": events})


@app.route("/api/events/<int:event_id>/visitor", methods=["POST"])
def api_update_visitor(event_id):
    """Updates visitor classification (known/unknown) and caregiver notes."""
    data = request.get_json() or {}
    visitor_tag = data.get("visitor_tag", "unassigned")
    visitor_notes = data.get("visitor_notes")
    database.update_visitor_status(event_id, visitor_tag, visitor_notes)
    return jsonify({"status": "success", "event_id": event_id, "visitor_tag": visitor_tag})


@app.route("/api/devices/<device_id>/chime", methods=["POST"])
def api_play_chime(device_id):
    """Plays audio message or chime through the Ring device."""
    data = request.get_json() or {}
    audio_ref = data.get("audio_ref", "chime_gentle_bell")
    custom_message = data.get("message", "")

    client = RingClient()
    try:
        res = client.play_audio_chime(device_id, audio_ref)
        # Also broadcast alert that chime message was played
        device = database.get_device(device_id)
        dev_name = device["name"] if device else "Ring Device"
        webhook_handler.broadcast_event({
            "type": "chime_played",
            "device_id": device_id,
            "device_name": dev_name,
            "audio_ref": audio_ref,
            "message": custom_message or f"Chime message '{audio_ref}' played on {dev_name}."
        })
        return jsonify({"status": "success", "response": res})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/checkin/status", methods=["GET"])
def api_checkin_status():
    """Retrieves current care check-in status."""
    return jsonify(database.get_today_checkin_status())


@app.route("/api/checkin/run", methods=["POST"])
def api_checkin_run():
    """Triggers an immediate check-in evaluation (for hackathon demo)."""
    res = checkin_service.evaluate_daily_checkin(is_manual=True)
    return jsonify(res)


@app.route("/api/privacy/delete-data", methods=["POST"])
def api_delete_privacy_data():
    """Deletes all events, snapshots, and activity logs."""
    counts = database.delete_all_user_data()
    webhook_handler.broadcast_event({
        "type": "data_purged",
        "message": "All activity logs and visitor history have been permanently deleted."
    })
    return jsonify({
        "status": "success",
        "message": "All user data deleted successfully.",
        "details": counts
    })


# =============================================================================
# Demo & Simulation Helpers
# =============================================================================

@app.route("/api/demo/time", methods=["POST"])
def api_set_demo_time():
    """
    Overrides the simulated clock for demo mode (DEMO_NOW).
    Allows hackathon judges/testers to fast-forward to morning (e.g. 08:15 AM)
    or test inactivity thresholds (e.g. 10:30 AM).
    """
    data = request.get_json() or {}
    new_time_str = data.get("simulated_now", "").strip()
    effective_dt = set_simulated_now(new_time_str if new_time_str else None)

    # Immediately evaluate checkin status for the new simulated time
    eval_res = checkin_service.evaluate_daily_checkin(is_manual=True)

    return jsonify({
        "status": "success",
        "effective_time": format_timestamp(effective_dt),
        "iso": effective_dt.isoformat(),
        "checkin": eval_res
    })


@app.route("/api/simulate", methods=["POST"])
def api_simulate_event():
    """
    Direct simulation API from the dashboard:
    Simulates doorbell press, human motion, pet movement, or offline states.
    """
    data = request.get_json() or {}
    sim_type = data.get("type", "button_press")
    device_id = data.get("device_id", "dev_front_door_01")
    sub_type = data.get("sub_type", "human" if sim_type == "motion_detected" else None)

    # Live Toggle Offline handling
    if sim_type == "device_offline":
        database.update_device_status(device_id, False)
        dev = database.get_device(device_id)
        dev_name = dev["name"] if dev else "Ring Device"
        webhook_handler.broadcast_event({
            "type": "device_status",
            "device_id": device_id,
            "online": False,
            "message": f"Alert: {dev_name} went offline!"
        })
        return jsonify({"status": "simulated", "type": sim_type, "online": False, "device_id": device_id})

    if sim_type == "device_online":
        database.update_device_status(device_id, True)
        dev = database.get_device(device_id)
        dev_name = dev["name"] if dev else "Ring Device"
        webhook_handler.broadcast_event({
            "type": "device_status",
            "device_id": device_id,
            "online": True,
            "message": f"{dev_name} is now online."
        })
        return jsonify({"status": "simulated", "type": sim_type, "online": True, "device_id": device_id})

    now = get_now()
    unique_suffix = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    req_id = f"sim_req_{unique_suffix}"

    payload = {
        "meta": {"request_id": req_id},
        "data": {
            "id": f"sim_evt_{unique_suffix}",
            "type": sim_type,
            "attributes": {
                "event_type": sim_type,
                "sub_type": sub_type,
                "created_at": now.isoformat(),
                "device_id": device_id
            },
            "relationships": {
                "device": {
                    "data": {"type": "devices", "id": device_id}
                }
            }
        }
    }

    # Pass into webhook queue
    webhook_handler.webhook_queue.put(payload)
    return jsonify({"status": "simulated", "request_id": req_id, "type": sim_type})


@app.route("/api/demo/snapshot")
def demo_snapshot_fallback():
    """
    Fallback endpoint redirecting to generated static JPEG snapshot files.
    Ensures that any request to /api/demo/snapshot returns a valid image.
    """
    ev_type = request.args.get("type", "doorbell").lower()
    if "doorbell" in ev_type:
        return redirect("/static/snapshots/placeholder_doorbell.jpg")
    elif "human" in ev_type:
        return redirect("/static/snapshots/placeholder_human.jpg")
    elif "pet" in ev_type:
        return redirect("/static/snapshots/placeholder_pet.jpg")
    return redirect("/static/snapshots/placeholder_motion.jpg")


@app.route("/download/zip")
def download_project_zip():
    """Directly serves the clean project zip file for downloading."""
    zip_path = BASE_DIR / "ringcare.zip"
    if not zip_path.exists():
        zip_path = Path(r"C:\Users\SHRISTI CHOUBEY\Desktop\ringcare.zip")
    return send_file(zip_path, as_attachment=True, download_name="ringcare.zip")


# Run entry point
if __name__ == "__main__":
    logger.info(f"Starting RingCare server on http://{HOST}:{PORT} (DEMO_MODE={DEMO_MODE})")
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
