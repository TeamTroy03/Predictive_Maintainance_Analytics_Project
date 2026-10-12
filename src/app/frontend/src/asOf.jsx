import { createContext, useContext } from 'react'
import { api } from './api/client.js'
import { useApi } from './hooks/useApi.js'

// "Data through" = the Gold as_of timestamp. The dataset is historical, so every "X d ago" counts from here.
// One place fetches it (the server caches /fleet/summary for a minute); pages read it with useAsOf().
const AsOfContext = createContext({ asOf: null, failed: false })

export function AsOfProvider({ children }) {
  const summary = useApi(() => api.getFleetSummary())
  const value = { asOf: summary.data?.as_of ?? null, failed: Boolean(summary.error) }
  return <AsOfContext.Provider value={value}>{children}</AsOfContext.Provider>
}

export const useAsOf = () => useContext(AsOfContext)