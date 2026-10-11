// ---------------------------------------------------------------------------
// All communication with the FastAPI backend lives in this one file.
// Every function returns a Promise with plain JSON.
//
// VITE_USE_MOCK=true  -> answers come from mockData.js (no backend needed)
// VITE_USE_MOCK=false -> real HTTP calls to FastAPI (see README for the contract)
// ---------------------------------------------------------------------------
import * as mock from './mockData.js'

const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'

// In development, Vite forwards /api to FastAPI (see vite.config.js).
// For a deployed build you can set VITE_API_BASE_URL=https://your-fastapi-host
const BASE_URL = import.meta.env.VITE_API_BASE_URL || ''

async function request(path, options = {}) {
  const response = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!response.ok) {
    const text = await response.text()
    throw new Error(`${response.status} ${response.statusText}: ${text || path}`)
  }
  return response.json()
}

const get = (path) => request(path)
const post = (path, body) => request(path, { method: 'POST', body: JSON.stringify(body ?? {}) })

function query(params) {
  const clean = Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined && v !== ''))
  const qs = new URLSearchParams(clean).toString()
  return qs ? `?${qs}` : ''
}

export const api = {
  // Auth is done by FastAPI (OAuth + session cookie); React only asks who is signed in.
  getMe: () => (USE_MOCK ? mock.getMe() : get('/api/auth/me')), // 401 = not signed in
  login: () => (USE_MOCK ? mock.login() : (window.location.href = `${BASE_URL}/api/auth/login`)),
  logout: () => (USE_MOCK ? mock.logout() : post('/api/auth/logout')),

  // 1. Fleet health
  getFleetSummary: () => (USE_MOCK ? mock.getFleetSummary() : get('/api/fleet/summary')),

  // 4. Equipment
  getAssets: () => (USE_MOCK ? mock.getAssets() : get('/api/assets')),
  getAsset: (assetId) => (USE_MOCK ? mock.getAsset(assetId) : get(`/api/assets/${assetId}`)),

  // 2. Sensor trends  (range = "1h" | "24h" | "7d")
  getSensorSeries: (assetId, range) =>
    USE_MOCK ? mock.getSensorSeries(assetId, range) : get(`/api/assets/${assetId}/sensors${query({ range })}`),

  // 3. Maintenance performance
  getMaintenanceSummary: () => (USE_MOCK ? mock.getMaintenanceSummary() : get('/api/maintenance/summary')),
  getWorkOrders: (filters = {}) =>
    USE_MOCK ? mock.getWorkOrders(filters) : get(`/api/maintenance/work-orders${query(filters)}`),

  // 5. Critical exceptions / alarms
  getAlarms: (filters = {}) => (USE_MOCK ? mock.getAlarms(filters) : get(`/api/alarms${query(filters)}`)),
  acknowledgeAlarm: (alarmId) => (USE_MOCK ? mock.acknowledgeAlarm(alarmId) : post(`/api/alarms/${alarmId}/ack`)),

  // AI agent (FastAPI forwards this to the agent backend)
  askAgent: (message, context, history) =>
    USE_MOCK ? mock.askAgent(message, context) : post('/api/agent/chat', { message, context, history }),
}

export const isMockMode = USE_MOCK
