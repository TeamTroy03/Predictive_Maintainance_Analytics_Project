import { useState } from 'react'
import { useAuth } from '../auth.jsx'
import { isMockMode } from '../api/client.js'

// The error text starts with the HTTP status (see client.js).
function message(error) {
  if (error.message.startsWith('401')) return 'Invalid email or password.'
  if (error.message.startsWith('429')) return 'Too many failed attempts. Try again in a few minutes.'
  if (error.message.startsWith('503')) return 'Sign-in is not available right now.'
  return 'Could not sign in. Please try again.'
}

export default function Login() {
  const { signIn } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (event) => {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      await signIn(email, password)
    } catch (e) {
      setError(message(e))
      setBusy(false)
    }
  }

  return (
    <div className="flex h-screen items-center justify-center bg-ink">
      <form onSubmit={submit} className="w-80 rounded-lg border-t-4 border-brand bg-panel p-8">
        <img src="/logo.svg" alt="" className="mx-auto h-12 w-12" />
        <h1 className="mt-4 text-center font-display text-3xl font-semibold">
          AssetPulse <span className="text-brand">AI</span>
        </h1>
        <p className="mt-1 text-center text-sm text-gray-600">Sign in to view fleet health and maintenance data.</p>

        <label className="mt-6 block text-xs font-medium text-gray-600" htmlFor="email">Email</label>
        <input
          id="email"
          type="email"
          autoComplete="username"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm"
        />
        <label className="mt-3 block text-xs font-medium text-gray-600" htmlFor="password">Password</label>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm"
        />

        {error && <p role="alert" className="mt-3 text-sm text-crit">{error}</p>}
        {isMockMode && <p className="mt-3 text-xs text-gray-500">Demo mode: any email and password work.</p>}

        <button
          type="submit"
          disabled={busy}
          className="mt-6 w-full rounded-md bg-brand px-3 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
        >
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}
