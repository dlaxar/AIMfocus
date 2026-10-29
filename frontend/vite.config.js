import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    // Forward API calls to the backend (e.g. FastAPI on :8000) to avoid CORS in dev
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
