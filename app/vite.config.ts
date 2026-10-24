import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Relative base so the same build works on any host path (GitHub Pages, Android WebView).
export default defineConfig({
  base: './',
  plugins: [react()],
})
