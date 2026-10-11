import { useSearchParams } from 'react-router-dom'
import { api } from '../api/client.js'
import { useApi } from '../hooks/useApi.js'
import { PageHeader, Segmented, Loading, ErrorBox } from '../components/ui.jsx'
import SensorCharts from '../components/SensorCharts.jsx'
import { useAgent } from '../components/AgentPanel.jsx'

const RANGES = [
  { value: '1h', label: '1 h' },
  { value: '24h', label: '24 h' },
  { value: '7d', label: '7 days' },
]

export default function SensorTrends() {
  // Asset, range and hidden sensors are kept in the URL, so a view can be shared as a link
  const [params, setParams] = useSearchParams()
  const assetId = params.get('asset') || 'PUMP-204B'
  const range = params.get('range') || '24h'
  const hidden = (params.get('hide') || '').split(',').filter(Boolean)
  const { ask } = useAgent()

  const update = (changes) => {
    const next = new URLSearchParams(params)
    Object.entries(changes).forEach(([k, v]) => (v ? next.set(k, v) : next.delete(k)))
    setParams(next)
  }

  const assets = useApi(() => api.getAssets())
  const series = useApi(() => api.getSensorSeries(assetId, range), [assetId, range])

  const toggleSensor = (id) => {
    const next = hidden.includes(id) ? hidden.filter((h) => h !== id) : [...hidden, id]
    update({ hide: next.join(',') })
  }

  return (
    <div>
      <PageHeader title="Sensor Trends & Telemetry" subtitle="Hover over any chart to compare all sensors at the same moment">
        <select
          value={assetId}
          onChange={(e) => update({ asset: e.target.value })}
          className="rounded-md border border-line bg-white px-3 py-1.5 text-sm"
        >
          {(assets.data ?? [{ id: assetId, name: '' }]).map((a) => (
            <option key={a.id} value={a.id}>
              {a.id} {a.name && `– ${a.name}`}
            </option>
          ))}
        </select>
        <Segmented options={RANGES} value={range} onChange={(r) => update({ range: r })} />
        <button
          onClick={() => ask(`Explain the sensor anomalies on ${assetId} in the last ${range}.`)}
          className="rounded-md border border-brand/30 bg-brand-light px-3 py-1.5 text-sm font-medium text-brand hover:bg-brand hover:text-white"
        >
          ✦ Explain this window
        </button>
      </PageHeader>

      {series.error && <ErrorBox error={series.error} onRetry={series.reload} />}
      {!series.data && !series.error && <Loading />}

      {series.data && (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <span className="text-xs text-gray-500">Show sensors:</span>
            {series.data.sensors.map((s) => (
              <label key={s.id} className="flex cursor-pointer items-center gap-1.5 rounded-md border border-line bg-white px-2 py-1 text-xs">
                <input type="checkbox" checked={!hidden.includes(s.id)} onChange={() => toggleSensor(s.id)} className="accent-brand" />
                {s.name}
              </label>
            ))}
            {series.loading && <span className="text-xs text-gray-500">Updating…</span>}
          </div>
          <SensorCharts sensors={series.data.sensors.filter((s) => !hidden.includes(s.id))} range={range} />
        </>
      )}
    </div>
  )
}
