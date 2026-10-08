import { api } from './api.js';

const DIRECTIONS = ['NORTH', 'SOUTH', 'EAST', 'WEST'];
const REFRESH_MS = 3000;
const $ = (id) => document.getElementById(id);
let refreshing = false;
let refreshingHistory = false;

function text(id, value) { $(id).textContent = String(value ?? '—'); }
function label(value) { return String(value ?? 'UNKNOWN').replaceAll('_', ' '); }
function timeLabel(iso) {
  if (!iso) return 'Time unavailable';
  const d = new Date(iso.endsWith('Z') || /[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
  return Number.isNaN(d.valueOf()) ? 'Time unavailable' : d.toLocaleString();
}
function connection(connected) {
  $('connection-dot').className = `status-dot ${connected ? 'online' : 'offline'}`;
  text('connection-text', connected ? 'API connected' : 'API disconnected');
}
function renderSignal(direction, state) {
  const box = document.querySelector(`[data-direction="${direction}"]`);
  const normalized = String(state ?? 'UNKNOWN').toUpperCase();
  box.dataset.state = ['RED', 'YELLOW', 'GREEN'].includes(normalized) ? normalized : 'UNKNOWN';
  text(`signal-${direction.toLowerCase()}`, normalized);
}
function renderQueues(queues) {
  const safe = Object.fromEntries(DIRECTIONS.map((direction) => {
    const count = Number(queues?.[direction]);
    return [direction, Number.isFinite(count) && count > 0 ? Math.floor(count) : 0];
  }));
  const total = Object.values(safe).reduce((sum, count) => sum + count, 0);
  text('total-vehicles', total);
  text('queue-total', total);
  const max = Math.max(...Object.values(safe), 1);
  $('queue-list').replaceChildren(...DIRECTIONS.map((direction) => {
    const wrapper = document.createElement('div'); wrapper.className = 'queue-item';
    const header = document.createElement('div'); header.className = 'queue-item-heading';
    const name = document.createElement('strong'); name.textContent = direction;
    const count = document.createElement('span'); count.textContent = `${safe[direction]} vehicle${safe[direction] === 1 ? '' : 's'}`;
    header.append(name, count);
    const track = document.createElement('div'); track.className = 'queue-track';
    const fill = document.createElement('div'); fill.className = 'queue-fill';
    fill.style.width = `${(safe[direction] / max) * 100}%`;
    track.append(fill); wrapper.append(header, track); return wrapper;
  }));
}
function renderStatus(status) {
  text('controller-status', label(status.controller_status));
  $('controller-status').dataset.health = status.controller_status ?? '';
  text('operating-mode', label(status.mode));
  text('current-phase', label(status.phase));
  text('transition-stage', `Stage: ${label(status.transition_stage)}`);
  text('emergency-indicator', status.emergency_active ? 'Emergency: ACTIVE' : 'Emergency: Inactive');
  $('emergency-indicator').classList.toggle('emergency', Boolean(status.emergency_active));
  DIRECTIONS.forEach((direction) => renderSignal(direction, status.actual_signals?.[direction]));
  renderQueues(status.queues);
  text('last-updated', `Updated ${new Date().toLocaleTimeString()}`);
}
function renderHistory(data) {
  const events = Array.isArray(data.items) ? data.items : [];
  if (!events.length) { $('activity-list').textContent = 'No recorded events yet.'; return; }
  $('activity-list').replaceChildren(...events.map((item) => {
    const row = document.createElement('div'); row.className = 'activity-row';
    const icon = document.createElement('span'); icon.className = 'event-symbol'; icon.textContent = '↗';
    const body = document.createElement('div'); body.className = 'event-body';
    const title = document.createElement('strong'); title.textContent = label(item.event_type);
    const description = document.createElement('p'); description.textContent = item.description ?? '';
    body.append(title, description);
    const date = document.createElement('time'); date.className = 'event-time';
    date.textContent = timeLabel(item.timestamp);
    row.append(icon, body, date); return row;
  }));
}
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  $('refresh-button').disabled = true;
  try {
    const data = await api.status();
    renderStatus(data);
    connection(true);
    $('error-banner').classList.add('hidden');
  } catch (error) {
    connection(false);
    $('error-banner').textContent = `Unable to refresh junction status: ${error.message}`;
    $('error-banner').classList.remove('hidden');
    // Retain previous signal states visually but mark them stale/unconfirmed.
    DIRECTIONS.forEach((direction) => renderSignal(direction, 'UNKNOWN'));
    text('last-updated', 'Live state unavailable');
  } finally { refreshing = false; $('refresh-button').disabled = false; }
  if (!refreshingHistory) {
    refreshingHistory = true;
    try { renderHistory(await api.history()); }
    catch { $('activity-list').textContent = 'History temporarily unavailable.'; }
    finally { refreshingHistory = false; }
  }
}
$('refresh-button').addEventListener('click', refresh);
refresh();
setInterval(() => { if (!document.hidden) refresh(); }, REFRESH_MS);

// Step 6B.1: Sensor event submission. The backend owns queue decisions.
let submittingSensorEvent = false;
const sensorAction = $('sim-action');
sensorAction.addEventListener('change', () => {
  $('sim-sequence').value = sensorAction.value === 'VEHICLE_CLEARED' ? '2' : '1';
});
$('vehicle-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (submittingSensorEvent) return;

  const resultBox = $('sim-result');
  const submitButton = $('sim-submit');
  const vehicleId = $('sim-id').value.trim();
  const sequence = Number($('sim-sequence').value);
  if (!vehicleId || !Number.isSafeInteger(sequence) || sequence < 1) {
    resultBox.textContent = 'Enter a vehicle ID and a valid positive sequence number.';
    resultBox.className = 'sim-result error';
    return;
  }

  const payload = {
    event_id: crypto.randomUUID(),
    junction_id: 'A',
    vehicle_id: vehicleId,
    direction: $('sim-direction').value,
    event_type: sensorAction.value,
    vehicle_type: $('sim-type').value,
    sequence_no: sequence,
    timestamp: new Date().toISOString(),
  };

  submittingSensorEvent = true;
  submitButton.disabled = true;
  submitButton.textContent = 'Submitting…';
  resultBox.className = 'sim-result hidden';
  try {
    await api.sensorEvent(payload);
    resultBox.textContent = `${payload.event_type.replaceAll('_', ' ')} recorded for ${vehicleId}. Event ID: ${payload.event_id}`;
    resultBox.className = 'sim-result';
    if (payload.event_type === 'VEHICLE_ARRIVED') {
      sensorAction.value = 'VEHICLE_CLEARED';
      $('sim-sequence').value = String(sequence + 1);
    }
    await refresh();
  } catch (error) {
    resultBox.textContent = `Sensor event rejected: ${error.message}`;
    resultBox.className = 'sim-result error';
  } finally {
    submittingSensorEvent = false;
    submitButton.disabled = false;
    submitButton.textContent = 'Submit sensor event';
  }
});


// Step 6B.2: Controller actions. The backend remains the source of truth.
let controllerBusy = false;
let lastCommandId = null;
let lastCommandState = null;
let controllerHealthy = false;

function updateControllerControls() {
  const ready = !controllerBusy && controllerHealthy;
  $('control-tick').disabled = !ready;
  $('control-manual').disabled = !ready;
  $('control-auto').disabled = controllerBusy;
  $('control-ack').disabled = controllerBusy || !lastCommandId || !lastCommandState;
  $('control-nack').disabled = controllerBusy || !lastCommandId || !lastCommandState;
  $('control-reconcile').disabled = controllerBusy || !$('control-confirm-red').checked;
}

function showControllerResult(message, error = false) {
  const el = $('control-result');
  el.textContent = message;
  el.className = error ? 'sim-result error' : 'sim-result';
}

async function controllerAction(action, successMessage) {
  if (controllerBusy) return;
  controllerBusy = true;
  updateControllerControls();
  try {
    const data = await action();
    if (data?.pending_command_id) lastCommandId = data.pending_command_id;
    if (data?.execution_status === 'COMMAND_TIMEOUT' || data?.controller_status === 'DEGRADED') {
      lastCommandId = null;
      lastCommandState = null;
    }
    showControllerResult(`${successMessage}: ${data?.execution_status ?? data?.message ?? data?.status ?? 'Accepted'}`);
    await refresh();
    return data;
  } catch (error) {
    showControllerResult(`Request failed: ${error.message}`, true);
    return null;
  } finally {
    controllerBusy = false;
    updateControllerControls();
  }
}

// Intercept the existing renderer without changing how confirmed signal colors are drawn.
const originalRenderStatus = renderStatus;
renderStatus = function (status) {
  originalRenderStatus(status);
  controllerHealthy = status.controller_status === 'ONLINE';
  text('control-stage', label(status.transition_stage));
  text('control-target', label(status.target_phase ?? 'NONE'));
  text('control-pending', lastCommandId ?? 'None recorded by this dashboard');
  updateControllerControls();
};

$('control-tick').addEventListener('click', () => controllerAction(async () => {
  const result = await api.tick('A');
  if (result.pending_command_id) {
    lastCommandId = result.pending_command_id;
    // Requested color is derived from the current stage only for simulator ACK.
    const stage = result.transition_stage ?? '';
    lastCommandState = stage.includes('YELLOW') && !stage.includes('HOLD') ? 'YELLOW'
      : stage.includes('WAIT_RED') ? 'RED'
      : stage.includes('WAIT_GREEN') ? 'GREEN' : null;
    $('control-command-preview').textContent = `Command ${lastCommandId} · requested ${lastCommandState ?? 'state unknown'}`;
  } else if (result.execution_status !== 'WAITING_FOR_ACK') {
    lastCommandId = null;
    lastCommandState = null;
    $('control-command-preview').textContent = 'No command pending from the latest tick.';
  }
  return result;
}, 'Tick executed'));

async function sendAck(status) {
  if (!lastCommandId || !lastCommandState) return;
  const id = lastCommandId;
  const state = lastCommandState;
  const result = await controllerAction(
    () => api.controllerAck({ junction_id: 'A', command_id: id, status, actual_state: state }),
    `Controller ${status}`,
  );
  if (result) {
    lastCommandId = null;
    lastCommandState = null;
    $('control-command-preview').textContent = 'Command resolved. Execute next tick.';
    updateControllerControls();
  }
}
$('control-ack').addEventListener('click', () => sendAck('ACK'));
$('control-nack').addEventListener('click', () => sendAck('NACK'));
$('control-manual').addEventListener('click', () => controllerAction(
  () => api.manualCommand('A', { command: 'MANUAL_GREEN_REQUEST', direction: $('control-direction').value }),
  'Manual request',
));
$('control-auto').addEventListener('click', () => controllerAction(
  () => api.manualCommand('A', { command: 'RETURN_TO_AUTOMATIC', direction: null }),
  'Automatic mode request',
));
$('control-confirm-red').addEventListener('change', updateControllerControls);
$('control-reconcile').addEventListener('click', async () => {
  if (!$('control-confirm-red').checked) return;
  const result = await controllerAction(() => api.reconcile('A'), 'Reconciliation');
  if (result) {
    lastCommandId = null;
    lastCommandState = null;
    $('control-confirm-red').checked = false;
    $('control-command-preview').textContent = 'No pending command.';
    updateControllerControls();
  }
});
updateControllerControls();
