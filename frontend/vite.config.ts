import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

/**
 * Фронт живёт отдельной сборкой и общается с бэкендом только по HTTP.
 * В разработке запросы на `/api` проксируются на сервер, чтобы адрес
 * в коде был один и тот же и там, и в проде: иначе переменная с адресом
 * однажды уезжает в сборку незаполненной, и фронт молча стучится сам в себя.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_PROXY_TARGET || 'http://127.0.0.1:8100',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['src/test/setup.ts'],
  },
});
