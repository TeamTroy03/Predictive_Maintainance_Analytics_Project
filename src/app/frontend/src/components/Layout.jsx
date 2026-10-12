import { NavLink, Outlet } from 'react-router-dom'
import { AgentProvider, useAgent } from './AgentPanel.jsx'
import AgentPanel from './AgentPanel.jsx'
import { isMockMode } from '../api/client.js'
import { useAuth } from '../auth.jsx'
import { AsOfProvider, useAsOf } from '../asOf.jsx'
import { fmtDateTimeUtc, timeAgo } from '../format.js'

const NAV = [
  { to: '/', label: 'Fleet Health', icon: '▦' },
  { to: '/alarms', label: 'Critical Exceptions', icon: '⚠' },
  { to: '/equipment', label: 'Equipment', icon: '⚙' },
  { to: '/sensors', label: 'Sensor Trends', icon: '∿' },
  { to: '/maintenance', label: 'Maintenance', icon: '⚒' },
]

// What the sidebar footer tells: how fresh the data is, not that the API answers
function DataFreshness() {
  const { asOf, failed } = useAsOf()
  return (
    <div className="border-t border-line p-3 text-xs text-gray-500">
      {isMockMode && <div className="mb-2"><span className="rounded bg-warn/10 px-2 py-1 font-medium text-warn">Demo data (mock mode)</span></div>}
      <div className="font-semibold uppercase tracking-wide text-[11px]">Data through</div>
      <div className="mt-0.5 text-sm font-medium text-gray-800">
        {asOf ? fmtDateTimeUtc(asOf) : failed ? 'Unknown' : 'Loading…'}
      </div>
      {asOf && <div className="mt-0.5">Latest event in Gold · {timeAgo(asOf)}</div>}
    </div>
  )
}

function Sidebar() {
  return (
    <aside className="flex w-56 shrink-0 flex-col border-r border-line bg-white">
      <div className="flex h-14 items-center gap-2 border-b border-line px-4">
        <img src="/logo.svg" alt="" className="h-7 w-7 object-contain" />
        <span className="font-semibold">AssetPulse <span className="text-brand">AI</span></span>
      </div>
      <nav className="flex-1 space-y-0.5 p-2">
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            className={({ isActive }) =>
              `flex items-center gap-3 rounded-md px-3 py-2 text-sm ${
                isActive ? 'bg-brand-light font-semibold text-brand' : 'text-gray-700 hover:bg-gray-100'
              }`
            }
          >
            <span className="w-4 text-center">{item.icon}</span>
            {item.label}
          </NavLink>
        ))}
      </nav>
      <DataFreshness />
    </aside>
  )
}

function ProfileMenu() {
  const { user, signOut } = useAuth()
  const initials = user.name.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase()
  return (
    <details className="relative">
      <summary
        className="flex h-8 w-8 cursor-pointer list-none items-center justify-center rounded-full bg-brand text-xs font-semibold text-white"
        aria-label="Profile menu"
      >
        {initials}
      </summary>
      <div className="absolute right-0 z-10 mt-2 w-60 rounded-md border border-line bg-white p-3 text-sm shadow-lg">
        <div className="font-medium">{user.name}</div>
        <div className="truncate text-xs text-gray-500">{user.email}</div>
        <div className="mt-2 inline-block rounded bg-brand-light px-2 py-0.5 text-xs font-medium text-brand">{user.role}</div>
        <button onClick={signOut} className="mt-3 w-full rounded-md border border-line px-3 py-1.5 text-left hover:bg-gray-100">
          Sign out
        </button>
      </div>
    </details>
  )
}

function TopBar() {
  const { open, setOpen } = useAgent()
  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-line bg-white px-6">
      <div className="text-sm text-gray-600">
        <span className="font-medium text-gray-900">Plant 4</span> · Predictive Maintenance Analytics
      </div>
      <div className="flex items-center gap-3">
        <button
          onClick={() => setOpen(!open)}
          className="rounded-md border border-brand/30 bg-brand-light px-3 py-1.5 text-sm font-medium text-brand hover:bg-brand hover:text-white"
        >
          ✦ AI Assistant
        </button>
        <ProfileMenu />
      </div>
    </header>
  )
}

export default function Layout() {
  return (
    <AsOfProvider>
      <AgentProvider>
        <div className="flex h-screen overflow-hidden">
          <Sidebar />
          <div className="flex min-w-0 flex-1 flex-col">
            <TopBar />
            <div className="flex min-h-0 flex-1">
              {/* Main page content. The AI panel sits next to it, so charts stay visible. */}
              <main className="min-w-0 flex-1 overflow-y-auto p-6">
                <Outlet />
              </main>
              <AgentPanel />
            </div>
          </div>
        </div>
      </AgentProvider>
    </AsOfProvider>
  )
}
