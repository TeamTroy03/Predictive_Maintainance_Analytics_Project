import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ResponsiveContainer, ComposedChart, Line, Bar, BarChart, XAxis, YAxis, Tooltip, Legend, CartesianGrid, Cell, LabelList } from 'recharts'
import { api } from '../api/client.js'
import { useApi } from '../hooks/useApi.js'
import { Card, PageHeader, KpiCard, StatusBadge, Loading, ErrorBox, Table, Segmented } from '../components/ui.jsx'
import { fmtNumber, fmtEuro, fmtDate } from '../format.js'

const STATUS_FILTERS = [
  { value: '', label: 'All' },
  { value: 'open', label: 'Open' },
  { value: 'overdue', label: 'Overdue' },
  { value: 'completed', label: 'Completed' },
]
const TYPE_COLORS = { Preventive: '#7e00c9', Predictive: '#a45ad9', Corrective: '#d1273f' }
const AXIS_TICK = { fontSize: 11, fill: '#4b5563' }
const fmtEuroM = (v) => `€${(v / 1e6).toLocaleString('en-GB', { minimumFractionDigits: 1, maximumFractionDigits: 2 })}M`

export default function Maintenance() {
  const [status, setStatus] = useState('')
  const summary = useApi(() => api.getMaintenanceSummary())
  const workOrders = useApi(() => api.getWorkOrders({ status }), [status])

  if (summary.error) return <ErrorBox error={summary.error} onRetry={summary.reload} />
  if (!summary.data) return <Loading />
  const { kpis, monthly, cost_by_type, period_from, period_to } = summary.data
  // Real data: the KPIs cover the whole Gold period. Mock data is "last 30 days" / "last 6 months".
  const period = period_from ? `${period_from} to ${period_to}` : 'last 30 days'
  const planned = kpis.planned_pct

  return (
    <div>
      <PageHeader title="Maintenance Performance" subtitle="Reliability, cost and work order backlog" />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <KpiCard label="MTBF" value={fmtNumber(kpis.mtbf_h, 0)} unit="h" hint="Higher is better" />
        <KpiCard label="MTTR" value={fmtNumber(kpis.mttr_h)} unit="h" hint="Lower is better" />
        <KpiCard label="Overdue work orders" value={fmtNumber(kpis.overdue_work_orders, 0)} hint="Open past due date" tone={kpis.overdue_work_orders > 0 ? 'warn' : 'good'} />
        <KpiCard label="Maintenance cost" value={fmtEuro(kpis.maintenance_cost_eur)} hint={period} />
        <KpiCard label="Planned vs reactive" value={planned == null ? '–' : `${fmtNumber(planned)}%`} hint={planned == null ? undefined : `${fmtNumber(100 - planned)}% reactive · target ≥ 85%`} tone={planned >= 85 ? 'good' : 'warn'} />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card title="MTBF vs MTTR" subtitle="Monthly trend" className="lg:col-span-2">
          <ResponsiveContainer width="100%" height={240}>
            <ComposedChart data={monthly} margin={{ top: 5, right: 0, left: -10, bottom: 0 }}>
              <CartesianGrid stroke="#f0f0f0" vertical={false} />
              <XAxis dataKey="month" tick={AXIS_TICK} />
              <YAxis yAxisId="mtbf" tick={AXIS_TICK} unit="h" />
              <YAxis yAxisId="mttr" orientation="right" tick={AXIS_TICK} unit="h" />
              <Tooltip contentStyle={{ fontSize: 12 }} />
              {/* Legend text in dark grey: by default it takes the (light) series colour */}
              <Legend wrapperStyle={{ fontSize: 12 }} formatter={(name) => <span style={{ color: '#1f2937' }}>{name}</span>} />
              <Bar yAxisId="mtbf" dataKey="mtbf_h" name="MTBF (h)" fill="#8b3fd0" isAnimationActive={false} />
              <Line yAxisId="mttr" dataKey="mttr_h" name="MTTR (h)" stroke="#d1273f" strokeWidth={2.5} dot={{ r: 3 }} type="linear" isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </Card>

        <Card title="Cost by maintenance type" subtitle={period_from ? period : 'Last 6 months'}>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={cost_by_type} layout="vertical" margin={{ top: 5, right: 55, left: 10, bottom: 0 }}>
              <XAxis type="number" tickFormatter={fmtEuroM} tick={AXIS_TICK} />
              <YAxis type="category" dataKey="type" tick={{ fontSize: 12, fill: '#1f2937' }} width={80} />
              <Tooltip formatter={(v) => [fmtEuro(v), 'Cost']} contentStyle={{ fontSize: 12 }} />
              <Bar dataKey="cost_eur" isAnimationActive={false}>
                {cost_by_type.map((c) => (
                  <Cell key={c.type} fill={TYPE_COLORS[c.type] ?? '#6b7280'} />
                ))}
                <LabelList dataKey="cost_eur" position="right" formatter={fmtEuroM} style={{ fontSize: 12, fontWeight: 600, fill: '#1f2937' }} />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Card>
      </div>

      <Card title="Work orders" className="mt-4" action={<Segmented options={STATUS_FILTERS} value={status} onChange={setStatus} />}>
        {workOrders.error && <ErrorBox error={workOrders.error} onRetry={workOrders.reload} />}
        {!workOrders.data && !workOrders.error && <Loading />}
        {workOrders.data && <WorkOrderTable rows={workOrders.data} />}
      </Card>
    </div>
  )
}

// Also used on the Equipment detail page
export function WorkOrderTable({ rows, showAsset = true }) {
  const showTechnician = rows.some((w) => w.technician) // Gold has no technician: the column only shows when there is one
  const columns = [
    { key: 'id', label: 'WO #', render: (w) => <span className="font-semibold text-brand">{w.id}</span> },
    showAsset && { key: 'asset', label: 'Asset', render: (w) => <Link to={`/equipment/${w.asset_id}`} className="hover:underline">{w.asset_id}</Link> },
    { key: 'title', label: 'Task', wrap: true },
    { key: 'type', label: 'Type' },
    { key: 'status', label: 'Status', render: (w) => <StatusBadge status={w.status} /> },
    showTechnician && { key: 'technician', label: 'Technician' },
    { key: 'due', label: 'Due', render: (w) => fmtDate(w.due) },
    { key: 'downtime_h', label: 'Downtime', align: 'right', render: (w) => (w.downtime_h ? `${w.downtime_h} h` : '–') },
    { key: 'cost', label: 'Cost', align: 'right', render: (w) => fmtEuro(w.cost) },
  ].filter(Boolean)
  return <Table rows={rows} columns={columns} empty="No work orders" />
}
