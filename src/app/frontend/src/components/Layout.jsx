import { NavLink, Outlet } from 'react-router-dom'
import { AgentProvider, useAgent } from './AgentPanel.jsx'
import AgentPanel from './AgentPanel.jsx'
import AskAiButton from './AskAiButton.jsx'
import { isMockMode } from '../api/client.js'
import { useAuth } from '../auth.jsx'
import { AsOfProvider, useAsOf } from '../asOf.jsx'
import { fmtDateTimeUtc, timeAgo } from '../format.js'

const NAV = [
  { to: '/', label: 'Fleet Health' },
  { to: '/alarms', label: 'Critical Exceptions' },
  { to: '/equipment', label: 'Equipment' },
  { to: '/sensors', label: 'Sensor Trends' },
  { to: '/maintenance', label: 'Maintenance' },
]

// How fresh the data is (not whether the API answers); every "ago" on the pages counts from here
function DataFreshness() {
  const { asOf, failed } = useAsOf()
  return (
    <div className="hidden text-right leading-tight lg:block" title={asOf ? `Latest event ${timeAgo(asOf)}` : undefined}>
      {isMockMode && <span className="mr-3 rounded-sm bg-warn/25 px-1.5 py-0.5 text-xs font-medium text-amber-200">Demo data</span>}
      <span className="text-xs text-gray-400">Data through </span>
      <span className="num text-xs font-medium text-white">{asOf ? fmtDateTimeUtc(asOf) : failed ? 'unknown' : 'loading…'}</span>
    </div>
  )
}

// All windows in view at once; the current one gets the lamp underline. Scrolls sideways on narrow screens.
function NavTabs() {
  return (
    <nav aria-label="Windows" className="flex h-full min-w-0 items-stretch overflow-x-auto">
      {NAV.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.to === '/'}
          className={({ isActive }) =>
            `flex shrink-0 items-center border-b-2 px-3 text-sm font-medium ${
              isActive ? 'border-brand-glow text-white' : 'border-transparent text-gray-300 hover:bg-white/10 hover:text-white'
            }`
          }
        >
          {item.label}
        </NavLink>
      ))}
    </nav>
  )
}

function ProfileMenu() {
  const { user, signOut } = useAuth()
  const initials = user.name.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase()
  return (
    <details className="relative">
      <summary
        className="flex h-8 w-8 cursor-pointer list-none items-center justify-center overflow-hidden rounded-full bg-brand text-xs font-semibold text-white"
        aria-label="Profile menu"
      >
        <img src="/avatar-prof.png" alt={initials} className="h-full w-full object-cover" />
      </summary>
      <div className="absolute right-0 z-10 mt-2 w-60 rounded-md border border-line bg-panel p-3 text-sm text-ink shadow-lg">
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
    <header className="flex h-14 shrink-0 items-stretch justify-between gap-4 border-b border-white/10 bg-ink px-4 text-white md:px-6">
      <div className="flex min-w-0 items-stretch gap-6">
        <div className="flex shrink-0 items-center gap-3">
          <img src="/logo.svg" alt="" className="h-7 w-7 object-contain" />
          <span className="hidden font-display text-xl font-semibold sm:inline">AssetPulse <span className="text-brand-glow">AI</span></span>
        </div>
        <NavTabs />
      </div>
      <div className="flex shrink-0 items-center gap-4">
        <DataFreshness />
        <AskAiButton onClick={() => setOpen(!open)} open={open} />
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