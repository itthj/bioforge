# BioForge frontend — Vite + React + Tailwind, served static via nginx.
#
# Multi-stage:
#   1. `builder` runs `npm ci && npm run build` against node:22-alpine. The build
#      output lands in /app/dist; everything else (node_modules, sources) is
#      discarded by leaving the stage behind.
#   2. `runtime` is nginx:alpine. The compiled dist/ is served as static files;
#      /agent, /projects, /traces, /health, /benchmarks are proxied to the backend
#      container (named `backend` on the compose network) — see nginx.conf next to
#      this Dockerfile.

# --- Stage 1: build ----------------------------------------------------------
FROM node:22-alpine AS builder

WORKDIR /app

# package.json + lockfile FIRST so the deps layer is cacheable independent of
# source-file edits.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . .
RUN npm run build

# --- Stage 2: runtime --------------------------------------------------------
FROM nginx:alpine AS runtime

# Drop nginx's stock site config; supply our own with SPA fallback + backend proxy.
RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --quiet --spider http://localhost/ || exit 1
