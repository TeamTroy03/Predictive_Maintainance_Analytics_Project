import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, ReferenceLine, CartesianGrid } from 'recharts'

// One small chart per sensor, stacked vertically.
// syncId="sensors" makes the hover crosshair move on ALL charts at the same time,
// so you can compare what every sensor did at the same moment.

function formatTick(t, range) {
  const d = new Date(t)
  if (range === '1h' || range === '24h') return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' })
}

// Red dot on points the backend marked as anomalies. Series with few points (real sensors report about
// once a day) also get a grey dot on every point, otherwise a single reading would draw nothing.
function PointDot({ cx, cy, payload, showAll }) {
  if (cy == null) return null
  if (payload?.anomaly) return <circle cx={cx} cy={cy} r={2.5} fill="#e5384f" stroke="none" />
  return showAll ? <circle cx={cx} cy={cy} r={2.5} fill="#4b5563" stroke="none" /> : null
}

function SensorChart({ sensor, range, height }) {
  const values = sensor.points.map((p) => p.value).filter((v) => v !== null)
  // warn / crit are optional: the Gold layer has no alarm limits, so real sensors come without them
  const limits = [sensor.warn, sensor.crit].filter((v) => v != null)
  const min = Math.min(...values, ...limits)
  const max = Math.max(...values, ...limits)
  const pad = (max - min) * 0.1 || 1
  const latest = [...sensor.points].reverse().find((p) => p.value !== null)
  const anomalies = sensor.points.filter((p) => p.anomaly).length
  const hasGap = sensor.points.some((p) => p.value === null)

  return (
    <div className="rounded-lg border border-line bg-white p-3">
      <div className="mb-1 flex items-baseline justify-between">
        <div>
          <span className="text-sm font-semibold">{sensor.name}</span>
          {limits.length > 0 && (
            <span className="ml-2 text-xs text-gray-500">
              warn {sensor.warn} · crit {sensor.crit} {sensor.unit}
            </span>
          )}
          {anomalies > 0 && <span className="ml-2 rounded bg-crit/10 px-1.5 py-0.5 text-[11px] font-medium text-crit">{anomalies} alarm readings</span>}
          {hasGap && <span className="ml-2 rounded bg-off/10 px-1.5 py-0.5 text-[11px] font-medium text-gray-600">data gap</span>}
        </div>
        <div className="num text-lg font-bold">
          {latest ? Number(latest.value.toFixed(2)) : '–'} <span className="text-xs font-medium text-gray-500">{sensor.unit}</span>
        </div>
      </div>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={sensor.points} syncId="sensors" margin={{ top: 5, right: 10, bottom: 0, left: -10 }}>
          <CartesianGrid stroke="#f0f0f0" vertical={false} />
          <XAxis dataKey="t" tickFormatter={(t) => formatTick(t, range)} minTickGap={50} tick={{ fontSize: 11, fill: '#6b7280' }} />
          <YAxis domain={[Math.floor(min - pad), Math.ceil(max + pad)]} allowDecimals={max - min < 10} tick={{ fontSize: 11, fill: '#6b7280' }} width={50} />
          <Tooltip
            labelFormatter={(t) => new Date(t).toLocaleString('en-GB')}
            formatter={(v) => [v === null ? 'no data' : `${Number(v.toFixed(2))} ${sensor.unit}`, sensor.name]}
            contentStyle={{ fontSize: 12 }}
          />
          {sensor.warn != null && <ReferenceLine y={sensor.warn} stroke="#c98a00" strokeDasharray="4 4" />}
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
  return (
    <div className="space-y-3">
      {sensors.map((s) => (
        <SensorChart key={s.id} sensor={s} range={range} height={height} />
      ))}
      <div className="flex gap-4 text-xs text-gray-500">
        <span><span className="mr-1 inline-block h-0.5 w-4 bg-gray-600 align-middle" />reading</span>
        {hasLimits && <span><span className="mr-1 inline-block w-4 border-t-2 border-dashed border-warn align-middle" />warning limit</span>}
        {hasLimits && <span><span className="mr-1 inline-block w-4 border-t-2 border-dashed border-crit align-middle" />critical limit</span>}
        <span><span className="mr-1 inline-block h-2 w-2 rounded-full bg-crit align-middle" />alarm reading</span>
      </div>
    </div>
  )
}