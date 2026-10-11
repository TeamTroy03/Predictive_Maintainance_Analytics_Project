import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api/client.js'
import { useApi } from '../hooks/useApi.js'
import { Card, PageHeader, StatusBadge, Loading, ErrorBox, Table, Segmented } from '../components/ui.jsx'
import { fmtDate, fmtNumber } from '../format.js'

const FILTERS = [
  { value: '', label: 'All' },
  { value: 'critical', label: 'Critical' },
  { value: 'warning', label: 'Warning' },
  { value: 'operational', label: 'Operational' },
  { value: 'offline', label: 'Offline' },
]

export default function EquipmentList() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const status = params.get('status') || ''
  const search = params.get('q') || ''
  const assets = useApi(() => api.getAssets())

  const setParam = (key, value) => {
    const next = new URLSearchParams(params)
    value ? next.set(key, value) : next.delete(key)
    setParams(next, { replace: true })
  }

  if (assets.error) return <ErrorBox error={assets.error} onRetry={assets.reload} />
  if (!assets.data) return <Loading />

  const rows = assets.data.filter(
    (a) => (!status || a.status === status) && `${a.id} ${a.name} ${a.type} ${a.line}`.toLowerCase().includes(search.toLowerCase()),
  )

  return (
    <div>
      <PageHeader title="Equipment" subtitle="Click an asset to drill into its sensors, components and maintenance history" />
      <Card
        title={`${rows.length} of ${assets.data.length} assets`}
        action={
          <div className="flex flex-wrap gap-2">
            <input
              value={search}
              onChange={(e) => setParam('q', e.target.value)}
              placeholder="Search asset, type, line…"
              className="w-56 rounded-md border border-line px-3 py-1 text-sm outline-none focus:border-brand"
            />
            <Segmented options={FILTERS} value={status} onChange={(v) => setParam('status', v)} />
          </div>
        }
      >
        <Table
          rows={rows}
          onRowClick={(a) => navigate(`/equipment/${a.id}`)}
          empty="No assets match the filter"
          columns={[
            { key: 'id', label: 'Asset', render: (a) => <div><div className="font-semibold">{a.id}</div><div className="text-xs text-gray-500">{a.name}</div></div> },
            { key: 'type', label: 'Type' },
            { key: 'line', label: 'Line' },
            { key: 'status', label: 'Status', render: (a) => <StatusBadge status={a.status} /> },
            { key: 'health_score', label: 'Health', align: 'right', render: (a) => a.health_score ?? '–' },
            { key: 'run_hours', label: 'Run hours', align: 'right', render: (a) => fmtNumber(a.run_hours, 0) },
            { key: 'active_alarms', label: 'Alarms', align: 'right' },
            { key: 'last_maintenance', label: 'Last maintenance', render: (a) => fmtDate(a.last_maintenance) },
          ]}
        />
      </Card>
    </div>
  )
}
