// Helpers to display numbers and dates consistently everywhere
export const fmtNumber = (v, digits = 1) => (v === null || v === undefined ? '–' : Number(v).toLocaleString('en-GB', { maximumFractionDigits: digits }))
export const fmtEuro = (v) => (v === null || v === undefined ? '–' : `€${Number(v).toLocaleString('en-GB', { maximumFractionDigits: 0 })}`)
export const fmtDate = (s) => (s ? new Date(s).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }) : '–')
export const fmtDateUtc = (s) => (s ? new Date(s).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' }) : '–')
export const fmtDateTime =(s) => (s ? new Date(s).toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : '–')
// The data is UTC and historical: show the zone, so "20:04" is not read as local time
export const fmtDateTimeUtc = (s) =>
  s ? `${new Date(s).toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' })} UTC` : '–'

// Gold stores the canonical unit as ASCII ("degC")
const UNIT_LABELS = { degC: '°C', degF: '°F' }
export const unitLabel = (u) => UNIT_LABELS[u] ?? u ?? ''

// "X ago" counted from `ref` (the end of the data, see asOf.jsx), not from today: the dataset is historical.
// Without a reference it falls back to the real clock.
export function timeAgo(s, ref = Date.now()) {
  if (!s) return '–'
  const minutes = Math.round((new Date(ref).getTime() - new Date(s).getTime()) / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} h ago`
  return `${Math.round(hours / 24)} d ago`
}