import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The built SPA is served by the FastAPI process from ../app/static, because
// only one service may bind to DATABRICKS_APP_PORT.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../app/static',
    emptyOutDir: true,
    sourcemap: false,
    // Every asset must stay well under the platform's 10 MB per-file limit.
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        manualChunks: {
          // Split the vendor bundle so a UI change does not invalidate React
          // in the browser cache on every deploy.
          react: ['react', 'react-dom', 'react-router-dom'],
          query: ['@tanstack/react-query'],
        },
      },
    },
  },
  server: {
    port: 5173,
    // Local development: the API runs separately on 8000.
    proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true } },
  },
})
