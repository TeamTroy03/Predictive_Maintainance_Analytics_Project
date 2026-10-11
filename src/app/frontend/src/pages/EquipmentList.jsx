import { useEffect, useState } from 'react'
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
const PAGE_SIZE = 50

export default function EquipmentList() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const status = params.get('status') || ''
  const search = params.get('q') || ''
  const page = Number(params.get('page')) || 1
  const [typed, setTyped] = useState(search)

  // Filtering, search and paging happen on the server: there are thousands of assets
  const assets = useApi(() => api.getAssets({ status, q: search, page, page_size: PAGE_SIZE }), [status, search, page])

  const setParam = (changes) => {
    const next = new URLSearchParams(params)
    Object.entries(changes).forEach(([key, value]) => (value ? next.set(key, value) : next.delete(key)))
    setParams(next, { replace: true })
  }

  // One request when typing pauses, not one per keystroke
  useEffect(() => {
    if (typed === search) return undefined
    const timer = setTimeout(() => setParam({ q: typed, page: '' }), 400)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [typed])

  if (assets.error) return <ErrorBox error={assets.error} onRetry={assets.reload} />
  if (!assets.data) return <Loading />

  const { items, total } = assets.data
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div>
      <PageHeader title="Equipment" subtitle="Most urgent first · click an asset to drill into its sensors and maintenance history" />
      <Card
        title={`${total.toLocaleString('en-GB')} assets`}
        action={
          <div className="flex flex-wrap items-center gap-2">
            {assets.loading && <span className="text-xs text-gray-500">Updating…</span>}
            <input
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder="Search asset, type, line…"
              className="w-56 rounded-md border border-line px-3 py-1 text-sm outline-none focus:border-brand"
            />
            <Segmented options={FILTERS} value={status} onChange={(v) => setParam({ status: v, page: '' })} />
          </div>
        }
      >
        <Table
          rows={items}
          onRowClick={(a) => navigate(`/equipment/${a.id}`)}
          empty="No assets match the filter"
          columns={[
            { key: 'id', label: 'Asset', render: (a) => <div><div className="font-semibold">{a.id}</div><div className="text-xs text-gray-500">{a.name}</div></div> },
            { key: 'type', label: 'Type' },
            { key: 'line', label: 'Line' },
            { key: 'status', label: 'Status', render: (a) => <StatusBadge status={a.status} /> },
            { key: 'availability_pct', label: 'Availability', align: 'right', render: (a) => (a.availability_pct == null ? '–' : `${fmtNumber(a.availability_pct, 2)} %`) },
            { key: 'active_alarms', label: 'Alarms', align: 'right' },
            { key: 'last_maintenance', label: 'Last maintenance', render: (a) => fmtDate(a.last_maintenance) },
          ]}
        />
        {pages > 1 && (
          <div className="mt-3 flex items-center justify-between text-sm text-gray-600">
            <span>Page {page} of {pages}</span>
            <div className="flex gap-2">
              <button disabled={page <= 1} onClick={() => setParam({ page: String(page - 1) })} className="rounded border border-line px-3 py-1 disabled:opacity-40">Previous</button>
              <button disabled={page >= pages} onClick={() => setParam({ page: String(page + 1) })} className="rounded border border-line px-3 py-1 disabled:opacity-40">Next</button>
            </div>
          </div>
        )}
      </Card>
    </div>
  )
}