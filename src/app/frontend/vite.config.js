import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// During development every request to /api/... is forwarded to your FastAPI server (the /api prefix is
// removed: FastAPI serves /fleet/summary, not /api/fleet/summary). The browser only ever talks to this
// server, so the session cookie stays same-origin and CORS is not needed.
// Change VITE_API_TARGET in .env if FastAPI runs elsewhere.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [react(), tailwindcss()],
    build: { chunkSizeWarningLimit: 1000 },
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target: env.VITE_API_TARGET || 'http://localhost:8000',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ''),
        },
      },
    },
  }
})
