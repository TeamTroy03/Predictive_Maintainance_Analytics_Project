import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client.js'
import { useApi } from '../hooks/useApi.js'
import { Card, KpiCard, StatusBadge, Loading, ErrorBox, Segmented } from '../components/ui.jsx'
import SensorCharts from '../components/SensorCharts.jsx'
import { WorkOrderTable } from './Maintenance.jsx'
import { AlarmTable } from './Alarms.jsx'
import { useAgent } from '../components/AgentPanel.jsx'
import { fmtDate, fmtNumber } from '../format.js'

const RANGES = [
  { value: '1h', label: '1 h' },
  { value: '24h', label: '24 h' },
  { value: '7d', label: '7 days' },
]

export default function EquipmentDetail() {
  const { assetId } = useParams()
  const [range, setRange] = useState('24h')
  const { ask } = useAgent()

  const asset = useApi(() => api.getAsset(assetId), [assetId])
  const series = useApi(() => api.getSensorSeries(assetId, range), [assetId, range])
  const workOrders = useApi(() => api.getWorkOrders({ asset_id: assetId }), [assetId])
  const alarms = useApi(() => api.getAlarms({ asset_id: assetId }), [assetId])

  if (asset.error) return <ErrorBox error={asset.error} onRetry={asset.reload} />
  if (!asset.data) return <Loading />
  const a = asset.data

  return (
    <div>
      <Link to="/equipment" className="text-sm text-gray-500 hover:text-brand">← All equipment</Link>

      {/* Header */}
      <div className="mt-2 mb-5 flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-semibold">{a.id}</h1>
            <StatusBadge status={a.status} />
          </div>
          <p className="mt-1 text-sm text-gray-600">{a.name}</p>
          <p className="mt-1 text-xs text-gray-500">
            {a.manufacturer} · {a.line} · installed {fmtDate(a.installed)} · {fmtNumber(a.run_hours, 0)} run hours
          </p>
        </div>
        <div className="flex gap-2">
          <Link to={`/sensors?asset=${a.id}`} className="rounded-md border border-line bg-white px-3 py-1.5 text-sm hover:border-brand">
            Open in Sensor Trends
          </Link>
          <button
            onClick={() => ask(`Why is ${a.id} in "${a.status}" state and what should we do?`)}
            className="rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
          >
            ✦ Ask AI about this asset
          </button>
        </div>
      </div>

      {/* KPIs for this asset */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <KpiCard label="Availability" value={fmtNumber(a.kpis.availability_pct)} unit="%" tone={a.kpis.availability_pct >= 95 ? 'good' : 'bad'} />
        <KpiCard label="MTBF" value={fmtNumber(a.kpis.mtbf_h, 0)} unit="h" />
        <KpiCard label="MTTR" value={fmtNumber(a.kpis.mttr_h)} unit="h" />
        <KpiCard label="Unplanned downtime" value={fmtNumber(a.kpis.unplanned_downtime_h)} unit="h" hint="Last 30 days" />
        <KpiCard
          label="Remaining useful life"
          value={a.rul_days ?? '–'}
          unit={a.rul_days ? 'days' : ''}
          hint={a.predicted_failure ? `Predicted: ${a.predicted_failure}` : 'No failure predicted'}
          tone={a.rul_days == null ? 'good' : a.rul_days < 7 ? 'bad' : 'warn'}
        />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card title="Latest sensor readings">
          <ul className="divide-y divide-line">
            {a.latest_readings.map((r) => (
              <li key={r.sensor} className="flex items-center justify-between py-2 text-sm">
                <span>{r.sensor}</span>
                <span className="flex items-center gap-3">
                  <span className="num font-semibold">{r.value ?? '–'} {r.unit}</span>
                  <StatusBadge status={r.status} />
                </span>
              </li>
            ))}
          </ul>
        </Card>
        <Card title="Components">
          <ul className="divide-y divide-line">
            {a.components.map((c) => (
              <li key={c.name} className="flex items-center justify-between py-2 text-sm">
                <span>{c.name}</span>
                <StatusBadge status={c.status} />
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <Card title="Sensor trends" className="mt-4" action={<Segmented options={RANGES} value={range} onChange={setRange} />}>
        {series.error && <ErrorBox error={series.error} onRetry={series.reload} />}
        {!series.data && !series.error && <Loading />}
        {series.data && <SensorCharts sensors={series.data.sensors} range={range} height={120} />}
      </Card>

      <Card title="Alarms on this asset" className="mt-4">
        {alarms.data ? <AlarmTable rows={alarms.data} showAsset={false} onChanged={alarms.reload} /> : <Loading />}
      </Card>

      <Card title="Maintenance history" className="mt-4">
        {workOrders.data ? <WorkOrderTable rows={workOrders.data} showAsset={false} /> : <Loading />}
      </Card>
    </div>
  )
}
