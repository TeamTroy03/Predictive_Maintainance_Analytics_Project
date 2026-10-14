// ---------------------------------------------------------------------------
// Demo data so the UI works before the backend is ready.
// The shapes returned here are EXACTLY what FastAPI should return (see README).
// ---------------------------------------------------------------------------

const delay = (ms = 250) => new Promise((resolve) => setTimeout(resolve, ms))
const HOUR = 3600 * 1000
const DAY = 24 * HOUR
const now = Date.now()
const iso = (t) => new Date(t).toISOString()

// Small deterministic random generator so charts look the same on every reload
function seededRandom(seed) {
  let s = seed
  return () => {
    s = (s * 9301 + 49297) % 233280
    return s / 233280
  }
}

// ---------------- Assets ----------------
const ASSETS = [
  { id: 'PUMP-204B', name: 'High Pressure Feed Pump', type: 'Pump', line: 'Line 2', status: 'critical', health_score: 42, run_hours: 28140, manufacturer: 'Sulzer HPH-150', installed: '2021-05-12' },
  { id: 'COMP-109', name: 'Rotary Screw Compressor', type: 'Compressor', line: 'Line 1', status: 'warning', health_score: 68, run_hours: 19820, manufacturer: 'Atlas Copco GA-90', installed: '2022-02-03' },
  { id: 'TURB-004', name: 'Steam Turbine Generator', type: 'Turbine', line: 'Utilities', status: 'operational', health_score: 96, run_hours: 41200, manufacturer: 'Siemens SST-300', installed: '2019-09-20' },
  { id: 'CNC-550', name: '5-Axis Machining Centre', type: 'CNC', line: 'Line 3', status: 'warning', health_score: 74, run_hours: 15360, manufacturer: 'DMG MORI DMU 75', installed: '2022-11-01' },
  { id: 'PRESS-800A', name: 'Hydraulic Stamping Press', type: 'Press', line: 'Line 1', status: 'operational', health_score: 91, run_hours: 22410, manufacturer: 'Schuler TPC 2500', installed: '2020-06-15' },
  { id: 'EXT-004', name: 'Extruder EX-4', type: 'Extruder', line: 'Line 2', status: 'critical', health_score: 51, run_hours: 30550, manufacturer: 'KraussMaffei KMD', installed: '2020-01-10' },
  { id: 'ROB-014', name: 'Robotic Welder RA-14', type: 'Robot', line: 'Line 4', status: 'operational', health_score: 88, run_hours: 12890, manufacturer: 'KUKA KR 210', installed: '2023-03-08' },
  { id: 'FAN-003', name: 'Cooling Tower Fan', type: 'Fan', line: 'Utilities', status: 'operational', health_score: 93, run_hours: 36700, manufacturer: 'Howden CTF', installed: '2018-07-22' },
  { id: 'CONV-021', name: 'Main Conveyor Drive', type: 'Conveyor', line: 'Line 3', status: 'offline', health_score: null, run_hours: 25010, manufacturer: 'SEW Eurodrive', installed: '2021-10-05' },
  { id: 'PUMP-112', name: 'Coolant Circulation Pump', type: 'Pump', line: 'Line 2', status: 'operational', health_score: 89, run_hours: 17650, manufacturer: 'Grundfos NK', installed: '2022-04-19' },
  { id: 'FURN-002', name: 'Heat Treatment Furnace F-2', type: 'Furnace', line: 'Line 1', status: 'operational', health_score: 85, run_hours: 33300, manufacturer: 'Ipsen TQ', installed: '2019-12-02' },
  { id: 'HP-088', name: 'Hydraulic Press HP-88', type: 'Press', line: 'Line 3', status: 'operational', health_score: 82, run_hours: 20940, manufacturer: 'Lasco SPR', installed: '2021-08-30' },
]

// Sensors every asset has in this demo (warn / crit = alarm thresholds)
const SENSORS = [
  { id: 'vibration', name: 'Vibration RMS', unit: 'mm/s', base: 2.2, noise: 0.25, warn: 4.5, crit: 7.1 },
  { id: 'bearing_temp', name: 'Bearing temperature', unit: '°C', base: 58, noise: 1.5, warn: 80, crit: 95 },
  { id: 'pressure', name: 'Discharge pressure', unit: 'bar', base: 20, noise: 0.4, warn: 17, crit: 15, lowerIsBad: true },
  { id: 'current', name: 'Motor current', unit: 'A', base: 118, noise: 3, warn: 135, crit: 150 },
]

// ---------------- Work orders ----------------
const WORK_ORDERS = [
  { id: 'WO-994', asset_id: 'PUMP-204B', type: 'Corrective', status: 'open', priority: 'high', title: 'Replace drive-end bearing', technician: 'J. Miller', opened: iso(now - 1 * DAY), due: iso(now + 1 * DAY), cost: 4200 },
  { id: 'WO-991', asset_id: 'EXT-004', type: 'Corrective', status: 'overdue', priority: 'high', title: 'Inspect barrel heater zone 3', technician: 'D. Rourke', opened: iso(now - 9 * DAY), due: iso(now - 2 * DAY), cost: 1800 },
  { id: 'WO-988', asset_id: 'COMP-109', type: 'Predictive', status: 'overdue', priority: 'medium', title: 'Coolant lube analysis', technician: 'R. Santos', opened: iso(now - 12 * DAY), due: iso(now - 4 * DAY), cost: 650 },
  { id: 'WO-985', asset_id: 'CNC-550', type: 'Predictive', status: 'overdue', priority: 'medium', title: 'Spindle runout check', technician: 'K. Tanaka', opened: iso(now - 14 * DAY), due: iso(now - 3 * DAY), cost: 900 },
  { id: 'WO-982', asset_id: 'CONV-021', type: 'Corrective', status: 'overdue', priority: 'high', title: 'Gearbox oil leak repair', technician: 'A. Chen', opened: iso(now - 6 * DAY), due: iso(now - 1 * DAY), cost: 2600 },
  { id: 'WO-979', asset_id: 'HP-088', type: 'Preventive', status: 'overdue', priority: 'low', title: 'Hydraulic filter swap', technician: 'M. Kowalski', opened: iso(now - 20 * DAY), due: iso(now - 6 * DAY), cost: 480 },
  { id: 'WO-976', asset_id: 'FURN-002', type: 'Preventive', status: 'overdue', priority: 'low', title: 'Thermocouple calibration', technician: 'E. Rodriguez', opened: iso(now - 18 * DAY), due: iso(now - 5 * DAY), cost: 350 },
  { id: 'WO-973', asset_id: 'ROB-014', type: 'Preventive', status: 'open', priority: 'low', title: 'Joint 4 grease & recalibration', technician: 'A. Chen', opened: iso(now - 2 * DAY), due: iso(now + 6 * DAY), cost: 520 },
  { id: 'WO-970', asset_id: 'TURB-004', type: 'Preventive', status: 'open', priority: 'medium', title: 'Annual borescope inspection', technician: 'M. Lindqvist', opened: iso(now - 3 * DAY), due: iso(now + 10 * DAY), cost: 3100 },
  { id: 'WO-961', asset_id: 'PUMP-204B', type: 'Preventive', status: 'completed', priority: 'medium', title: 'Re-lubrication & seal inspection', technician: 'J. Miller', opened: iso(now - 80 * DAY), due: iso(now - 75 * DAY), completed: iso(now - 76 * DAY), downtime_h: 2.4, cost: 950 },
  { id: 'WO-955', asset_id: 'PRESS-800A', type: 'Predictive', status: 'completed', priority: 'medium', title: 'Seal valve pack replacement', technician: 'M. Kowalski', opened: iso(now - 25 * DAY), due: iso(now - 20 * DAY), completed: iso(now - 21 * DAY), downtime_h: 2.0, cost: 2450 },
  { id: 'WO-948', asset_id: 'EXT-004', type: 'Corrective', status: 'completed', priority: 'high', title: 'Unplanned stop – screw drive coupling', technician: 'D. Rourke', opened: iso(now - 22 * DAY), due: iso(now - 22 * DAY), completed: iso(now - 21 * DAY), downtime_h: 8.6, cost: 14200 },
  { id: 'WO-944', asset_id: 'PUMP-112', type: 'Preventive', status: 'completed', priority: 'low', title: 'Impeller balance check', technician: 'E. Rodriguez', opened: iso(now - 30 * DAY), due: iso(now - 26 * DAY), completed: iso(now - 27 * DAY), downtime_h: 1.0, cost: 850 },
  { id: 'WO-939', asset_id: 'PUMP-204B', type: 'Corrective', status: 'completed', priority: 'high', title: 'Unplanned trip – bearing overheating', technician: 'J. Miller', opened: iso(now - 15 * DAY), due: iso(now - 15 * DAY), completed: iso(now - 15 * DAY + 5 * HOUR), downtime_h: 5.2, cost: 3900 },
  { id: 'WO-932', asset_id: 'FAN-003', type: 'Preventive', status: 'completed', priority: 'low', title: 'Belt tension adjustment', technician: 'A. Chen', opened: iso(now - 35 * DAY), due: iso(now - 30 * DAY), completed: iso(now - 31 * DAY), downtime_h: 1.5, cost: 300 },
]

// ---------------- Alarms ----------------
let ALARMS = [
  { id: 'ALM-1042', asset_id: 'PUMP-204B', severity: 'critical', sensor: 'vibration', message: 'Vibration above critical limit', value: 8.4, threshold: 7.1, unit: 'mm/s', time: iso(now - 25 * 60 * 1000), acknowledged: false },
  { id: 'ALM-1041', asset_id: 'PUMP-204B', severity: 'critical', sensor: 'bearing_temp', message: 'Bearing temperature above trip level', value: 96.5, threshold: 95, unit: '°C', time: iso(now - 40 * 60 * 1000), acknowledged: false },
  { id: 'ALM-1039', asset_id: 'EXT-004', severity: 'critical', sensor: 'current', message: 'Motor current surge', value: 152, threshold: 150, unit: 'A', time: iso(now - 2 * HOUR), acknowledged: false },
  { id: 'ALM-1036', asset_id: 'COMP-109', severity: 'warning', sensor: 'bearing_temp', message: 'Temperature trending up (+1.8 °C/h)', value: 83, threshold: 80, unit: '°C', time: iso(now - 3 * HOUR), acknowledged: false },
  { id: 'ALM-1033', asset_id: 'CNC-550', severity: 'warning', sensor: 'vibration', message: 'Spindle vibration elevated', value: 4.9, threshold: 4.5, unit: 'mm/s', time: iso(now - 5 * HOUR), acknowledged: true },
  { id: 'ALM-1030', asset_id: 'CONV-021', severity: 'warning', sensor: null, message: 'No data from gateway for 2 h (offline)', value: null, threshold: null, unit: null, time: iso(now - 6 * HOUR), acknowledged: false },
  { id: 'ALM-1027', asset_id: 'HP-088', severity: 'info', sensor: 'pressure', message: 'Supply pressure oscillation ±1.4 bar', value: 18.2, threshold: 17, unit: 'bar', time: iso(now - 9 * HOUR), acknowledged: true },
]

const assetName = (id) => ASSETS.find((a) => a.id === id)?.name ?? id

// ---------------- Endpoint equivalents ----------------

// Stand-in for the FastAPI session: signed in once "login" was clicked (kept per browser tab).
const USER = { name: 'Demo User', email: 'demo.user@assetpulse.example', role: 'PM.Reader' }
export async function getMe() {
  await delay(100)
  if (!sessionStorage.getItem('mock_signed_in')) throw new Error('401 Unauthorized')
  return USER
}
export const login = () => sessionStorage.setItem('mock_signed_in', '1')
export const logout = () => sessionStorage.removeItem('mock_signed_in')

export async function getFleetSummary() {
  await delay()
  const status_counts = { operational: 0, warning: 0, critical: 0, offline: 0 }
  ASSETS.forEach((a) => status_counts[a.status]++)

  const rand = seededRandom(7)
  const availability_trend = Array.from({ length: 30 }, (_, i) => ({
    date: iso(now - (29 - i) * DAY).slice(0, 10),
    availability_pct: +(95.2 + rand() * 2.4 + i * 0.02).toFixed(1),
    unplanned_downtime_h: +(rand() * 2.8).toFixed(1),
  }))

  return {
    as_of: iso(now - 4 * 60 * 1000),
    kpis: {
      availability_pct: 96.4,
      unplanned_downtime_h: 38.2,
      mtbf_h: 742,
      mttr_h: 2.4,
      repeat_failure_pct: 4.8,
      overdue_work_orders: WORK_ORDERS.filter((w) => w.status === 'overdue').length,
      maintenance_cost_eur: 25700, // October = last 30 days (same as maintenance summary)
      alarm_rate_per_h: 12.4,
    },
    status_counts,
    availability_trend,
  }
}

// Same shape as FastAPI: { items, total, page, page_size }, filtered like the server does
export async function getAssets({ status, q, page = 1, page_size = 50 } = {}) {
  await delay()
  const needle = (q ?? '').toLowerCase()
  const all = ASSETS.filter(
    (a) => (!status || a.status === status) && `${a.id} ${a.name} ${a.type} ${a.line}`.toLowerCase().includes(needle),
  ).map((a) => ({
    ...a,
    active_alarms: ALARMS.filter((al) => al.asset_id === a.id && !al.acknowledged).length,
    last_maintenance: WORK_ORDERS.filter((w) => w.asset_id === a.id && w.completed).map((w) => w.completed).sort().at(-1) ?? null,
  }))
  return { items: all.slice((page - 1) * page_size, page * page_size), total: all.length, page, page_size }
}

export async function getAsset(assetId) {
  await delay()
  const a = ASSETS.find((x) => x.id === assetId)
  if (!a) throw new Error(`404 Asset ${assetId} not found`)
  const bad = a.status === 'critical'
  const meh = a.status === 'warning'
  const series = await getSensorSeries(assetId, '24h', 0)
  return {
    ...a,
    kpis: {
      availability_pct: bad ? 91.2 : meh ? 95.1 : 98.6,
      mtbf_h: bad ? 410 : meh ? 620 : 980,
      mttr_h: bad ? 4.1 : meh ? 2.8 : 1.6,
      unplanned_downtime_h: bad ? 8.4 : meh ? 3.1 : 0.6,
    },
    rul_days: bad ? 4 : meh ? 19 : null,
    predicted_failure: bad ? 'Bearing seizure' : meh ? 'Thermal degradation' : null,
    components: [
      { name: 'Motor', status: a.status === 'offline' ? 'offline' : 'operational' },
      { name: 'Drive-end bearing', status: bad ? 'critical' : 'operational' },
      { name: 'Non-drive-end bearing', status: 'operational' },
      { name: 'Coupling', status: meh ? 'warning' : 'operational' },
      { name: 'Seal / gasket', status: 'operational' },
    ],
    latest_readings: series.sensors.map((s) => {
      const last = s.points.at(-1).value
      const over = (lim) => (s.lower_is_bad ? last <= lim : last >= lim)
      return { sensor: s.name, value: last, unit: s.unit, status: over(s.crit) ? 'critical' : over(s.warn) ? 'warning' : 'operational' }
    }),
  }
}

export async function getSensorSeries(assetId, range = '24h', ms = 300) {
  await delay(ms)
  const asset = ASSETS.find((a) => a.id === assetId)
  if (!asset) throw new Error(`404 Asset ${assetId} not found`)

  const config = { '1h': { span: HOUR, points: 120 }, '24h': { span: DAY, points: 288 }, '7d': { span: 7 * DAY, points: 336 } }[range] ?? { span: DAY, points: 288 }
  const step = config.span / config.points
  const severity = { critical: 1, warning: 0.55, operational: 0.1, offline: 0 }[asset.status]

  const sensors = SENSORS.map((s, idx) => {
    const rand = seededRandom(assetId.length * 31 + idx * 17 + range.length)
    const points = []
    for (let i = 0; i <= config.points; i++) {
      const progress = i / config.points // 0 → 1 over the window
      const drift = severity * Math.pow(progress, 3) * (s.crit - s.base) * 1.05
      const wave = Math.sin(i / 9) * s.noise
      let value = s.base + wave + (rand() - 0.5) * s.noise * 2 + drift
      // last point identical for every range, so "latest reading" always matches the charts
      if (i === config.points) value = s.base + drift
      // Offline asset: last 15% of the window has no data
      if (asset.status === 'offline' && progress > 0.85) value = null
      const v = value === null ? null : +value.toFixed(2)
      const over = v !== null && (s.lowerIsBad ? v <= s.warn : v >= s.warn)
      points.push({ t: iso(now - config.span + i * step), value: v, anomaly: over })
    }
    return { id: s.id, name: s.name, unit: s.unit, warn: s.warn, crit: s.crit, lower_is_bad: !!s.lowerIsBad, points }
  })
  return { asset_id: assetId, range, sensors }
}

export async function getMaintenanceSummary() {
  await delay()
  const months = ['May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct']
  return {
    kpis: {
      mtbf_h: 742,
      mttr_h: 2.4,
      overdue_work_orders: WORK_ORDERS.filter((w) => w.status === 'overdue').length,
      maintenance_cost_eur: 25700,
      repeat_failure_pct: 4.8,
      planned_pct: 82,
    },
    monthly: months.map((month, i) => ({
      month,
      mtbf_h: [610, 645, 668, 690, 721, 742][i],
      mttr_h: [3.4, 3.1, 3.0, 2.8, 2.6, 2.4][i],
      cost_eur: [36200, 33800, 31500, 29400, 27900, 25700][i],
    })),
    cost_by_type: [
      { type: 'Preventive', cost_eur: 61200 },
      { type: 'Predictive', cost_eur: 42800 },
      { type: 'Corrective', cost_eur: 80500 },
    ],
  }
}

export async function getWorkOrders({ status, asset_id, page = 1, page_size = 20 } = {}) {
  await delay()
  const all = WORK_ORDERS.filter((w) => (!status || w.status === status) && (!asset_id || w.asset_id === asset_id)).map((w) => ({
    ...w,
    asset_name: assetName(w.asset_id),
  }))
  return { items: all.slice((page - 1) * page_size, page * page_size), total: all.length, page, page_size }
}

export async function getAlarms({ asset_id } = {}) {
  await delay()
  return ALARMS.filter((a) => !asset_id || a.asset_id === asset_id).map((a) => ({ ...a, asset_name: assetName(a.asset_id) }))
}

export async function acknowledgeAlarm(alarmId) {
  await delay(150)
  ALARMS = ALARMS.map((a) => (a.id === alarmId ? { ...a, acknowledged: true } : a))
  return { ok: true }
}

// Mirrors the real two-step flow: askAgent returns a plan to approve, approveAgent returns the answer.
const mockRuns = new Map()

export async function askAgent(question) {
  await delay(900)
  const runId = `mock-${Date.now()}`
  mockRuns.set(runId, question)
  return {
    status: 'awaiting_approval',
    run_id: runId,
    plan: `We will look up the relevant KPI for the requested period. Then we will review the supporting detail records to explain the result. (Demo plan: connect FastAPI to get the real agent.)`,
    result: null,
  }
}

export async function approveAgent(runId, approved) {
  await delay(900)
  const question = mockRuns.get(runId) ?? ''
  mockRuns.delete(runId)
  const result = approved
    ? {
        answer: `Demo answer for: "${question}"

Fleet availability is 96.4% and 6 work orders are overdue. (Demo data: connect FastAPI to get real agent output.)`,
        evidence: [{ tool: 'get_kpi', arguments: { kpi_name: 'availability' }, status: 'ok', result: { row_count: 1 } }],
        grounded: true,
        limitations: ['Demo data only.'],
        filters: {},
        source_measure: 'availability (demo)',
      }
    : {
        answer: 'The plan was declined, so nothing was run.',
        evidence: [],
        grounded: false,
        limitations: ['The plan was declined by the user.'],
        filters: {},
        source_measure: '',
      }
  return { status: 'answered', run_id: runId, plan: null, result }
}
