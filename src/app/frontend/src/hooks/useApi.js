import { useEffect, useState } from 'react'

// Small helper: runs an API call and gives back { data, loading, error, reload }.
// Re-runs whenever something in `deps` changes (e.g. the selected asset or time range).
export function useApi(apiCall, deps = []) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [reloadCount, setReloadCount] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    apiCall()
      .then((result) => !cancelled && setData(result))
      .catch((err) => !cancelled && setError(err.message))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, reloadCount])

  return { data, loading, error, reload: () => setReloadCount((n) => n + 1) }
}
