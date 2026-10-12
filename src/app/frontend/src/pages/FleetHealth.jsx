import { Link, useNavigate } from 'react-router-dom'
import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, ReferenceLine, CartesianGrid } from 'recharts'
import { api } from '../api/client.js'
import { useApi } from '../hooks/useApi.js'
import { Card, PageHeader, KpiCard, StatusBadge, Loading, ErrorBox, Table, STATUS_COLORS } from '../components/ui.jsx'
import { fmtNumber, fmtEuro, fmtDateTimeUtc, timeAgo } from '../format.js'

const STATUS_ORDER = ['operational', 'warning', 'critical', 'offline']
const TARGET_AVAILABILITY = 95

function StatusDistribution({ counts }) {
  const total = STATUS_ORDER.reduce((sum, s) => sum + counts[s], 0)
  return (
    <div>
      <div className="num text-3xl font-bold">{fmtNumber(total, 0)} <span className="text-sm font-medium text-gray-500">assets monitored</span></div>
      {/* One horizontal bar split by status */}
      <div className="mt-4 flex h-3 overflow-hidden rounded-full bg-gray-100">
        {STATUS_ORDER.map((s) => (
          <div key={s} style={{ width: `${(counts[s] / total) * 100}%`, background: STATUS_COLORS[s] }} title={`${s}: ${fmtNumber(counts[s], 0)}`} />
        ))}
      </div>
      <div className="mt-4 grid grid-cols-2 gap-2">
        {STATUS_ORDER.map((s) => (
          <Link key={s} to={`/equipment?status=${s}`} className="flex items-center justify-between rounded-md border border-line px-3 py-2 hover:border-brand">
            <span className="flex items-center gap-2 text-sm capitalize">
              <span className="h-2.5 w-2.5 rounded-full" style={{ background: STATUS_COLORS[s] }} />
              {s}
            </span>
            <span className="num font-semibold">{fmtNumber(counts[s], 0)}</span>
          </Link>
        ))}
      </div>
    </div>
  )
}

// Zoom the axis to the data (a 90-100% axis drew a flat line at 99.9%): from just under the lowest point to 100
function availabilityAxis(trend) {
  const values = trend.map((p) => p.availability_pct).filter((v) => v != null)
  if (values.length === 0) return { domain: [90, 100], ticks: undefined }
  const lo = Math.min(Math.floor((Math.min(...values) - 0.05) * 10) / 10, 99.9)
  const step = (100 - lo) / 4
  return { domain: [lo, 100], ticks: [0, 1, 2, 3, 4].map((i) => Number((lo + step * i).toFixed(3))) }
}

// 5e-6 per hour must not read as "0"
const fmtRate = (v) => (v != null && v > 0 && v < 0.01 ? '<0.01' : fmtNumber(v, 2))

export default function FleetHealth() {
  const navigate = useNavigate()
  const summary = useApi(() => api.getFleetSummary())
  // Lowest availability first, offline assets last (offline assets have no meaningful availability)
  const assets = useApi(() => api.getAssets({ sort: 'availability', page_size: 10 }))
  const byType = useApi(() => api.queryKpis({ kpis: ['availability', 'unplanned_downtime_hours', 'unplanned_failures'], group_by: ['equipment_type'] }))

  if (summary.error) return <ErrorBox error={summary.error} onRetry={summary.reload} />
  if (!summary.data) return <Loading />

  const { kpis, status_counts, availability_trend, as_of, period_from, period_to } = summary.data
  // Real data: the KPIs cover the whole Gold period and the trend is monthly. Mock data is "last 30 days".
  const period = period_from ? `${period_from} to ${period_to}` : 'last 30 days'
  const axis = availabilityAxis(availability_trend)
  const types = [...(byType.data?.rows ?? [])].sort((a, b) => (a.availability ?? 2) - (b.availability ?? 2))

  return (
    <div>
      <PageHeader title="Fleet Health Overview" subtitle={`All assets · ${period} · data through ${fmtDateTimeUtc(as_of)}`}>
        <span className="rounded-md border border-line bg-white px-2.5 py-1 text-xs text-gray-600" title="Latest event in Gold. Every “ago” on these pages counts from here, not from today.">
          Data through <span className="font-semibold text-gray-900">{fmtDateTimeUtc(as_of)}</span> · {timeAgo(as_of)}
        </span>
      </PageHeader>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard label="Asset availability" value={fmtNumber(kpis.availability_pct)} unit="%" hint="Target ≥ 95% · hours-weighted, all assets" tone={kpis.availability_pct >= 95 ? 'good' : 'bad'} />
        <KpiCard label="Unplanned downtime" value={fmtNumber(kpis.unplanned_downtime_h)} unit="h" hint={period} />
        <KpiCard label="MTBF" value={fmtNumber(kpis.mtbf_h, 0)} unit="h" hint="Mean time between failures" />
        <KpiCard label="MTTR" value={fmtNumber(kpis.mttr_h)} unit="h" hint="Mean time to repair" />
        <KpiCard label="Repeat-failure rate" value={fmtNumber(kpis.repeat_failure_pct)} unit="%" hint="Target < 5%" tone={kpis.repeat_failure_pct < 5 ? 'good' : 'bad'} />
        <KpiCard label="Overdue work orders" value={fmtNumber(kpis.overdue_work_orders, 0)} hint="Open past due date" tone={kpis.overdue_work_orders > 0 ? 'warn' : 'good'} />
        <KpiCard label="Maintenance cost" value={fmtEuro(kpis.maintenance_cost_eur)} hint={period} />
        <KpiCard label="Alarm rate" value={fmtRate(kpis.alarm_rate_per_h)} unit="/h" hint="Alarm readings per hour, whole fleet" />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card title="Asset status">
          <StatusDistribution counts={status_counts} />
        </Card>

        <Card
          title="Availability trend"
          subtitle={`${period_from ? 'Monthly' : 'Daily'} availability, ${period} · axis zoomed to ${axis.domain[0]}–100%${axis.domain[0] > TARGET_AVAILABILITY ? ` (the ${TARGET_AVAILABILITY}% target is below this range)` : ''}`}
          className="lg:col-span-2"
        >
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={availability_trend} margin={{ top: 5, right: 10, left: -5, bottom: 0 }}>
              <CartesianGrid stroke="#f0f0f0" vertical={false} />
              <XAxis dataKey="date" tickFormatter={(d) => d.slice(5)} tick={{ fontSize: 11, fill: '#6b7280' }} minTickGap={30} />
              <YAxis domain={axis.domain} ticks={axis.ticks} tickFormatter={(v) => `${Number(v.toFixed(2))}`} tick={{ fontSize: 11, fill: '#6b7280' }} unit="%" width={55} />
              <Tooltip formatter={(v) => [`${v} %`, 'Availability']} contentStyle={{ fontSize: 12 }} />
              {axis.domain[0] <= TARGET_AVAILABILITY && (
                <ReferenceLine y={TARGET_AVAILABILITY} stroke="#a16207" strokeDasharray="4 4" label={{ value: 'target 95%', position: 'insideBottomRight', fontSize: 11, fill: '#a16207' }} />
              )}
              <Line type="linear" dataKey="availability_pct" stroke="#7e00c9" strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card title="By equipment type" subtitle="Lowest availability first · whole period">
          {byType.error ? (
            <ErrorBox error={byType.error} onRetry={byType.reload} />
          ) : !byType.data ? (
            <Loading />
          ) : (
            <Table
              rows={types.map((t) => ({ ...t, id: t.equipment_type ?? 'Unknown' }))}
              empty="No equipment type data"
              columns={[
                { key: 'id', label: 'Type' },
                { key: 'availability', label: 'Avail.', align: 'right', render: (t) => (t.availability == null ? 'No data' : `${fmtNumber(t.availability * 100, 2)} %`) },
                { key: 'unplanned_downtime_hours', label: 'Downtime h', align: 'right', render: (t) => fmtNumber(t.unplanned_downtime_hours, 0) },
                { key: 'unplanned_failures', label: 'Failures', align: 'right', render: (t) => fmtNumber(t.unplanned_failures, 0) },
              ]}
            />
          )}
        </Card>

        <Card title="Lowest-availability assets" subtitle="The 10 in-service assets with the lowest availability · offline assets are listed last · click a row for details" className="lg:col-span-2">
          {assets.error ? (
            <ErrorBox error={assets.error} />
          ) : assets.loading && !assets.data ? (
            <Loading />
          ) : (
            <Table
              onRowClick={(a) => navigate(`/equipment/${a.id}`)}
              rows={assets.data?.items ?? []}
              empty="No assets"
              columns={[
                { key: 'id', label: 'Asset', render: (a) => <div><div className="font-semibold">{a.id}</div><div className="text-xs text-gray-500">{a.name}</div></div> },
                { key: 'line', label: 'Line' },
                { key: 'status', label: 'Status', render: (a) => <StatusBadge status={a.status} /> },
                { key: 'availability_pct', label: 'Availability', align: 'right', render: (a) => (a.availability_pct == null ? <span className="text-gray-400">No data</span> : `${fmtNumber(a.availability_pct, 2)} %`) },
                { key: 'last_alarm', label: 'Last alarm', render: (a) => (a.last_alarm ? `${timeAgo(a.last_alarm, as_of)} (${a.last_alarm_state})` : <span className="text-gray-400">No alarms</span>) },
              ]}
            />
          )}
        </Card>
      </div>
    </div>
  )
}