import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Local Smart Move preview: the page talks to the read-only Smart Move server on 8791
// (docs/smart_move/app/README.md). Same-origin /api and /auth, like the normal dev config.
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: Number(process.env.SM_VITE_PORT || 5191),
    strictPort: true,
    proxy: {
      '/api': { target: `http://127.0.0.1:${process.env.SM_API_PORT || 8791}`, changeOrigin: true },
      '/auth': { target: `http://127.0.0.1:${process.env.SM_API_PORT || 8791}`, changeOrigin: true },
    },
  },
})
