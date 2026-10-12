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

  // Throws when the credentials are wrong: the login form shows the message.
  const signIn = async (email, password) => {
    await api.login(email, password)
    await refresh()
  }
  const signOut = async () => {
    await api.logout()
    setUser(null)
  }

  return <AuthContext.Provider value={{ user, loading, signIn, signOut }}>{children}</AuthContext.Provider>
}

export const useAuth = () => useContext(AuthContext)
