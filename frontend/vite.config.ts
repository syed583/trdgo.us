import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    // Proxy the API so the app talks to the backend same-origin. This lets the
    // dev page (and the in-app browser pane, which can't reach a host-only
    // 127.0.0.1:8000 directly) reach the backend and send auth cookies.
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
  preview: {
    host: '127.0.0.1',
    port: 4173,
  },
})
