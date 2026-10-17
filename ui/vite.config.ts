import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { '/api': { target: process.env.API_BASE ?? 'http://localhost:8120', changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, '') } },
  },
});
