import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    // Forward API calls to the core (FastAPI on :8001) to avoid CORS in dev
    proxy: {
      '/api': 'http://localhost:8001',
    },
  },
})
