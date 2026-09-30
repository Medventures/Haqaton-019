import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// При разработке запросы к /api уходят на локальный сервер Хатшы.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
})
