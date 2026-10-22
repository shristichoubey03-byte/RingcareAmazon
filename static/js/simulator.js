/**
 * RingCare Demo Simulator Helper
 * Allows hackathon judges & testers to trigger instant Ring events without physical hardware.
 */

let deviceOfflineState = false;

async function simulateEvent(eventType, subType, deviceId) {
  console.log(`[Simulator] Triggering ${eventType} (${subType}) on ${deviceId}`);

  try {
    const res = await fetch('/api/simulate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        type: eventType,
        sub_type: subType,
        device_id: deviceId
      })
    });

    const data = await res.json();
    console.log("[Simulator] Event queued successfully:", data);

    // Provide visual toast feedback in the demo panel
    showSimulatorToast(`Simulated ${eventType} (${subType || 'standard'}) queued & broadcast.`);
  } catch (err) {
    console.error("[Simulator] Error dispatching event:", err);
    showSimulatorToast("Error dispatching simulated event", true);
  }
}

async function simulateInactivityCheck() {
  console.log("[Simulator] Simulating missed morning check-in threshold...");
  try {
    // Purge morning events so check-in evaluation finds no motion
    await fetch('/api/privacy/delete-data', { method: 'POST' });
    // Trigger evaluation
    const res = await fetch('/api/checkin/run', { method: 'POST' });
    const data = await res.json();
    showSimulatorToast("⚠️ Inactivity simulated: Caregiver alert triggered!");
  } catch (err) {
    console.error("[Simulator] Error simulating inactivity:", err);
  }
}

async function toggleDeviceOfflineSim(deviceId) {
  deviceOfflineState = !deviceOfflineState;
  const eventType = deviceOfflineState ? 'device_offline' : 'device_online';
  await simulateEvent(eventType, null, deviceId);
  showSimulatorToast(`Device state toggled: ${deviceOfflineState ? 'OFFLINE' : 'ONLINE'}`);
}

async function setDemoTime(timeStr) {
  try {
    const res = await fetch('/api/demo/time', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ simulated_now: timeStr })
    });
    const data = await res.json();
    if (data.status === 'success') {
      const clockEl = document.getElementById('demo-clock-display');
      if (clockEl) {
        clockEl.textContent = data.effective_time;
      }
      if (data.checkin && typeof updateCheckinCardUI === 'function') {
        updateCheckinCardUI(data.checkin.status, data.checkin.message);
      }
      showSimulatorToast(`Demo clock set to: ${data.effective_time}`);
    }
  } catch (err) {
    console.error("[Simulator] Error setting demo time:", err);
    showSimulatorToast("Error setting demo clock", true);
  }
}

function showSimulatorToast(msg, isError = false) {
  let toast = document.getElementById('sim-toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'sim-toast';
    toast.style.cssText = `
      position: fixed;
      bottom: 24px;
      right: 24px;
      background: #1e293b;
      color: #38bdf8;
      border: 1px solid #38bdf8;
      padding: 12px 20px;
      border-radius: 8px;
      font-size: 0.85rem;
      font-weight: 600;
      box-shadow: 0 10px 25px rgba(0,0,0,0.5);
      z-index: 3000;
      transition: opacity 0.3s ease;
      display: flex;
      align-items: center;
      gap: 10px;
    `;
    document.body.appendChild(toast);
  }

  toast.style.borderColor = isError ? '#ef4444' : '#38bdf8';
  toast.style.color = isError ? '#ef4444' : '#38bdf8';
  toast.innerHTML = `<span>⚡</span> <span>${msg}</span>`;
  toast.style.opacity = '1';

  setTimeout(() => {
    toast.style.opacity = '0';
  }, 3500);
}
