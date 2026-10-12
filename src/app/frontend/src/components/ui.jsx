// Small reusable building blocks used by every page

export function Card({ title, subtitle, action, children, className = '' }) {
  return (
    <section className={`rounded-lg border border-line bg-white ${className}`}>
      {(title || action) && (
        <header className="flex items-start justify-between gap-4 border-b border-line px-4 py-3">
          <div>
            <h2 className="text-sm font-semibold">{title}</h2>
            {subtitle && <p className="mt-0.5 text-xs text-gray-500">{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  )
}

export function PageHeader({ title, subtitle, children }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-gray-500">{subtitle}</p>}
      </div>
      <div className="flex flex-wrap items-center gap-2">{children}</div>
    </div>
  )
}

export function KpiCard({ label, value, unit, hint, tone = 'neutral' }) {
  const toneClass = { neutral: 'text-gray-900', good: 'text-ok', warn: 'text-warn', bad: 'text-crit' }[tone]
  return (
    <div className="rounded-lg border border-line bg-white p-4">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">{label}</div>
      <div className={`num mt-1 text-2xl font-bold ${toneClass}`}>
        {value}
        {unit && <span className="ml-1 text-sm font-medium text-gray-500">{unit}</span>}
      </div>
      {hint && <div className="mt-1 text-xs text-gray-500">{hint}</div>}
    </div>
  )
}

const STATUS_STYLES = {
  operational: 'bg-ok/10 text-ok border-ok/30',
  warning: 'bg-warn/10 text-warn border-warn/30',
  critical: 'bg-crit/10 text-crit border-crit/30',
  offline: 'bg-off/10 text-gray-600 border-off/30',
  info: 'bg-blue-50 text-blue-700 border-blue-200',
  open: 'bg-blue-50 text-blue-700 border-blue-200',
  overdue: 'bg-crit/10 text-crit border-crit/30',
  completed: 'bg-ok/10 text-ok border-ok/30',
  active: 'bg-crit/10 text-crit border-crit/30',
  cleared: 'bg-off/10 text-gray-600 border-off/30',
}

export function StatusBadge({ status }) {
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold uppercase ${STATUS_STYLES[status] ?? STATUS_STYLES.offline}`}>
      {status}
    </span>
  )
}

export const STATUS_COLORS = { operational: '#00a86b', warning: '#c98a00', critical: '#e5384f', offline: '#8c8c8c' }

export function Loading({ text = 'Loading…' }) {
  return <div className="py-10 text-center text-sm text-gray-500">{text}</div>
}

export function ErrorBox({ error, onRetry }) {
  return (
    <div className="rounded-lg border border-crit/30 bg-crit/5 p-4 text-sm text-crit">
      <div className="font-semibold">Could not load data from the API</div>
      <div className="mt-1 text-xs break-all">{error}</div>
      {onRetry && (
        <button onClick={onRetry} className="mt-3 rounded border border-crit/40 bg-white px-3 py-1 text-xs font-medium hover:bg-crit/10">
          Retry
        </button>
      )}
    </div>
  )
}

export function Segmented({ options, value, onChange }) {
  return (
    <div className="inline-flex rounded-md border border-line bg-white p-0.5">
      {options.map((opt) => (
        <button
          key={opt.value}
          onClick={() => onChange(opt.value)}
          className={`rounded px-3 py-1 text-xs font-medium ${value === opt.value ? 'bg-brand text-white' : 'text-gray-600 hover:bg-gray-100'}`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}

export function Table({ columns, rows, empty = 'No data', onRowClick }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line bg-canvas text-left text-[11px] uppercase tracking-wide text-gray-500">
            {columns.map((c) => (
              <th key={c.key} className={`whitespace-nowrap px-3 py-2 font-semibold ${c.align === 'right' ? 'text-right' : ''}`}>
                {c.label}
              </th>
            ))}
            {onRowClick && <th className="w-6" />}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr>
              <td colSpan={columns.length + (onRowClick ? 1 : 0)} className="px-3 py-6 text-center text-gray-500">
                {empty}
              </td>
            </tr>
          )}
          {rows.map((row, i) => (
            <tr
              key={row.id ?? i}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              className={`group border-b border-line last:border-0 ${onRowClick ? 'cursor-pointer hover:bg-brand-light/60' : ''}`}
            >
              {columns.map((c) => (
                <td key={c.key} className={`px-3 py-2 align-middle ${c.wrap ? '' : 'whitespace-nowrap'} ${c.align === 'right' ? 'num text-right' : ''}`}>
                  {c.render ? c.render(row) : row[c.key]}
                </td>
              ))}
              {/* Chevron: tells the row opens something */}
              {onRowClick && <td aria-hidden className="w-6 pr-3 text-right text-gray-400 group-hover:text-brand">›</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
