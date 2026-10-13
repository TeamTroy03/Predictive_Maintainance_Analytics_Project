import { Routes, Route, Link } from 'react-router-dom'
import Layout from './components/Layout.jsx'
import FleetHealth from './pages/FleetHealth.jsx'
import SensorTrends from './pages/SensorTrends.jsx'
import Maintenance from './pages/Maintenance.jsx'
import EquipmentList from './pages/EquipmentList.jsx'
import EquipmentDetail from './pages/EquipmentDetail.jsx'
import Alarms from './pages/Alarms.jsx'
import Login from './pages/Login.jsx'
import { useAuth } from './auth.jsx'

function NotFound() {
  return (
    <div className="py-16 text-center">
      <h1 className="font-display text-3xl font-semibold">Page not found</h1>
      <p className="mt-2 text-sm text-gray-600">This address doesn't match any window.</p>
      <Link to="/" className="mt-4 inline-block rounded-md bg-brand px-4 py-1.5 text-sm font-medium text-white hover:opacity-90">
        Go to Fleet Health
      </Link>
    </div>
  )
}

export default function App() {
  const { user, loading } = useAuth()
  // No page (and so no API call or chart) renders until FastAPI says the user is signed in.
  if (loading) return null
  if (!user) return <Login />
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<FleetHealth />} />
        <Route path="/sensors" element={<SensorTrends />} />
        <Route path="/maintenance" element={<Maintenance />} />
        <Route path="/equipment" element={<EquipmentList />} />
        <Route path="/equipment/:assetId" element={<EquipmentDetail />} />
        <Route path="/alarms" element={<Alarms />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  )
}
