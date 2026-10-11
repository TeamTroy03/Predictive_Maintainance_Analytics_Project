import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client.js'
import { useApi } from '../hooks/useApi.js'
import { Card, PageHeader, StatusBadge, Loading, ErrorBox, Table, Segmented } from '../components/ui.jsx'
import { useAgent } from '../components/AgentPanel.jsx'
import { fmtDateTime, timeAgo } from '../format.js'

const SEVERITY_RANK = { critical: 0, warning: 1, info: 2 }
const REFRESH_SECONDS = 30

export default function Alarms() {
  const [severity, setSeverity] = useState('')
  const [showAcked, setShowAcked] = useState(false)
  const alarms = useApi(() => api.getAlarms())

  // Simple polling: reload the alarm list every 30 seconds
  useEffect(() => {
    const timer = setInterval(alarms.reload, REFRESH_SECONDS * 1000)
    return () => clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (alarms.error) return <ErrorBox error={alarms.error} onRetry={alarms.reload} />
  if (!alarms.data) return <Loading />

  const active = alarms.data.filter((a) => showAcked || !a.acknowledged)
  const count = (s) => active.filter((a) => a.severity === s).length
  const rows = active.filter((a) => !severity || a.severity === severity)

  return (
    <div>
      <PageHeader title="Critical Exceptions & Alarms" subtitle={`Unacknowledged alarms first, then by severity · refreshes every ${REFRESH_SECONDS} s`}>
        <label className="flex items-center gap-2 text-sm text-gray-600">
          <input type="checkbox" checked={showAcked} onChange={(e) => setShowAcked(e.target.checked)} className="accent-brand" />
          Show acknowledged
        </label>
        <Segmented
          value={severity}
          onChange={setSeverity}
          options={[
            { value: '', label: `All (${active.length})` },
            { value: 'critical', label: `Critical (${count('critical')})` },
            { value: 'warning', label: `Warning (${count('warning')})` },
            { value: 'info', label: `Info (${count('info')})` },
          ]}
        />
      </PageHeader>
      <Card>
        <AlarmTable rows={rows} onChanged={alarms.reload} />
      </Card>
    </div>
  )
}

// Also used on the Equipment detail page
export function AlarmTable({ rows, showAsset = true, onChanged }) {
  const { ask } = useAgent()
  const [busyId, setBusyId] = useState(null)

  async function acknowledge(id) {
    setBusyId(id)
    try {
      await api.acknowledgeAlarm(id)
      onChanged?.()
    } finally {
      setBusyId(null)
    }
  }

  const sorted = [...rows].sort(
    (a, b) => a.acknowledged - b.acknowledged || SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity] || new Date(b.time) - new Date(a.time),
  )

  const columns = [
    { key: 'severity', label: 'Severity', render: (a) => <StatusBadge status={a.severity} /> },
    { key: 'time', label: 'Raised', render: (a) => <span title={fmtDateTime(a.time)}>{timeAgo(a.time)}</span> },
    showAsset && { key: 'asset', label: 'Asset', render: (a) => <Link to={`/equipment/${a.asset_id}`} className="font-semibold hover:text-brand">{a.asset_id}</Link> },
    { key: 'message', label: 'Condition', wrap: true },
    { key: 'value', label: 'Value / limit', align: 'right', render: (a) => (a.value == null ? '–' : `${a.value} / ${a.threshold} ${a.unit}`) },
    {
      key: 'actions',
      label: '',
      render: (a) => (
        <div className="flex justify-end gap-2">
          {a.acknowledged ? (
            <span className="px-2 py-1 text-xs text-gray-400">Acknowledged</span>
          ) : (
            <button disabled={busyId === a.id} onClick={() => acknowledge(a.id)} className="rounded border border-crit/40 bg-white px-2 py-1 text-xs font-medium text-crit hover:bg-crit hover:text-white disabled:opacity-50">
              Acknowledge
            </button>
          )}
          <button
            onClick={() => ask(`Explain alarm ${a.id} on ${a.asset_id}: "${a.message}". What is the likely root cause and what should we do?`, { asset_id: a.asset_id, alarm_id: a.id })}
            className="rounded border border-brand/30 bg-brand-light px-2 py-1 text-xs font-medium text-brand hover:bg-brand hover:text-white"
          >
            ✦ Ask AI
          </button>
        </div>
      ),
    },
  ].filter(Boolean)

  return <Table rows={sorted} columns={columns} empty="No active alarms" />
}
