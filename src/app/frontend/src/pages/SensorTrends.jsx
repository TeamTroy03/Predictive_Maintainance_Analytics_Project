import { useSearchParams } from 'react-router-dom'
import { api } from '../api/client.js'
import { useApi } from '../hooks/useApi.js'
import { PageHeader, Segmented, Loading, ErrorBox } from '../components/ui.jsx'
import SensorCharts from '../components/SensorCharts.jsx'
import { useAgent } from '../components/AgentPanel.jsx'

// Counted back from the asset's last reading. Real sensors report about once a day, so short windows hold
// only a point or two: the default is 30 days.
const RANGES = [
  { value: '24h', label: '24 h' },
  { value: '7d', label: '7 days' },
  { value: '30d', label: '30 days' },
  { value: 'all', label: 'All' },
]

export default function SensorTrends() {
  // Asset, range and hidden sensors are kept in the URL, so a view can be shared as a link
  const [params, setParams] = useSearchParams()
  const range = params.get('range') || '30d'
  const hidden = (params.get('hide') || '').split(',').filter(Boolean)
  const { ask } = useAgent()

  const update = (changes) => {
    const next = new URLSearchParams(params)
    Object.entries(changes).forEach(([k, v]) => (v ? next.set(k, v) : next.delete(k)))
    setParams(next)
  }

  // Only assets that have sensor readings can be picked (the server lists the most urgent first)
  const assets = useApi(() => api.getAssets({ has_sensors: true, page_size: 100 }))
  const choices = assets.data?.items ?? []
  const assetId = params.get('asset') || choices[0]?.id
  const series = useApi(() => (assetId ? api.getSensorSeries(assetId, range) : Promise.resolve(null)), [assetId, range])

  const toggleSensor = (id) => {
    const next = hidden.includes(id) ? hidden.filter((h) => h !== id) : [...hidden, id]
    update({ hide: next.join(',') })
  }

  if (assets.error) return <ErrorBox error={assets.error} onRetry={assets.reload} />

  return (
    <div>
      <PageHeader title="Sensor Trends & Telemetry" subtitle="Hover over any chart to compare all sensors at the same moment">
        <select
          value={assetId ?? ''}
          onChange={(e) => update({ asset: e.target.value })}
          className="rounded-md border border-line bg-white px-3 py-1.5 text-sm"
        >
          {choices.map((a) => (
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
      {!assets.loading && !assetId && <p className="py-10 text-center text-sm text-gray-500">No assets have sensor readings.</p>}
      {(assets.loading || (assetId && !series.data)) && !series.error && <Loading />}

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