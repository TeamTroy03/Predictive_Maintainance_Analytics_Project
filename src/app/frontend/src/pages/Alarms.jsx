import { Fragment, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, isMockMode } from '../api/client.js'
import { useApi } from '../hooks/useApi.js'
import { Card, PageHeader, StatusBadge, Loading, ErrorBox, Table, Segmented } from '../components/ui.jsx'
import { useAgent } from '../components/AgentPanel.jsx'
import { useAsOf } from '../asOf.jsx'
import { fmtDateTimeUtc, fmtNumber, timeAgo, unitLabel } from '../format.js'

const SEVERITY_RANK = { critical: 0, warning: 1, info: 2 }
// Gold keeps no acknowledged/cleared state, so acknowledging only exists in the demo (mock) mode
const CAN_ACKNOWLEDGE = isMockMode

// Gold has no dismissals: the API derives `state` (active until the sensor reports a normal reading, then cleared)
const stateOf = (a) => a.state ?? (a.acknowledged ? 'cleared' : 'active')
const readingText = (a) =>
  a.value == null ? 'No value' : `${Number(a.value.toFixed(2))} ${unitLabel(a.unit)}${a.threshold == null ? '' : ` / limit ${a.threshold}`}`.trim()
const byTimeDesc = (a, b) => new Date(b.time) - new Date(a.time)

const assetLink = (id) => (
  <Link to={`/equipment/${id}`} onClick={(e) => e.stopPropagation()} className="font-semibold text-brand underline-offset-2 hover:underline">
    {id}
  </Link>
)

function askAboutAlarm(ask, a) {
  ask(`Explain alarm ${a.id} on ${a.asset_id}: "${a.message}". What is the likely root cause and what should we do?`, { asset_id: a.asset_id, alarm_id: a.id })
}

function AskButton({ alarm }) {
  const { ask } = useAgent()
  return (
    <button
      onClick={() => askAboutAlarm(ask, alarm)}
      className="rounded border border-brand/30 bg-brand-light px-2 py-1 text-xs font-medium text-brand hover:bg-brand hover:text-white"
    >
      ✦ Ask AI
    </button>
  )
}

// One row per asset, with the individual alarms behind a chevron
function groupByAsset(alarms) {
  const groups = new Map()
  for (const a of alarms) {
    if (!groups.has(a.asset_id)) groups.set(a.asset_id, [])
    groups.get(a.asset_id).push(a)
  }
  return [...groups.entries()]
    .map(([asset_id, items]) => {
      const sorted = [...items].sort(byTimeDesc) // newest first
      return {
        asset_id,
        items: sorted,
        latest: sorted[0],
        first: sorted[sorted.length - 1].time,
        severity: items.reduce((w, a) => (SEVERITY_RANK[a.severity] < SEVERITY_RANK[w] ? a.severity : w), 'info'),
      }
    })
    .sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity] || byTimeDesc(a.latest, b.latest))
}

const TH = 'whitespace-nowrap px-3 py-2 font-semibold'

function GroupedAlarms({ groups, empty, asOf }) {
  const [open, setOpen] = useState(() => new Set())
  const toggle = (id) =>
    setOpen((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  if (groups.length === 0) return <div className="px-3 py-8 text-center text-sm text-gray-500">{empty}</div>
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line bg-canvas text-left text-[11px] uppercase tracking-wide text-gray-500">
            <th className="w-8" />
            <th className={TH}>Asset</th>
            <th className={TH}>Severity</th>
            <th className={TH}>Status</th>
            <th className={`${TH} text-right`}>Occurrences</th>
            <th className={TH}>First</th>
            <th className={TH}>Last</th>
            <th className={`${TH} text-right`}>Latest reading</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((g) => {
            const expanded = open.has(g.asset_id)
            const active = g.items.some((a) => stateOf(a) === 'active')
            return (
              <Fragment key={g.asset_id}>
                <tr onClick={() => toggle(g.asset_id)} className="group cursor-pointer border-b border-line hover:bg-brand-light/60">
                  <td className="pl-3 text-gray-400 group-hover:text-brand">
                    <button aria-expanded={expanded} aria-label={`${expanded ? 'Hide' : 'Show'} alarms of ${g.asset_id}`} className="inline-block w-4 text-center">
                      {expanded ? '▾' : '▸'}
                    </button>
                  </td>
                  <td className="px-3 py-2">{assetLink(g.asset_id)}</td>
                  <td className="px-3 py-2"><StatusBadge status={g.severity} /></td>
                  <td className="px-3 py-2"><StatusBadge status={active ? 'active' : 'cleared'} /></td>
                  <td className="num px-3 py-2 text-right">{fmtNumber(g.items.length, 0)}</td>
                  <td className="whitespace-nowrap px-3 py-2" title={`${fmtDateTimeUtc(g.first)} · ${timeAgo(g.first, asOf ?? undefined)}`}>{fmtDateTimeUtc(g.first)}</td>
                  <td className="whitespace-nowrap px-3 py-2" title={fmtDateTimeUtc(g.latest.time)}>
                    {fmtDateTimeUtc(g.latest.time)} <span className="text-xs text-gray-500">· {timeAgo(g.latest.time, asOf ?? undefined)}</span>
                  </td>
                  <td className="num whitespace-nowrap px-3 py-2 text-right">{readingText(g.latest)}</td>
                </tr>
                {expanded && (
                  <tr className="border-b border-line bg-canvas/60">
                    <td />
                    <td colSpan={7} className="px-3 py-2">
                      <AlarmTable rows={g.items} showAsset={false} asOf={asOf} />
                    </td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export default function Alarms() {
  const [tab, setTab] = useState('active')
  const [severity, setSeverity] = useState('')
  const alarms = useApi(() => api.getAlarms({ limit: 500 }))
  const { asOf } = useAsOf()

  if (alarms.error) return <ErrorBox error={alarms.error} onRetry={alarms.reload} />
  if (!alarms.data) return <Loading />

  const isActive = (a) => stateOf(a) === 'active'
  const activeCount = alarms.data.filter(isActive).length
  const historyCount = alarms.data.length - activeCount
  const inTab = alarms.data.filter((a) => isActive(a) === (tab === 'active'))
  const count = (s) => inTab.filter((a) => a.severity === s).length
  const rows = inTab.filter((a) => !severity || a.severity === severity)
  const hasLimits = alarms.data.some((a) => a.threshold != null)
  const through = asOf ? ` · data through ${fmtDateTimeUtc(asOf)}` : ''
  const subtitle =
    tab === 'active'
      ? `Alarms whose sensor has not reported a normal reading since · one row per asset${through}`
      : `Earlier alarms, sensor back to normal · one row per asset, latest first${through}`

  return (
    <div>
      <PageHeader title="Critical Exceptions" subtitle={subtitle}>
        <Segmented
          value={tab}
          onChange={(t) => { setTab(t); setSeverity('') }}
          options={[
            { value: 'active', label: `Active (${fmtNumber(activeCount, 0)})` },
            { value: 'history', label: `History (${fmtNumber(historyCount, 0)})` },
          ]}
        />
      </PageHeader>
      <Card
        action={
          <Segmented
            value={severity}
            onChange={setSeverity}
            options={[
              { value: '', label: `All severities (${fmtNumber(inTab.length, 0)})` },
              { value: 'critical', label: `Critical (${fmtNumber(count('critical'), 0)})` },
              { value: 'warning', label: `Warning (${fmtNumber(count('warning'), 0)})` },
            ]}
          />
        }
      >
        <GroupedAlarms
          groups={groupByAsset(rows)}
          asOf={asOf}
          empty={tab === 'active' ? `No active ${severity ? `${severity} ` : 'critical '}exceptions` : 'No alarm history'}
        />
      </Card>
      <p className="mt-3 text-xs text-gray-500">
        Gold records no acknowledgement or dismissal, so there is no “dismissed by” or “acknowledged”: an alarm is shown as cleared once its sensor reports a normal reading.
        {!hasLimits && ' Alarm limits are in the source data but not in Gold yet, so readings are shown without a limit.'}
        {alarms.data.length >= 500 && ' Showing the latest 500 alarm readings.'}
      </p>
    </div>
  )
}

// The individual alarms. Also used on the Equipment detail page
export function AlarmTable({ rows, showAsset = true, onChanged, asOf }) {
  const [busyId, setBusyId] = useState(null)
  const { asOf: dataThrough } = useAsOf()
  const ref = asOf ?? dataThrough ?? undefined

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
    (a, b) => (stateOf(a) === 'active' ? 0 : 1) - (stateOf(b) === 'active' ? 0 : 1) || SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity] || byTimeDesc(a, b),
  )

  const columns = [
    { key: 'severity', label: 'Severity', render: (a) => <StatusBadge status={a.severity} /> },
    { key: 'state', label: 'Status', render: (a) => <StatusBadge status={stateOf(a)} /> },
    { key: 'time', label: 'Raised', render: (a) => <span title={timeAgo(a.time, ref)}>{fmtDateTimeUtc(a.time)}</span> },
    showAsset && { key: 'asset', label: 'Asset', render: (a) => assetLink(a.asset_id) },
    { key: 'message', label: 'Condition', wrap: true },
    { key: 'value', label: 'Reading', align: 'right', render: readingText },
    {
      key: 'actions',
      label: '',
      render: (a) => (
        <div className="flex justify-end gap-2">
          {CAN_ACKNOWLEDGE && !a.acknowledged && (
            <button disabled={busyId === a.id} onClick={() => acknowledge(a.id)} className="rounded border border-crit/40 bg-white px-2 py-1 text-xs font-medium text-crit hover:bg-crit hover:text-white disabled:opacity-50">
              Acknowledge
            </button>
          )}
          <AskButton alarm={a} />
        </div>
      ),
    },
  ].filter(Boolean)

  return <Table rows={sorted} columns={columns} empty="No alarms" />
}