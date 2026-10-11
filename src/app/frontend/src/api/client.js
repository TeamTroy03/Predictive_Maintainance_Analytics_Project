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

// The session is an HttpOnly cookie set by FastAPI: the browser sends it by itself, JavaScript never sees it.
async function request(path, options = {}) {
  const response = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    ...options,
  })
  if (!response.ok) {
    // Session expired while using the app: reload, the app then shows the login page.
    if (response.status === 401 && !path.startsWith('/api/auth/')) window.location.reload()
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

// FastAPI does not serve these yet. With real data they fail with a clear message instead of showing mock
// data next to real KPIs. Once the endpoint exists, replace the pending(...) call with the real get/post.
const pending = (name, mockFn) =>
  USE_MOCK ? mockFn : () => Promise.reject(new Error(`${name}: not available yet, the backend endpoint is pending`))

export const api = {
  // Users are added by hand on the server. Login sets the session cookie; /auth/me says who is signed in (401 = nobody).
  getMe: () => (USE_MOCK ? mock.getMe() : get('/api/auth/me')),
  login: (email, password) => (USE_MOCK ? mock.login() : post('/api/auth/login', { email, password })),
  logout: () => (USE_MOCK ? mock.logout() : post('/api/auth/logout')),

  // 1. Fleet health
  getFleetSummary: () => (USE_MOCK ? mock.getFleetSummary() : get('/api/fleet/summary')),

  // 4. Equipment: { items, total, page, page_size }; filters: status, q, has_sensors, page, page_size
  getAssets: (filters = {}) => (USE_MOCK ? mock.getAssets(filters) : get(`/api/assets${query(filters)}`)),
  getAsset: (assetId) => (USE_MOCK ? mock.getAsset(assetId) : get(`/api/assets/${assetId}`)),

  // 2. Sensor trends  (range = "24h" | "7d" | "30d" | "all", counted back from the asset's last reading)
  getSensorSeries: (assetId, range) =>
    USE_MOCK ? mock.getSensorSeries(assetId, range) : get(`/api/assets/${assetId}/sensors${query({ range })}`),

  // 3. Maintenance performance
  getMaintenanceSummary: () => (USE_MOCK ? mock.getMaintenanceSummary() : get('/api/maintenance/summary')),
  getWorkOrders: (filters = {}) =>
    USE_MOCK ? mock.getWorkOrders(filters) : get(`/api/maintenance/work-orders${query(filters)}`),

  // 5. Critical exceptions / alarms (Gold keeps no acknowledgements, so there is nothing to acknowledge)
  getAlarms: (filters = {}) => (USE_MOCK ? mock.getAlarms(filters) : get(`/api/alarms${query(filters)}`)),
  acknowledgeAlarm: pending('Alarm acknowledgement', mock.acknowledgeAlarm),

  // AI agent (needs the two-step /assistant/query + /assistant/approve flow)
  askAgent: pending('AI assistant', mock.askAgent),
}

export const isMockMode = USE_MOCK
