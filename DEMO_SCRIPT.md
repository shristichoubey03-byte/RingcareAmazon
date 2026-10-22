# 🎬 RingCare: 60-90 Second Hackathon Demo Script

**Speaker Persona:** Passionate full-stack engineer demonstrating RingCare for elderly care & accessibility.  
**Screen Setup:** Browser open at `http://127.0.0.1:5000` (Caregiver Dashboard) with audio unmuted. Optional second window showing terminal for `simulate_webhook.py`.

---

### [00:00 - 00:15] The Hook & Project Idea
- **Visual:** Camera on speaker, or screen recording on the RingCare Dashboard header showing *"RingCare: Elderly Care & Accessibility Monitor"*.
- **Voiceover:**
  > *"Every day, millions of families worry about an aging parent or relative living alone. Did grandma wake up on time? Did she hear the doorbell? Who just came to her door?
  > Meet **RingCare** — an assistive caregiving platform built directly on the **Ring Partner API (Amazon Vision API)**."*

---

### [00:15 - 00:35] Feature 1: Deaf & Hard-of-Hearing Accessibility Alert
- **Action:** Click the simulator button **"🔔 Ring Doorbell (Front Porch)"** (or run `python simulate_webhook.py doorbell`).
- **Visual:** The screen immediately displays the pulsing red visual alert banner across the top: *"DOORBELL PRESSED AT FRONT PORCH"*. The Web Audio doorbell chime plays, and browser Text-to-Speech announces: *"Accessibility Alert: Someone is at the door. Doorbell pressed at Front Door Video Doorbell Pro 2."*
- **Voiceover:**
  > *"For a deaf or hard-of-hearing senior, missing the doorbell is dangerous. With our server-to-server webhook pipeline and Server-Sent Events, RingCare triggers a high-visibility flashing banner and speaks the alert aloud through Text-to-Speech the second a visitor presses the Ring doorbell."*

---

### [00:35 - 00:50] Feature 2: Daily Morning Care Check-in
- **Visual:** Point cursor to the **Daily Care Check-in** card.
- **Action:** Click **"⚠️ Inactivity Check"** on the simulator panel.
- **Visual:** The status card shifts to a pulsing red warning: *"Needs Attention: Inactivity Alert"*, and a notification is dispatched. Then click **"🚶 Human Motion (Hallway)"** — the card updates to green: *"Checked In: Safe & Active"*.
- **Voiceover:**
  > *"Next: Care Check-in. If no motion or door activity is detected before 10:00 AM, RingCare flags the day as 'Needs Attention' and dispatches emergency caregiver alerts. As soon as morning motion is detected, the status turns green, giving family members complete peace of mind."*

---

### [00:50 - 01:10] Feature 3: Smart Visitor Log & Chime Dispatcher
- **Visual:** Scroll down to the **Visitor Log**. Show the camera snapshot preview.
- **Action:** Click **"✓ Known"** or **"✕ Unknown"** on a recent visitor event, type *"Nurse Martha - Weekly Checkup"* into notes, and click **Save**.
- **Action:** Click **"💊 Time for morning medication"** under the Chime card.
- **Visual:** Chime feedback appears: *"Played: Grandma, time for your morning medication"*.
- **Voiceover:**
  > *"In our Visitor Log, caregivers can review snapshot captures, tag visitors as 'Known' or 'Unknown', and leave care notes. And with the Ring Audio Playback API, caregivers can broadcast gentle voice reminders through the Ring chime with a single click."*

---

### [01:10 - 01:25] Feature 4: WCAG AAA Accessibility & Privacy
- **Action:** Click the **"◐ High Contrast"** button in the top navigation (the UI toggles into high-contrast black/yellow mode). Click **"A++"** to show extra-large font scaling.
- **Action:** Click **"🗑️ Purge My Data"** to demonstrate privacy controls.
- **Voiceover:**
  > *"RingCare is built for everyone: WCAG AAA high-contrast mode, dynamic text scaling, and strict privacy controls with full right-to-be-forgotten data purging. All communication with Amazon Vision and oauth.ring.com is strictly server-to-server with HMAC-SHA256 verification and automatic 401 token refresh."*

---

### [01:25 - 01:30] Wrap-up & Call to Action
- **Visual:** Zoom out to full dashboard.
- **Voiceover:**
  > *"RingCare: Turning everyday Ring doorbells into a lifeline of independence and safety for our loved ones. Thank you!"*
