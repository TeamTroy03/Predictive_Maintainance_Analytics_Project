// Small reusable building blocks used by every page

export function Card({ title, subtitle, action, children, className = '' }) {
  return (
    <section className={`rounded-lg border border-line bg-panel ${className}`}>
      {(title || action) && (
        <header className="flex items-start justify-between gap-4 border-b border-line bg-gray-50 px-4 py-2.5">
          <div>
            <h2 className="font-display text-lg font-semibold leading-tight">{title}</h2>
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
        <h1 className="font-display text-3xl font-semibold leading-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-gray-500">{subtitle}</p>}
      </div>
      <div className="flex flex-wrap items-center gap-2">{children}</div>
    </div>
  )
}

// The left edge is the status lamp: graphite when the value carries no verdict
const LAMP = { neutral: 'bg-gray-300', good: 'bg-ok', warn: 'bg-warn', bad: 'bg-crit' }

export function KpiCard({ label, value, unit, hint, tone = 'neutral' }) {
  const toneClass = { neutral: 'text-ink', good: 'text-ok', warn: 'text-warn', bad: 'text-crit' }[tone]
  return (
    <div className="flex overflow-hidden rounded-lg border border-line bg-panel">
      <div aria-hidden className={`w-1.5 shrink-0 ${LAMP[tone]}`} />
      <div className="min-w-0 flex-1 px-4 py-3">
        <div className="text-xs font-medium text-gray-600">{label}</div>
        <div className={`num mt-0.5 font-display text-4xl font-semibold leading-none ${toneClass}`}>
          {value}
          {unit && <span className="ml-1 font-sans text-sm font-medium text-gray-500">{unit}</span>}
        </div>
        {hint && <div className="mt-1.5 text-xs text-gray-500">{hint}</div>}
      </div>
    </div>
  )
}

const LAMP_COLOR = {
  operational: 'bg-ok', completed: 'bg-ok',
  warning: 'bg-warn',
  critical: 'bg-crit', overdue: 'bg-crit', active: 'bg-crit',
  info: 'bg-brand', open: 'bg-brand',
  offline: 'bg-off', cleared: 'bg-off',
}

export function StatusBadge({ status }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-medium capitalize text-gray-800">
      <span aria-hidden className={`h-2 w-2 rounded-full ${LAMP_COLOR[status] ?? LAMP_COLOR.offline}`} />
      {status}
    </span>
  )
}

export const STATUS_COLORS = { operational: '#00a86b', warning: '#d99a00', critical: '#e5384f', offline: '#8c8c8c' }

// The logo's heartbeat line with a pulse travelling along it (animation in index.css)
export function PulseLine({ className = 'h-12 w-12', color = '#7e00c9' }) {
  const d = 'M8 21.5H13.5L16.5 13L21.5 28L25 18.5L27.5 21.5H32'
  const stroke = { stroke: color, strokeWidth: 2.75, strokeLinecap: 'round', strokeLinejoin: 'round' }
  return (
    <svg viewBox="0 0 40 40" className={className} fill="none" aria-hidden="true">
      <path d={d} {...stroke} strokeOpacity="0.2" />
      <path d={d} {...stroke} className="pulse-trace" pathLength="1" />
    </svg>
  )
}

export function Loading({ text = 'Loading…' }) {
  return (
    <div role="status" className="flex flex-col items-center gap-1 py-12 text-sm text-gray-600">
      <PulseLine />
      {text}
    </div>
  )
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
    <div className="inline-flex rounded-md border border-line bg-panel p-0.5">
      {options.map((opt) => (
        <button
          key={opt.value}
          onClick={() => onChange(opt.value)}
          className={`rounded-sm px-3 py-1 text-xs font-medium ${value === opt.value ? 'bg-ink text-white' : 'text-gray-600 hover:bg-gray-100'}`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}

// Page numbers with the first, the last and the neighbours of the current page: 1 … 4 5 [6] 7 8 … 20
function pageNumbers(page, pages) {
  const shown = [...new Set([1, page - 2, page - 1, page, page + 1, page + 2, pages])].filter((p) => p >= 1 && p <= pages).sort((a, b) => a - b)
  return shown.flatMap((p, i) => (i > 0 && p - shown[i - 1] > 1 ? ['…', p] : [p]))
}

export function Pagination({ page, pageSize, total, onChange }) {
  const pages = Math.max(1, Math.ceil(total / pageSize))
  if (pages === 1) return null
  const from = (page - 1) * pageSize + 1
  const to = Math.min(page * pageSize, total)
  const btn = 'min-w-8 rounded border px-2 py-1 text-xs font-medium'
  return (
    <nav aria-label="Pagination" className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-line pt-3">
      <p className="num text-xs text-gray-600">{from.toLocaleString('en-GB')}–{to.toLocaleString('en-GB')} of {total.toLocaleString('en-GB')}</p>
      <div className="flex items-center gap-1">
        <button onClick={() => onChange(page - 1)} disabled={page === 1} className={`${btn} border-line hover:bg-gray-100 disabled:opacity-40`}>Previous</button>
        {pageNumbers(page, pages).map((p, i) =>
          p === '…' ? (
            <span key={`gap${i}`} aria-hidden className="px-1 text-xs text-gray-500">…</span>
          ) : (
            <button
              key={p}
              onClick={() => onChange(p)}
              aria-current={p === page ? 'page' : undefined}
              aria-label={`Page ${p}`}
              className={`${btn} num ${p === page ? 'border-ink bg-ink text-white' : 'border-line hover:bg-gray-100'}`}
            >
              {p}
            </button>
          ),
        )}
        <button onClick={() => onChange(page + 1)} disabled={page === pages} className={`${btn} border-line hover:bg-gray-100 disabled:opacity-40`}>Next</button>
      </div>
    </nav>
  )
}

export function Table({ columns, rows, empty = 'No data', onRowClick }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line bg-gray-50 text-left text-xs text-gray-600">
            {columns.map((c) => (
              <th key={c.key} scope="col" className={`whitespace-nowrap px-3 py-2 font-display text-sm font-semibold ${c.align === 'right' ? 'text-right' : ''}`}>
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
