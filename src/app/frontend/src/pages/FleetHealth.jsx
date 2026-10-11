import { Link, useNavigate } from 'react-router-dom'
import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, ReferenceLine, CartesianGrid } from 'recharts'
import { api } from '../api/client.js'
import { useApi } from '../hooks/useApi.js'
import { Card, PageHeader, KpiCard, StatusBadge, Loading, ErrorBox, Table, STATUS_COLORS } from '../components/ui.jsx'
import { fmtNumber, fmtEuro, timeAgo } from '../format.js'

const STATUS_ORDER = ['operational', 'warning', 'critical', 'offline']
const URGENCY = { critical: 0, warning: 1, offline: 2, operational: 3 }

function StatusDistribution({ counts }) {
  const total = STATUS_ORDER.reduce((sum, s) => sum + counts[s], 0)
  return (
    <div>
      <div className="num text-3xl font-bold">{total} <span className="text-sm font-medium text-gray-500">assets monitored</span></div>
      {/* One horizontal bar split by status */}
      <div className="mt-4 flex h-3 overflow-hidden rounded-full bg-gray-100">
        {STATUS_ORDER.map((s) => (
          <div key={s} style={{ width: `${(counts[s] / total) * 100}%`, background: STATUS_COLORS[s] }} title={`${s}: ${counts[s]}`} />
        ))}
      </div>
      <div className="mt-4 grid grid-cols-2 gap-2">
        {STATUS_ORDER.map((s) => (
          <Link key={s} to={`/equipment?status=${s}`} className="flex items-center justify-between rounded-md border border-line px-3 py-2 hover:border-brand">
            <span className="flex items-center gap-2 text-sm capitalize">
              <span className="h-2.5 w-2.5 rounded-full" style={{ background: STATUS_COLORS[s] }} />
              {s}
            </span>
            <span className="num font-semibold">{counts[s]}</span>
          </Link>
        ))}
      </div>
    </div>
  )
}

export default function FleetHealth() {
  const navigate = useNavigate()
  const summary = useApi(() => api.getFleetSummary())
  const assets = useApi(() => api.getAssets())

  if (summary.error) return <ErrorBox error={summary.error} onRetry={summary.reload} />
  if (!summary.data) return <Loading />

  const { kpis, status_counts, availability_trend, as_of } = summary.data
  const attention = (assets.data ?? [])
    .filter((a) => a.status !== 'operational')
    .sort((a, b) => URGENCY[a.status] - URGENCY[b.status])

  return (
    <div>
      <PageHeader title="Fleet Health Overview" subtitle={`All assets in Plant 4 · last 30 days · data as of ${timeAgo(as_of)}`} />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard label="Asset availability" value={fmtNumber(kpis.availability_pct)} unit="%" hint="Target ≥ 95%" tone={kpis.availability_pct >= 95 ? 'good' : 'bad'} />
        <KpiCard label="Unplanned downtime" value={fmtNumber(kpis.unplanned_downtime_h)} unit="h" hint="Last 30 days" />
        <KpiCard label="MTBF" value={fmtNumber(kpis.mtbf_h, 0)} unit="h" hint="Mean time between failures" />
        <KpiCard label="MTTR" value={fmtNumber(kpis.mttr_h)} unit="h" hint="Mean time to repair" />
        <KpiCard label="Repeat-failure rate" value={fmtNumber(kpis.repeat_failure_pct)} unit="%" hint="Target < 5%" tone={kpis.repeat_failure_pct < 5 ? 'good' : 'bad'} />
        <KpiCard label="Overdue work orders" value={kpis.overdue_work_orders} hint="Open past due date" tone={kpis.overdue_work_orders > 0 ? 'warn' : 'good'} />
        <KpiCard label="Maintenance cost" value={fmtEuro(kpis.maintenance_cost_eur)} hint="Last 30 days" />
        <KpiCard label="Alarm rate" value={fmtNumber(kpis.alarm_rate_per_h)} unit="/h" hint="Alarms per hour" />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card title="Asset status">
          <StatusDistribution counts={status_counts} />
        </Card>

        <Card title="Availability trend" subtitle="Daily availability, last 30 days" className="lg:col-span-2">
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={availability_trend} margin={{ top: 5, right: 10, left: -15, bottom: 0 }}>
              <CartesianGrid stroke="#f0f0f0" vertical={false} />
              <XAxis dataKey="date" tickFormatter={(d) => d.slice(5)} tick={{ fontSize: 11, fill: '#6b7280' }} minTickGap={30} />
              <YAxis domain={[90, 100]} tick={{ fontSize: 11, fill: '#6b7280' }} unit="%" />
              <Tooltip formatter={(v) => [`${v} %`, 'Availability']} contentStyle={{ fontSize: 12 }} />
              <ReferenceLine y={95} stroke="#c98a00" strokeDasharray="4 4" label={{ value: 'target 95%', position: 'insideBottomRight', fontSize: 11, fill: '#c98a00' }} />
              <Line type="linear" dataKey="availability_pct" stroke="#7e00c9" strokeWidth={2} dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </Card>
      </div>

      <Card title="Assets needing attention" subtitle="Critical, warning and offline assets · click a row for details" className="mt-4">
        {assets.loading && !assets.data ? (
          <Loading />
        ) : (
          <Table
            onRowClick={(a) => navigate(`/equipment/${a.id}`)}
            rows={attention}
            empty="All assets are operational"
            columns={[
              { key: 'id', label: 'Asset', render: (a) => <div><div className="font-semibold">{a.id}</div><div className="text-xs text-gray-500">{a.name}</div></div> },
              { key: 'line', label: 'Line' },
              { key: 'status', label: 'Status', render: (a) => <StatusBadge status={a.status} /> },
              { key: 'health_score', label: 'Health score', align: 'right', render: (a) => a.health_score ?? '–' },
              { key: 'active_alarms', label: 'Active alarms', align: 'right' },
            ]}
          />
        )}
      </Card>
    </div>
  )
}
