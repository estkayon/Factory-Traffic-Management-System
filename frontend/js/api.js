/** Same-origin, read-only API client. Never assumes a traffic state locally. */
const BASE = '/api';

async function request(path, options = {}) {
  const response = await fetch(`${BASE}${path}`, {
    method: options.method ?? 'GET',
    headers: { Accept: 'application/json', ...(options.body ? { 'Content-Type': 'application/json' } : {}) },
    ...(options.body ? { body: JSON.stringify(options.body) } : {}),
    cache: 'no-store',
  });
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const payload = await response.json();
      if (typeof payload.detail === 'string') detail = payload.detail;
      else if (Array.isArray(payload.detail)) detail = payload.detail.map((e) => `${e.loc?.join('.') ?? 'field'}: ${e.msg}`).join('; ');
    } catch { /* Keep HTTP fallback. */ }
    throw new Error(detail);
  }
  return response.json();
}

export const api = Object.freeze({
  status: (junctionId = 'A') => request(`/junctions/${encodeURIComponent(junctionId)}/status`),
  history: (junctionId = 'A', limit = 8) => request(`/junctions/${encodeURIComponent(junctionId)}/history?limit=${limit}`),
  sensorEvent: (payload) => request('/sensor-events', { method: 'POST', body: payload }),
  tick: (junctionId = 'A') => request(`/junctions/${encodeURIComponent(junctionId)}/tick`, { method: 'POST' }),
  manualCommand: (junctionId, payload) => request(`/junctions/${encodeURIComponent(junctionId)}/commands`, { method: 'POST', body: payload }),
  controllerAck: (payload) => request('/controller-events', { method: 'POST', body: payload }),
  reconcile: (junctionId = 'A') => request('/controller-events/reconcile', { method: 'POST', body: { junction_id: junctionId, confirmed_state: 'ALL_RED' } }),
});
