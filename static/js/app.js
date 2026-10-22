/**
 * RingCare Caregiver Dashboard Controller
 * Features:
 * - Server-Sent Events (SSE) real-time event pipeline
 * - Web Speech API Text-to-Speech (TTS) for deaf/hard-of-hearing assistance
 * - Web Audio API synthesized doorbell chime
 * - Dynamic font sizing & WCAG AAA High Contrast mode persistence
 * - Visitor classification (Known vs. Unknown) & Caregiver notes
 * - Ring audio playback / chime dispatcher
 */

// Application State
const state = {
  ttsEnabled: localStorage.getItem('ringcare_tts') !== 'false',
  fontSize: localStorage.getItem('ringcare_font_size') || 'normal',
  highContrast: localStorage.getItem('ringcare_high_contrast') === 'true',
  lastAlertSpeech: '',
  audioCtx: null
};

/* ==============================================================================
   Snapshot Fallback & Unified Timestamp Helpers
   ============================================================================== */

const FALLBACK_SVG = 'data:image/svg+xml;utf8,' + encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180" viewBox="0 0 320 180">' +
  '<rect width="100%" height="100%" fill="#1f293d"/>' +
  '<circle cx="160" cy="80" r="32" fill="#334155"/>' +
  '<circle cx="160" cy="80" r="16" fill="#64748b"/>' +
  '<rect x="135" y="45" width="20" height="8" rx="2" fill="#64748b"/>' +
  '<text x="160" y="135" font-family="-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif" font-size="14" fill="#94a3b8" text-anchor="middle" font-weight="600">SNAPSHOT UNAVAILABLE</text>' +
  '<text x="160" y="155" font-family="-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif" font-size="11" fill="#64748b" text-anchor="middle">Camera Offline / Standby</text>' +
  '</svg>'
);

function onSnapshotError(img) {
  if (!img) return;
  img.onerror = null;
  img.src = FALLBACK_SVG;
}
window.onSnapshotError = onSnapshotError;

function formatTimestamp(dtOrStr) {
  if (!dtOrStr) return '';
  let d;
  if (dtOrStr instanceof Date) {
    d = dtOrStr;
  } else {
    const clean = String(dtOrStr).replace(' ', 'T');
    d = new Date(clean);
    if (isNaN(d.getTime())) return String(dtOrStr);
  }
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  let hours = d.getHours();
  const minutes = String(d.getMinutes()).padStart(2, '0');
  const seconds = String(d.getSeconds()).padStart(2, '0');
  const ampm = hours >= 12 ? 'PM' : 'AM';
  hours = hours % 12;
  hours = hours ? hours : 12;
  const strHours = String(hours).padStart(2, '0');
  return `${year}-${month}-${day} ${strHours}:${minutes}:${seconds} ${ampm}`;
}
window.formatTimestamp = formatTimestamp;

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
  initAccessibilityPreferences();
  initSSE();
  initKeyboardListeners();
});

/* ==============================================================================
   Accessibility & Voice (TTS) Management
   ============================================================================== */

function initAccessibilityPreferences() {
  setFontSize(state.fontSize, false);
  if (state.highContrast) {
    document.body.classList.add('theme-high-contrast');
    const btn = document.getElementById('btn-high-contrast');
    if (btn) btn.setAttribute('aria-pressed', 'true');
  }
  updateTTSButtonUI();
}

function setFontSize(size, save = true) {
  state.fontSize = size;
  document.body.classList.remove('font-normal', 'font-large', 'font-xlarge');
  document.body.classList.add(`font-${size}`);

  document.querySelectorAll('.toolbar-item .btn-tool').forEach(btn => btn.classList.remove('active'));
  const activeBtn = document.getElementById(`btn-font-${size}`);
  if (activeBtn) activeBtn.classList.add('active');

  if (save) localStorage.setItem('ringcare_font_size', size);
}

function toggleHighContrast() {
  state.highContrast = !state.highContrast;
  document.body.classList.toggle('theme-high-contrast', state.highContrast);
  const btn = document.getElementById('btn-high-contrast');
  if (btn) btn.setAttribute('aria-pressed', state.highContrast.toString());
  localStorage.setItem('ringcare_high_contrast', state.highContrast.toString());
}

function toggleTTS() {
  state.ttsEnabled = !state.ttsEnabled;
  localStorage.setItem('ringcare_tts', state.ttsEnabled.toString());
  updateTTSButtonUI();

  if (state.ttsEnabled) {
    speakText("Voice alerts enabled for RingCare.");
  } else {
    if (window.speechSynthesis) window.speechSynthesis.cancel();
  }
}

function updateTTSButtonUI() {
  const btn = document.getElementById('btn-tts-toggle');
  if (!btn) return;
  if (state.ttsEnabled) {
    btn.classList.add('active');
    btn.setAttribute('aria-pressed', 'true');
    btn.innerHTML = '<span>🔊</span> Voice Alerts: ON';
  } else {
    btn.classList.remove('active');
    btn.setAttribute('aria-pressed', 'false');
    btn.innerHTML = '<span>🔇</span> Voice Alerts: OFF';
  }
}

function speakText(text) {
  if (!state.ttsEnabled || !('speechSynthesis' in window)) return;
  window.speechSynthesis.cancel(); // Stop any pending speech

  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = 0.95; // Slightly slower for clarity
  utterance.pitch = 1.0;

  // Choose clear English voice if available
  const voices = window.speechSynthesis.getVoices();
  const naturalVoice = voices.find(v => v.lang.startsWith('en') && (v.name.includes('Google') || v.name.includes('Natural') || v.name.includes('Samantha')));
  if (naturalVoice) utterance.voice = naturalVoice;

  window.speechSynthesis.speak(utterance);
}

function playSynthesizedChime(type = 'bell') {
  try {
    if (!state.audioCtx) {
      state.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    const ctx = state.audioCtx;
    if (ctx.state === 'suspended') ctx.resume();

    const now = ctx.currentTime;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();

    osc.connect(gain);
    gain.connect(ctx.destination);

    if (type === 'alert') {
      // Urgent two-tone alert
      osc.type = 'sawtooth';
      osc.frequency.setValueAtTime(587.33, now); // D5
      osc.frequency.setValueAtTime(880, now + 0.15); // A5
      gain.gain.setValueAtTime(0.3, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.5);
      osc.start(now);
      osc.stop(now + 0.5);
    } else {
      // Pleasant rising doorbell chime
      osc.type = 'sine';
      osc.frequency.setValueAtTime(523.25, now); // C5
      osc.frequency.setValueAtTime(659.25, now + 0.2); // E5
      gain.gain.setValueAtTime(0.4, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.8);
      osc.start(now);
      osc.stop(now + 0.8);
    }
  } catch (e) {
    console.warn("Web Audio chime could not play:", e);
  }
}

function announceScreenReader(message) {
  const el = document.getElementById('sr-announcements');
  if (el) {
    el.textContent = '';
    setTimeout(() => { el.textContent = message; }, 50);
  }
}

/* ==============================================================================
   Server-Sent Events (SSE) Real-Time Pipeline
   ============================================================================== */

function initSSE() {
  if (!('EventSource' in window)) {
    console.warn("SSE not supported by browser. Falling back to periodic refresh.");
    return;
  }

  const evtSource = new EventSource('/api/events/stream');

  evtSource.addEventListener('message', (e) => {
    try {
      const data = JSON.parse(e.data);
      handleLiveEvent(data);
    } catch (err) {
      console.error("Error parsing SSE event:", err);
    }
  });

  evtSource.addEventListener('error', (err) => {
    console.warn("SSE connection error; reconnecting automatically...");
  });
}

function handleLiveEvent(data) {
  console.log("[Live Event Received]", data);

  // 1. Accessibility Event Alert (Doorbell press or Human motion)
  if (data.type === 'event') {
    if (data.is_accessible_alert) {
      triggerVisualAlert(
        data.alert_banner_text,
        data.device_name,
        data.event_type,
        data.speech_text,
        data.snapshot_url,
        data.received_offline
      );
      playSynthesizedChime(data.event_type === 'button_press' ? 'bell' : 'alert');
      speakText(data.speech_text);
    }

    // Prepend event card to activity feed
    prependEventCard(data);
    announceScreenReader(data.speech_text || data.alert_banner_text);
  }

  // 2. Check-in Updates
  if (data.type === 'checkin_alert') {
    updateCheckinCardUI('needs_attention', data.message);
    triggerVisualAlert("CARE CHECK-IN ALERT: NO ACTIVITY DETECTED", "Grandma's Residence", "alert", data.message, null, false);
    playSynthesizedChime('alert');
    speakText(data.message);
  } else if (data.type === 'checkin_update') {
    updateCheckinCardUI(data.status, data.message);
  }

  // 3. Device Online/Offline Telemetry
  if (data.type === 'device_status') {
    updateDeviceBadge(data.device_id, data.online);
    announceScreenReader(data.message);
  }

  // 4. Data Purge
  if (data.type === 'data_purged') {
    const feed = document.getElementById('events-feed');
    if (feed) feed.innerHTML = '<p style="padding: 20px; color: var(--text-muted); text-align: center;">No activity recorded yet.</p>';
    updateCheckinCardUI('waiting', 'Activity logs cleared.');
  }
}

/* ==============================================================================
   Visual Alert Banner (Deaf / Hard-of-Hearing)
   ============================================================================== */

function triggerVisualAlert(title, deviceName, type, speechText, snapshotUrl, receivedOffline) {
  const banner = document.getElementById('visual-alert-banner');
  const titleEl = document.getElementById('alert-banner-title');
  const subEl = document.getElementById('alert-banner-sub');
  const iconEl = document.getElementById('alert-banner-icon');
  const snapImg = document.getElementById('alert-banner-snapshot');
  const offlineWarn = document.getElementById('alert-banner-offline-warn');

  if (!banner || !titleEl) return;

  state.lastAlertSpeech = speechText || title;
  titleEl.textContent = title;
  subEl.textContent = `Location: ${deviceName} • Visual alert active • ${receivedOffline ? 'Device offline' : 'Snapshot captured'}.`;
  iconEl.textContent = type === 'button_press' ? '🔔' : type === 'alert' ? '⚠️' : '🚶';

  if (snapImg) {
    if (snapshotUrl) {
      snapImg.src = snapshotUrl;
    } else {
      snapImg.src = FALLBACK_SVG;
    }
  }

  if (offlineWarn) {
    if (receivedOffline) {
      offlineWarn.classList.remove('hidden');
    } else {
      offlineWarn.classList.add('hidden');
    }
  }

  banner.classList.remove('hidden');

  const replayBtn = document.getElementById('btn-replay-tts');
  if (replayBtn) {
    replayBtn.onclick = () => speakText(state.lastAlertSpeech);
  }
}

function dismissVisualAlert() {
  const banner = document.getElementById('visual-alert-banner');
  if (banner) banner.classList.add('hidden');
}

/* ==============================================================================
   Activity Feed & Visitor Log
   ============================================================================== */

function prependEventCard(ev) {
  const feed = document.getElementById('events-feed');
  if (!feed) return;

  const card = document.createElement('article');
  card.className = `event-card alert-border new-pulse`;
  card.id = `event-card-${ev.event_id}`;
  card.setAttribute('data-type', ev.event_type);
  card.setAttribute('data-sub', ev.sub_type || '');
  card.setAttribute('data-visitor', ev.visitor_tag || 'unassigned');

  const typeLabel = ev.event_type === 'button_press' ? '🔔 Doorbell Press' :
                    ev.sub_type === 'human' ? '🚶 Human Motion' :
                    `📡 ${ev.sub_type || ev.event_type}`;

  const isOffline = Boolean(ev.received_offline);
  const snapshotUrl = ev.snapshot_url || (isOffline ? FALLBACK_SVG : `/static/snapshots/placeholder_doorbell.jpg`);
  const timeFormatted = ev.formatted_time || formatTimestamp(ev.timestamp);

  const badgeHtml = isOffline
    ? '<span class="snapshot-badge offline">⚠️ Offline</span>'
    : '<span class="snapshot-badge">📸 Snapshot</span>';

  const offlineNoticeHtml = isOffline
    ? '<div class="offline-event-notice"><span class="badge-offline-warning">⚠️ Received while device offline</span></div>'
    : '';

  card.innerHTML = `
    <div class="event-thumbnail-box">
      <img 
        src="${snapshotUrl}" 
        alt="Camera snapshot for ${ev.device_name}" 
        class="event-snapshot" 
        onclick="openSnapshotModal(this.src, '${ev.device_name}', '${timeFormatted}')"
        tabindex="0"
        role="button"
        aria-label="Enlarge camera snapshot"
        onerror="onSnapshotError(this)"
      >
      ${badgeHtml}
    </div>
    <div class="event-content">
      <div class="event-headline">
        <span class="event-type-badge ${ev.event_type}">${typeLabel}</span>
        <time class="event-timestamp" datetime="${ev.timestamp}">${timeFormatted}</time>
      </div>
      ${offlineNoticeHtml}
      <h3 class="event-title">${ev.device_name}</h3>
      <div class="visitor-tag-group">
        <span class="visitor-label">Visitor Classification:</span>
        <div class="tag-buttons" role="group" aria-label="Classify visitor">
          <button 
            class="btn-tag btn-tag-known ${ev.visitor_tag === 'known' ? 'active' : ''}"
            onclick="tagVisitor(${ev.event_id}, 'known', this)"
          >✓ Known</button>
          <button 
            class="btn-tag btn-tag-unknown ${ev.visitor_tag === 'unknown' ? 'active' : ''}"
            onclick="tagVisitor(${ev.event_id}, 'unknown', this)"
          >✕ Unknown</button>
        </div>
      </div>
      <div class="visitor-notes-row">
        <input 
          type="text" 
          class="notes-input" 
          id="notes-${ev.event_id}" 
          placeholder="Add caregiver note..."
          value=""
          onblur="saveEventNotes(${ev.event_id})"
        >
        <button class="btn btn-tool btn-save-note" onclick="saveEventNotes(${ev.event_id})">Save</button>
      </div>
    </div>
  `;

  feed.prepend(card);
}

async function tagVisitor(eventId, tag, btn) {
  try {
    const res = await fetch(`/api/events/${eventId}/visitor`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ visitor_tag: tag })
    });
    if (res.ok) {
      const parent = btn.parentElement;
      parent.querySelectorAll('.btn-tag').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      const card = document.getElementById(`event-card-${eventId}`);
      if (card) card.setAttribute('data-visitor', tag);

      announceScreenReader(`Visitor tagged as ${tag}.`);
    }
  } catch (err) {
    console.error("Failed to tag visitor:", err);
  }
}

async function saveEventNotes(eventId) {
  const input = document.getElementById(`notes-${eventId}`);
  if (!input) return;
  const note = input.value.trim();

  try {
    await fetch(`/api/events/${eventId}/visitor`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ visitor_notes: note })
    });
    announceScreenReader("Caregiver note saved.");
  } catch (err) {
    console.error("Failed to save note:", err);
  }
}

function filterEvents(type, tabBtn) {
  document.querySelectorAll('.filter-tabs .tab-btn').forEach(b => {
    b.classList.remove('active');
    b.setAttribute('aria-selected', 'false');
  });
  tabBtn.classList.add('active');
  tabBtn.setAttribute('aria-selected', 'true');

  const cards = document.querySelectorAll('.events-container .event-card');
  cards.forEach(card => {
    const cardType = card.getAttribute('data-type');
    const cardVisitor = card.getAttribute('data-visitor');

    if (type === 'all') {
      card.style.display = 'flex';
    } else if (type === 'unknown') {
      card.style.display = cardVisitor === 'unknown' ? 'flex' : 'none';
    } else {
      card.style.display = cardType === type ? 'flex' : 'none';
    }
  });
}

/* ==============================================================================
   Care Check-in Status Card
   ============================================================================== */

function updateCheckinCardUI(status, message) {
  const box = document.getElementById('checkin-status-box');
  if (!box) return;

  if (status === 'checked_in') {
    box.innerHTML = `
      <div class="status-icon status-success">✓</div>
      <div class="status-info">
        <span class="status-state text-success">Checked In: Safe & Active</span>
        <p class="status-desc" id="checkin-desc">${message}</p>
      </div>
    `;
  } else if (status === 'needs_attention') {
    box.innerHTML = `
      <div class="status-icon status-danger">⚠️</div>
      <div class="status-info">
        <span class="status-state text-danger">Needs Attention: Inactivity Alert</span>
        <p class="status-desc" id="checkin-desc">${message}</p>
      </div>
    `;
  } else if (status === 'waiting') {
    box.innerHTML = `
      <div class="status-icon status-pending">⏳</div>
      <div class="status-info">
        <span class="status-state text-warning">Waiting for Morning Activity</span>
        <p class="status-desc" id="checkin-desc">${message}</p>
      </div>
    `;
  } else {
    box.innerHTML = `
      <div class="status-icon status-pending">⏳</div>
      <div class="status-info">
        <span class="status-state text-warning">Pending Morning Movement</span>
        <p class="status-desc" id="checkin-desc">${message}</p>
      </div>
    `;
  }
}

async function triggerCheckinEvaluation() {
  const btn = document.getElementById('btn-run-checkin');
  btn.disabled = true;
  btn.innerText = "Evaluating...";
  try {
    const res = await fetch('/api/checkin/run', { method: 'POST' });
    const data = await res.json();
    updateCheckinCardUI(data.status, data.message);
  } catch (err) {
    console.error("Checkin evaluation error:", err);
  } finally {
    btn.disabled = false;
    btn.innerText = "Evaluate Check-in Now";
  }
}

/* ==============================================================================
   Device Online/Offline & Chime Dispatcher
   ============================================================================== */

function updateDeviceBadge(deviceId, isOnline) {
  const badge = document.getElementById(`status-badge-${deviceId}`);
  if (badge) {
    badge.className = `device-online-badge ${isOnline ? 'online' : 'offline'}`;
    badge.textContent = isOnline ? 'Online' : 'Offline';
  }
}

async function sendChimePreset(deviceId, message) {
  showChimeFeedback("Dispatching audio to Ring chime...");
  try {
    const res = await fetch(`/api/devices/${deviceId}/chime`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ audio_ref: 'chime_tone_bell', message: message })
    });
    if (res.ok) {
      showChimeFeedback(`✓ Played: "${message}"`);
      playSynthesizedChime('bell');
    }
  } catch (err) {
    showChimeFeedback("Error triggering chime.");
  }
}

async function sendCustomChime(deviceId) {
  const input = document.getElementById('custom-chime-input');
  const msg = input.value.trim();
  if (!msg) return;
  await sendChimePreset(deviceId, msg);
  input.value = '';
}

function showChimeFeedback(msg) {
  const fb = document.getElementById('chime-status-msg');
  if (fb) {
    fb.textContent = msg;
    setTimeout(() => { if (fb.textContent === msg) fb.textContent = ''; }, 4000);
  }
}

/* Chime Modal */
function openChimeModal(deviceId, deviceName) {
  document.getElementById('modal-device-id').value = deviceId;
  document.getElementById('modal-device-name').textContent = deviceName;
  document.getElementById('chime-modal').classList.remove('hidden');
}

function closeChimeModal() {
  document.getElementById('chime-modal').classList.add('hidden');
}

function selectModalPreset(text) {
  document.getElementById('modal-chime-text').value = text;
}

function submitModalChime() {
  const deviceId = document.getElementById('modal-device-id').value;
  const text = document.getElementById('modal-chime-text').value.trim() || 'Caregiver notification';
  sendChimePreset(deviceId, text);
  closeChimeModal();
}

/* Snapshot Modal */
function openSnapshotModal(src, deviceName, timestamp) {
  const img = document.getElementById('modal-img');
  if (img) img.src = src;
  const meta = document.getElementById('modal-meta-text');
  if (meta) {
    const formatted = formatTimestamp(timestamp);
    meta.textContent = `Camera: ${deviceName} | Timestamp: ${formatted || timestamp}`;
  }
  const modal = document.getElementById('snapshot-modal');
  if (modal) modal.classList.remove('hidden');
}

function closeSnapshotModal() {
  document.getElementById('snapshot-modal').classList.add('hidden');
}

/* Keyboard Listener for Accessibility */
function initKeyboardListeners() {
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      dismissVisualAlert();
      closeSnapshotModal();
      closeChimeModal();
    }
  });
}

/* Privacy Quick Purge */
async function quickPurgeData() {
  if (!confirm("Are you sure you want to permanently delete all activity and visitor history?")) return;
  try {
    const res = await fetch('/api/privacy/delete-data', { method: 'POST' });
    const data = await res.json();
    alert(`Privacy Purge Complete: Removed ${data.details.events_deleted} events.`);
  } catch (err) {
    alert("Error purging data.");
  }
}
