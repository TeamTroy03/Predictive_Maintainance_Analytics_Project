import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, ReferenceLine, CartesianGrid } from 'recharts'
import { unitLabel } from '../format.js'

// One small chart per sensor, stacked vertically.
// All charts share ONE time axis (same domain, same ticks) and syncMethod="value" matches the hover by time, not by
// point number, so the crosshair lands on the same moment in every chart even when sensors report at different times.

const X_TICKS = 6

function formatTick(t, range) {
  const d = new Date(t)
  if (range === '1h' || range === '24h') return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' })
}

// "Nice" step (1, 2, 5 x 10^n), so ticks read 100, 120, 140 and not 107, 114, 121, 133
function niceNum(x, round) {
  const exp = Math.floor(Math.log10(x))
  const f = x / 10 ** exp
  const nice = round ? (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) : f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10
  return nice * 10 ** exp
}

export function niceTicks(min, max, count = 5) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1]
  if (min === max) return niceTicks(min - 1, max + 1, count)
  const step = niceNum(niceNum(max - min, false) / (count - 1), true)
  const lo = Math.floor(min / step) * step
  const n = Math.round((Math.ceil(max / step) * step - lo) / step)
  return Array.from({ length: n + 1 }, (_, i) => Number((lo + i * step).toFixed(8)))
}

// Red dot on points the backend marked as anomalies. Series with few points (real sensors report about
// once a day) also get a grey dot on every point, otherwise a single reading would draw nothing.
function PointDot({ cx, cy, payload, showAll }) {
  if (cy == null) return null
  if (payload?.anomaly) return <circle cx={cx} cy={cy} r={2.5} fill="#e5384f" stroke="none" />
  return showAll ? <circle cx={cx} cy={cy} r={2.5} fill="#4b5563" stroke="none" /> : null
}

function SensorChart({ sensor, range, height, xDomain, xTicks }) {
  const unit = unitLabel(sensor.unit)
  const data = sensor.points.map((p) => ({ ...p, ts: new Date(p.t).getTime() }))
  const values = sensor.points.map((p) => p.value).filter((v) => v !== null)
  // warn / crit are optional: the Gold layer has no alarm limits, so real sensors come without them
  const limits = [sensor.warn, sensor.crit].filter((v) => v != null)
  const yTicks = niceTicks(Math.min(...values, ...limits), Math.max(...values, ...limits))
  const latest = [...sensor.points].reverse().find((p) => p.value !== null)
  const anomalies = sensor.points.filter((p) => p.anomaly).length
  const hasGap = sensor.points.some((p) => p.value === null)
  const limitText = [sensor.warn != null && `warn ${sensor.warn}`, sensor.crit != null && `crit ${sensor.crit}`].filter(Boolean).join(' · ')

  return (
    <div className="rounded-lg border border-line bg-white p-3">
      <div className="mb-1 flex items-baseline justify-between">
        <div>
          <span className="text-sm font-semibold">{sensor.name}</span>
          {limitText && <span className="ml-2 text-xs text-gray-500">{limitText} {unit}</span>}
          {anomalies > 0 && <span className="ml-2 rounded bg-crit/10 px-1.5 py-0.5 text-[11px] font-medium text-crit">{anomalies} alarm readings</span>}
          {hasGap && <span className="ml-2 rounded bg-off/10 px-1.5 py-0.5 text-[11px] font-medium text-gray-600">data gap</span>}
        </div>
        <div className="num text-lg font-bold">
          {latest ? Number(latest.value.toFixed(2)) : '–'} <span className="text-xs font-medium text-gray-500">{unit}</span>
        </div>
      </div>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} syncId="sensors" syncMethod="value" margin={{ top: 5, right: 10, bottom: 0, left: -10 }}>
          <CartesianGrid stroke="#f0f0f0" vertical={false} />
          <XAxis dataKey="ts" type="number" scale="time" domain={xDomain} ticks={xTicks} tickFormatter={(t) => formatTick(t, range)} tick={{ fontSize: 11, fill: '#6b7280' }} />
          <YAxis domain={[yTicks[0], yTicks[yTicks.length - 1]]} ticks={yTicks} tick={{ fontSize: 11, fill: '#6b7280' }} width={50} />
          <Tooltip
            labelFormatter={(t) => new Date(t).toLocaleString('en-GB', { timeZone: 'UTC' }) + ' UTC'}
            formatter={(v) => [v === null ? 'no data' : `${Number(v.toFixed(2))} ${unit}`, sensor.name]}
            contentStyle={{ fontSize: 12 }}
          />
          {sensor.warn != null && <ReferenceLine y={sensor.warn} stroke="#a16207" strokeDasharray="4 4" />}
          {sensor.crit != null && <ReferenceLine y={sensor.crit} stroke="#e5384f" strokeDasharray="4 4" />}
          <Line type="linear" dataKey="value" stroke="#4b5563" strokeWidth={1.5} dot={<PointDot showAll={sensor.points.length < 30} />} activeDot={{ r: 4, fill: '#7e00c9' }} isAnimationActive={false} connectNulls={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

export default function SensorCharts({ sensors, range, height = 150 }) {
  if (sensors.length === 0) {
    return <p className="py-6 text-center text-sm text-gray-500">No sensor readings for this asset in the selected window.</p>
  }
  const hasLimits = sensors.some((s) => s.warn != null || s.crit != null)
  // One time axis for every chart: from the first to the last reading of any sensor, evenly spaced ticks
  const times = sensors.flatMap((s) => s.points.map((p) => new Date(p.t).getTime()))
  const tMin = Math.min(...times)
  const tMax = Math.max(...times)
  const xTicks = tMin === tMax ? [tMin] : Array.from({ length: X_TICKS }, (_, i) => Math.round(tMin + ((tMax - tMin) * i) / (X_TICKS - 1)))
  return (
    <div className="space-y-3">
      {sensors.map((s) => (
        <SensorChart key={s.id} sensor={s} range={range} height={height} xDomain={[tMin, tMax]} xTicks={xTicks} />
      ))}
      <div className="flex gap-4 text-xs text-gray-500">
        <span><span className="mr-1 inline-block h-0.5 w-4 bg-gray-600 align-middle" />reading</span>
        {hasLimits && <span><span className="mr-1 inline-block w-4 border-t-2 border-dashed border-warn align-middle" />warning limit</span>}
        {hasLimits && <span><span className="mr-1 inline-block w-4 border-t-2 border-dashed border-crit align-middle" />critical limit</span>}
        <span><span className="mr-1 inline-block h-2 w-2 rounded-full bg-crit align-middle" />alarm reading</span>
        <span className="ml-auto">Times in UTC{hasLimits ? '' : ' · alarm limits are not in Gold, so no limit lines'}</span>
      </div>
    </div>
  )
}
