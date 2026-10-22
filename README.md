# RingCare 🛡️
> **Elderly & Accessibility Care Monitoring on the Ring Partner API (Amazon Vision API)**

RingCare is an assistive caregiving platform built directly on the **Ring Partner API** (Amazon Vision API). Designed to help families look after an elderly or disabled relative using their Ring doorbells and cameras, RingCare solves three critical safety challenges:
1. **Daily Care Check-in:** Monitors for morning activity and alerts the caregiver if no motion or door activity is detected by a configurable target time (e.g., 10:00 AM).
2. **Accessibility Alerts (Deaf & Hard-of-Hearing):** On `button_press` or human `motion_detected`, RingCare immediately displays a pulsating high-contrast visual alert banner and speaks the notification aloud via browser Text-to-Speech (TTS).
3. **Smart Visitor Log & Safety Tagging:** Captures snapshots on doorbell events and allows caregivers to classify visitors as **Known** (caregiver, nurse, family) or **Unknown** with persistent notes.
4. **Caregiver Command Dashboard:** Real-time device telemetry, online status, quiet hours schedules, today's activity rhythm, and a *"Play Message on Chime"* feature.

---

## 🏗️ Architecture Diagram

```mermaid
flowchart TD
    subgraph Ring Ecosystem
        RD[Ring Video Doorbell / Stick Up Cam]
        R_AUTH[Ring OAuth Server: oauth.ring.com]
        R_API[Amazon Vision API: api.amazonvision.com]
    end

    subgraph Tunnel & Edge
        NGROK[Cloudflare Tunnel / ngrok HTTPS]
    end

    subgraph RingCare Server (Python Flask)
        APP[Flask App: app.py]
        SCHED[APScheduler: Daily Check-in & 24h Token Refresh]
        WH[Webhook Queue & Worker: webhook_handler.py]
        RC[Ring Client: ring_client.py]
        DB[(SQLite DB: ringcare.db)]
    end

    subgraph Caregiver Dashboard (Browser)
        DASH[Responsive High-Contrast UI: index.html]
        SSE[Server-Sent Events: /api/events/stream]
        TTS[Web Speech API Text-to-Speech]
        CHIME_SYNTH[Web Audio Doorbell Chime]
    end

    %% OAuth & Account Linking
    R_AUTH -->|1. Auth Code| APP
    APP -->|2. Exchange & Unclaimed Tokens| R_AUTH
    APP -->|3. GET /v1/users/me & Match HMAC Nonce| R_API
    APP -->|4. App Integration Completed| R_API

    %% Webhook Pipeline
    RD -->|Motion / Doorbell Ring| R_API
    R_API -->|POST /webhooks/ring with X-Signature| NGROK
    NGROK --> APP
    APP -->|Verify HMAC-SHA256 & Dedupe| WH
    WH -->|Store Event & Check-in Time| DB
    WH -->|Push Live Alert| SSE
    SSE --> DASH
    DASH -->|Visual Flash Banner| DASH
    DASH -->|Spoken Announcement| TTS
    DASH -->|Two-Tone Audio| CHIME_SYNTH

    %% Check-in & Scheduler
    SCHED -->|Daily Morning Monitor| DB
    SCHED -.->|Inactivity Alert if no motion by 10 AM| SSE

    %% Two-way Chime
    DASH -->|POST /v1/devices/{id}/media/audio/playback| RC
    RC -->|Audio Chime Broadcast| RD
```

---

## ⚡ Key Ring Partner API Constraints Implemented

| Constraint | How RingCare Implements It |
| :--- | :--- |
| **Strict Server-to-Server** | All calls to `https://api.amazonvision.com` and `https://oauth.ring.com` occur inside `ring_client.py`. Browser JavaScript never makes direct calls (bypassing CORS restrictions). |
| **Bearer Authentication** | Headers formatted as `Authorization: Bearer <access_token>`. Refresh tokens are stored safely and never used as Bearer tokens. |
| **Token Lifecycle & Refresh** | Token endpoint (`POST /oauth/token`) receives codes and exchanges within 60s. Refreshing tokens invalidates the old refresh token; RingCare updates **both** `access_token` and `refresh_token` simultaneously. |
| **Reactive 401 Refresh** | If any Amazon Vision API call encounters `401 Unauthorized`, `RingClient` automatically triggers `refresh_access_token()`, updates SQLite, and retries the request once. |
| **Rate Limit Backoff** | Honors 100 req/s rate limit. On `429 Too Many Requests`, honors `Retry-After` headers and performs exponential backoff starting from 1.0s. |
| **HMAC Webhook Signatures** | Verifies `X-Signature: sha256=<hex>` against the raw request body bytes using `hmac.compare_digest`. Deduplicates requests on `meta.request_id` and responds with HTTP 200 within milliseconds. |
| **Account Link Nonce** | Computes `nonce = urlsafe_b64encode(HMAC_SHA256(key, f"{time}:{account_id}")).rstrip('=')` and verifies timestamp is within 600 seconds. Completes mandatory `POST` and `PATCH {status: "completed"}` app-integrations handshake. |

---

## 📋 The 4 Endpoints to Register in the Ring Developer Portal

When registering your RingCare integration in the [Amazon Developer Portal](https://developer.amazon.com/ring) (under the Ring Partner / Amazon Vision API section), register these 4 URLs pointing to your public HTTPS tunnel:

| Endpoint Name in Portal | Path on RingCare | Purpose |
| :--- | :--- | :--- |
| **1. Token Exchange URL** | `POST https://<your-domain>/oauth/token` | Receives authorization code from Ring OAuth flow, exchanges it within 60s, and stores tokens as unclaimed. |
| **2. Account Link URL** | `GET https://<your-domain>/ring/link` | Presents caregiver sign-in form, validates timestamp (≤ 600s), verifies HMAC nonce against Ring Account ID, and completes app integration. |
| **3. Webhook URL** | `POST https://<your-domain>/webhooks/ring` | Receives `motion_detected`, `button_press`, and device telemetry. Validates `X-Signature` HMAC and dedupes on `meta.request_id`. |
| **4. App Homepage URL** | `GET https://<your-domain>/` | Primary Caregiver Dashboard displaying live activity, check-in status, devices, and visitor log. |

> [!NOTE]
> **Staging First**: Ring requires testing all 4 URLs on the Ring Partner Staging environment (`https://oauth.stage.ring.com` and `https://api.stage.amazonvision.com`) before submitting for production certification.

---

## 🚀 Quickstart & Setup Guide

### 1. Prerequisites
- Python 3.10+ (tested on Python 3.11, 3.12, 3.14)
- Git & pip

### 2. Clone & Install Dependencies
```bash
cd ringcare
python -m pip install -r requirements.txt
```

### 3. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Edit `.env` with your Ring Partner credentials:
```env
RING_CLIENT_ID=your_ring_client_id
RING_CLIENT_SECRET=your_ring_client_secret
RING_HMAC_KEY=your_shared_hmac_secret_key

# Default is true for local testing without physical Ring hardware
DEMO_MODE=true
CHECKIN_TARGET_TIME=10:00
```

### 4. Run the RingCare Server
```bash
python app.py
```
RingCare will start on **http://127.0.0.1:5000**. Open your browser to access the dashboard!

---

## 🌐 Public Tunnel Setup (ngrok or Cloudflare Tunnel)

To receive real webhooks from the Ring Partner API servers during staging/production, expose your local port 5000 via an HTTPS tunnel:

### Option A: Cloudflare Tunnel (Recommended, No Timeouts)
```bash
cloudflared tunnel --url http://localhost:5000
```
Copy the assigned `https://<random-subdomain>.trycloudflare.com` URL.

### Option B: ngrok
```bash
ngrok http 5000
```
Copy the `https://<random-id>.ngrok-free.app` URL.

Use this base domain when entering the 4 endpoints into the Ring Developer Portal:
- Token Exchange: `https://<your-tunnel>/oauth/token`
- Account Link: `https://<your-tunnel>/ring/link`
- Webhook URL: `https://<your-tunnel>/webhooks/ring`
- App Homepage: `https://<your-tunnel>/`

---

## 🧪 Demo Mode & Webhook Simulation

RingCare comes with a complete **Demo Mode** (`DEMO_MODE=true`) that lets hackathon judges and developers test the full pipeline without real Ring devices:
1. **Interactive Simulator Panel in Dashboard UI:**
   - 🔔 **"Ring Doorbell (Front Porch)"**: Emits a simulated doorbell press, triggering a flashing visual banner, audio chime, and Web Speech TTS announcement.
   - 🚶 **"Human Motion (Hallway)"**: Emits human motion, satisfying morning care check-in.
   - 🐱 **"Pet Motion (Quiet Hours Test)"**: Demonstrates filtering of non-critical pet movement during quiet hours.
   - ⚠️ **"Inactivity Check"**: Tests the missed morning check-in emergency alert.
   - 📶 **"Toggle Camera Offline"**: Live updates device online/offline status.

2. **CLI Webhook Simulator (`simulate_webhook.py`):**
   Sends real HTTP POST requests with authentic **HMAC-SHA256 signatures** (`X-Signature`) to your local or remote server:
   ```bash
   # Simulate doorbell press
   python simulate_webhook.py doorbell

   # Simulate human motion in hallway
   python simulate_webhook.py human

   # Test invalid HMAC signature rejection (Expects HTTP 401)
   python simulate_webhook.py test-bad-sig

   # Test duplicate request deduplication (Expects HTTP 200 duplicate_ignored)
   python simulate_webhook.py test-dedupe
   ```

---

## 🧪 Running Unit Tests

Run the full pytest suite:
```bash
python -m pytest tests -v
```
All 13 unit tests verify:
- HMAC Nonce generation, constant-time `hmac.compare_digest`, and 600s expiration check.
- `X-Signature` HMAC-SHA256 verification and `meta.request_id` deduplication.
- Reactive `401 Unauthorized` token refresh and rate limit `429` backoff.

---

## ♿ Accessibility & Privacy Features

- **WCAG AAA High Contrast:** Toggle between deep slate mode and high-contrast black/yellow with high-visibility borders.
- **Dynamic Text Scaling:** One-click stepper for Normal, Large, and Extra Large text.
- **Visual Alert Banner:** High-visibility pulsing banner for deaf/hard-of-hearing family members.
- **Web Speech Text-to-Speech:** Automatically speaks who is at the door or where movement occurred.
- **Privacy-First Data Purge:** Right to be Forgotten — "Delete All My Stored Data" permanently wipes SQLite events and snapshots.

---

## 📄 License
MIT License. Built for the Ring Partner API Hackathon 2026.
