// vite.config.ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
import { fileURLToPath } from "url";

// Recreate __dirname for ESM
const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Allow override via env (e.g. VITE_BACKEND=http://127.0.0.1:8010)
const BACKEND =
  process.env.VITE_BACKEND ??
  `http://127.0.0.1:${process.env.VITE_BACKEND_PORT ?? "8000"}`;

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      // Forward API calls to FastAPI backend (dev only)
      "/api": {
        target: BACKEND,
        changeOrigin: true,
        secure: false,
      },
      // Prometheus metrics (optional)
      "/metrics": {
        target: BACKEND,
        changeOrigin: true,
        secure: false,
      },
      // Explicitly proxy the SSE stream (EventSource over HTTP)
      "/api/market/stream": {
        target: BACKEND,
        changeOrigin: true,
        secure: false,
      },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
