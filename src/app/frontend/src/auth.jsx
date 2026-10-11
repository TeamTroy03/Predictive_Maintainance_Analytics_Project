import { createContext, useContext, useEffect, useState } from 'react'
import { api } from './api/client.js'

// FastAPI owns authentication. We only ask /api/auth/me who is signed in (401 -> nobody).
const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)

  const refresh = () =>
    api
      .getMe()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false))

  useEffect(() => {
    refresh()
  }, [])

  const signIn = async () => {
    await api.login() // real mode: browser leaves for FastAPI's login page and comes back signed in
    refresh()
  }
  const signOut = async () => {
    await api.logout()
    setUser(null)
  }

  return <AuthContext.Provider value={{ user, loading, signIn, signOut }}>{children}</AuthContext.Provider>
}

export const useAuth = () => useContext(AuthContext)