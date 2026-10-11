// Helpers to display numbers and dates consistently everywhere
export const fmtNumber = (v, digits = 1) => (v === null || v === undefined ? '–' : Number(v).toLocaleString('en-GB', { maximumFractionDigits: digits }))
export const fmtEuro = (v) => (v === null || v === undefined ? '–' : `€${Number(v).toLocaleString('en-GB', { maximumFractionDigits: 0 })}`)
export const fmtDate = (s) => (s ? new Date(s).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }) : '–')
export const fmtDateTime = (s) => (s ? new Date(s).toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : '–')

export function timeAgo(s) {
  if (!s) return '–'
  const minutes = Math.round((Date.now() - new Date(s).getTime()) / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} h ago`
  return `${Math.round(hours / 24)} d ago`
}
