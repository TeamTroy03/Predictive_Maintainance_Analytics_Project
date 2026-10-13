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
async function request(path, { timeoutMs, ...options } = {}) {
  const controller = new AbortController()
  const timer = timeoutMs ? setTimeout(() => controller.abort(), timeoutMs) : null
  try {
    const response = await fetch(`${BASE_URL}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      signal: controller.signal,
      ...options,
    })
    if (!response.ok) {
      // Session expired while using the app: reload, the app then shows the login page.
      if (response.status === 401 && !path.startsWith('/api/auth/')) window.location.reload()
      const text = await response.text()
      throw new Error(`${response.status} ${response.statusText}: ${text || path}`, { cause: parseApiError(text) })
    }
    return await response.json()
  } catch (err) {
    if (err.name === 'AbortError') throw new Error(`Request timed out after ${Math.round(timeoutMs / 1000)} s`)
    throw err
  } finally {
    if (timer) clearTimeout(timer)
  }
}

// FastAPI errors look like { error: { code, message, correlation_id, details } }
function parseApiError(text) {
  try {
    return JSON.parse(text).error ?? undefined
  } catch {
    return undefined
  }
}

// GET answers are kept for 10 minutes so switching windows back and forth doesn't re-query the backend each time
// (and two components asking for the same path share one request). Failures are never kept, so retries really retry.
// ponytail: fixed TTL, no manual invalidation; the data only changes on pipeline refreshes.
const CACHE_TTL_MS = 10 * 60_000
const cache = new Map() // path -> { at, promise }
function get(path) {
  if (path.startsWith('/api/auth/')) return request(path)
  const hit = cache.get(path)
  if (hit && Date.now() - hit.at < CACHE_TTL_MS) return hit.promise
  const promise = request(path)
  cache.set(path, { at: Date.now(), promise })
  promise.catch(() => cache.get(path)?.promise === promise && cache.delete(path))
  return promise
}
const post = (path, body, timeoutMs) => {
  // A different user may sign in next: never show them the previous user's data
  if (path.startsWith('/api/auth/')) cache.clear()
  return request(path, { method: 'POST', body: JSON.stringify(body ?? {}), timeoutMs })
}

// Each assistant call makes several sequential LLM calls (tens of seconds, minutes in the worst case).
const ASSISTANT_TIMEOUT_MS = 300_000

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

  // Governed KPIs grouped, e.g. { kpis: ['availability'], group_by: ['equipment_type'] } -> { rows, ... } (no demo data)
  queryKpis: (body) => (USE_MOCK ? Promise.resolve({ rows: [] }) : post('/api/analytics/query', body)),

  // 4. Equipment: { items, total, page, page_size }; filters: status, q, line, sort, has_sensors, page, page_size
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

  // AI agent, two steps: askAgent -> { status: 'awaiting_approval', run_id, plan } (or already 'answered'),
  // then approveAgent -> { status: 'answered', result: { answer, evidence, grounded, limitations, filters, source_measure } }
  // conversationId: questions with the same ID share a memory (follow-ups like "and for pumps?")
  askAgent: (question, conversationId) =>
    USE_MOCK
      ? mock.askAgent(question)
      : post('/api/assistant/query', { question, conversation_id: conversationId }, ASSISTANT_TIMEOUT_MS),
  approveAgent: (runId, approved) =>
    USE_MOCK
      ? mock.approveAgent(runId, approved)
      : post('/api/assistant/approve', { run_id: runId, approved }, ASSISTANT_TIMEOUT_MS),
}

export const isMockMode = USE_MOCK
