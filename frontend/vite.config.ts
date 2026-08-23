/// <reference types="vitest" />
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
  test: {
    // jsdom plutôt que le navigateur : ces contrôles portent sur du calcul et
    // sur ce que React met dans le DOM. Ce qu'un vrai navigateur apporte —
    // la mise en page, le défilement réel — relève du parcours de bout en
    // bout, qui est un banc à part.
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    // Le client généré est produit par openapi-typescript : le contrôler
    // reviendrait à contrôler le générateur.
    exclude: ['src/lib/schema.d.ts', 'node_modules/**'],
  },
  server: {
    port: 5173,
    // Local development: the API runs separately on 8000.
    proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true } },
  },
})
