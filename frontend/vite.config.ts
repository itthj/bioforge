import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite dev server proxies /agent, /projects, /traces, /health to the backend so the
// frontend can use relative URLs (avoids CORS in dev). Production builds serve the
// static SPA from whatever origin you deploy to, and the backend CORS middleware
// already allows that origin (configured in main.py).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/agent": { target: "http://localhost:8000", changeOrigin: true },
      "/projects": { target: "http://localhost:8000", changeOrigin: true },
      "/traces": { target: "http://localhost:8000", changeOrigin: true },
      "/health": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
