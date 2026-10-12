import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api/client.js'
import { useApi } from '../hooks/useApi.js'
import { Card, PageHeader, StatusBadge, Loading, ErrorBox, Table, Segmented } from '../components/ui.jsx'
import { useAsOf } from '../asOf.jsx'
import { fmtDate, fmtNumber, timeAgo } from '../format.js'

const STATUSES = ['critical', 'warning', 'operational', 'offline']
const LABELS = { '': 'All', critical: 'Critical', warning: 'Warning', operational: 'Operational', offline: 'Offline' }
const LINES = ['L1', 'L2', 'L3', 'L4']
const PAGE_SIZE = 50

// The first one is the default
const SORTS = [
  { value: 'maintenance', label: 'Time since last maintenance', subtitle: 'Longest since last maintenance first · assets with no maintenance record last' },
  { value: 'alarm', label: 'Last alarm date', subtitle: 'Most recent alarm first · assets that never alarmed last' },
  { value: 'status', label: 'Status', subtitle: 'Critical, then warning, then offline, then operational' },
]

const Muted = ({ children, title }) => <span title={title} className="text-gray-400">{children}</span>

export default function EquipmentList() {
  const navigate = useNavigate()
  const { asOf } = useAsOf()
  const [params, setParams] = useSearchParams()
  const status = params.get('status') || ''
  const search = params.get('q') || ''
  const line = params.get('line') || ''
  const sort = params.get('sort') || SORTS[0].value
  const page = Number(params.get('page')) || 1
  const [typed, setTyped] = useState(search)

  // Filtering, sorting, search and paging happen on the server: there are thousands of assets
  const assets = useApi(
    () => api.getAssets({ status, q: search, line, sort, page, page_size: PAGE_SIZE }),
    [status, search, line, sort, page],
  )
  // Chip counts follow the line filter (not the search box, so they stay cacheable on the server)
  const counts = useApi(
    () => Promise.all(STATUSES.map((s) => api.getAssets({ status: s, line, page_size: 1 }).then((r) => r.total))),
    [line],
  )

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
  const first = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1
  const chipLabel = (value) => {
    const n = value === '' ? counts.data?.reduce((a, b) => a + b, 0) : counts.data?.[STATUSES.indexOf(value)]
    return n == null ? LABELS[value] : `${LABELS[value]} (${fmtNumber(n, 0)})`
  }
  const filters = ['', ...STATUSES].map((value) => ({ value, label: chipLabel(value) }))

  return (
    <div>
      <PageHeader title="Equipment" subtitle={`${SORTS.find((s) => s.value === sort)?.subtitle ?? ''} · click an asset to drill into its sensors and maintenance history`}>
        <label className="flex items-center gap-2 text-sm text-gray-600">
          Sort by
          <select value={sort} onChange={(e) => setParam({ sort: e.target.value === SORTS[0].value ? '' : e.target.value, page: '' })} className="rounded-md border border-line bg-white px-2 py-1.5 text-sm">
            {SORTS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm text-gray-600">
          Line
          <select value={line} onChange={(e) => setParam({ line: e.target.value, page: '' })} className="rounded-md border border-line bg-white px-2 py-1.5 text-sm">
            <option value="">All lines</option>
            {LINES.map((l) => <option key={l} value={l}>{l}</option>)}
          </select>
        </label>
      </PageHeader>
      <Card
        title={`${fmtNumber(total, 0)} assets`}
        action={
          <div className="flex flex-wrap items-center gap-2">
            {assets.loading && <span className="text-xs text-gray-500">Updating…</span>}
            <input
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder="Search asset, type, line…"
              className="w-56 rounded-md border border-line px-3 py-1 text-sm outline-none focus:border-brand"
            />
            <Segmented options={filters} value={status} onChange={(v) => setParam({ status: v, page: '' })} />
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
            {
              key: 'availability_pct',
              label: 'Availability',
              align: 'right',
              render: (a) =>
                a.status === 'offline' ? (
                  <Muted title="Availability is not shown for offline (decommissioned) assets">Offline</Muted>
                ) : a.availability_pct == null ? (
                  <Muted>No data</Muted>
                ) : (
                  `${fmtNumber(a.availability_pct, 2)} %`
                ),
            },
            {
              key: 'last_alarm',
              label: 'Last alarm',
              render: (a) =>
                a.last_alarm ? (
                  <span title={fmtDate(a.last_alarm)}>
                    {timeAgo(a.last_alarm, asOf ?? undefined)} <span className="text-xs text-gray-500">({a.last_alarm_state})</span>
                  </span>
                ) : (
                  <Muted>No alarms</Muted>
                ),
            },
            {
              key: 'last_maintenance',
              label: 'Last maintenance',
              render: (a) => (a.last_maintenance ? fmtDate(a.last_maintenance) : <Muted>No record</Muted>),
            },
          ]}
        />
        <div className="mt-3 flex items-center justify-between text-sm text-gray-600">
          <span>
            Showing {fmtNumber(first, 0)}–{fmtNumber(Math.min(page * PAGE_SIZE, total), 0)} of {fmtNumber(total, 0)} · page {fmtNumber(page, 0)} of {fmtNumber(pages, 0)}
          </span>
          <div className="flex gap-2">
            <button disabled={page <= 1} onClick={() => setParam({ page: '' })} className="rounded border border-line px-3 py-1 disabled:opacity-40">First</button>
            <button disabled={page <= 1} onClick={() => setParam({ page: page - 1 > 1 ? String(page - 1) : '' })} className="rounded border border-line px-3 py-1 disabled:opacity-40">Previous</button>
            <button disabled={page >= pages} onClick={() => setParam({ page: String(page + 1) })} className="rounded border border-line px-3 py-1 disabled:opacity-40">Next</button>
            <button disabled={page >= pages} onClick={() => setParam({ page: String(pages) })} className="rounded border border-line px-3 py-1 disabled:opacity-40">Last</button>
          </div>
        </div>
      </Card>
    </div>
  )
}