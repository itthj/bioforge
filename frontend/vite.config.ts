/// <reference types="vitest/config" />
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite dev server proxies /agent, /projects, /traces, /health to the backend so the
// frontend can use relative URLs (avoids CORS in dev). Production builds serve the
// static SPA from whatever origin you deploy to, and the backend CORS middleware
// already allows that origin (configured in main.py).
export default defineConfig({
  plugins: [react()],
  // Multi-page: the main app + a standalone /showcase.html that renders the real
  // components with mock data (a no-backend visual demo, deployable to a static host).
  build: {
    rollupOptions: {
      input: {
        main: fileURLToPath(new URL("./index.html", import.meta.url)),
        showcase: fileURLToPath(new URL("./showcase.html", import.meta.url)),
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/agent": { target: "http://localhost:8000", changeOrigin: true },
      "/auth": { target: "http://localhost:8000", changeOrigin: true },
      "/projects": { target: "http://localhost:8000", changeOrigin: true },
      "/benchmarks": { target: "http://localhost:8000", changeOrigin: true },
      "/traces": { target: "http://localhost:8000", changeOrigin: true },
      "/config": { target: "http://localhost:8000", changeOrigin: true },
      "/usage": { target: "http://localhost:8000", changeOrigin: true },
      "/pipelines": { target: "http://localhost:8000", changeOrigin: true },
      "/health": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  test: {
    // happy-dom is faster than jsdom and covers everything React Testing Library
    // needs (DOM, MutationObserver, requestAnimationFrame). If we ever need
    // browser-specific behavior we don't get here, switch to jsdom or playwright.
    environment: "happy-dom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    // CSS is build-time (Tailwind); no need to process @tailwind directives during
    // tests. Skip the postcss pipeline for speed.
    css: false,
  },
});
