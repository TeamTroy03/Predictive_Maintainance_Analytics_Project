import { useAuth } from '../auth.jsx'

export default function Login() {
  const { signIn } = useAuth()
  return (
    <div className="flex h-screen items-center justify-center">
      <div className="w-80 rounded-lg border border-line bg-white p-8 text-center shadow-sm">
        <img src="/logo.svg" alt="" className="mx-auto h-12 w-12" />
        <h1 className="mt-4 text-lg font-semibold">
          AssetPulse <span className="text-brand">AI</span>
        </h1>
        <p className="mt-1 text-sm text-gray-600">Sign in to view fleet health, sensors and maintenance data.</p>
        <button
          onClick={signIn}
          className="mt-6 w-full rounded-md bg-brand px-3 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          Sign in
        </button>
      </div>
    </div>
  )
}