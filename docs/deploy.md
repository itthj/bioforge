# Deploying BioForge to a host

BioForge is a containerized stack (`docker-compose.yml`): a FastAPI backend + agent loop and
an nginx-served React frontend, with **optional** Celery workers and MinIO object storage behind
compose profiles. It runs on any Docker host. This doc is the honest operator's guide — what to
set, how to persist state, and **how not to expose it unsafely**.

> ⚠️ **Read "Exposing it safely" before any public deploy.** BioForge has **no authentication layer
> yet** (auth/multi-tenancy is deferred — see the v4 blueprint Phase 6). The `/agent` endpoint spends
> your Anthropic API budget on every call, so an unauthenticated public endpoint is both a security
> and a cost exposure.

---

## 1. One-command local / single-host run

```bash
# A .env beside docker-compose.yml must define ANTHROPIC_API_KEY.
cp .env.example .env          # then edit ANTHROPIC_API_KEY
docker compose up --build
```

- Frontend: `http://<host>:5173` (nginx; serves the SPA + proxies the API to the backend).
- Backend: internal only (`expose: 8000` on the compose network — not published to the host).
- State: SQLite in the named volume `bioforge-data`. `docker compose down` keeps it; `down -v` wipes it.

The **Accuracy** tab works with no API key (it reads `GET /benchmarks/accuracy`); the agent chat
requires `ANTHROPIC_API_KEY`.

---

## 2. Configuration (environment)

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | **yes** (for the agent) | — | Claude API access. The Accuracy Report does not need it. |
| `BIOFORGE_DB_URL` | no | `sqlite+aiosqlite:///./bioforge.db` | Database URL (see §4). |
| `BIOFORGE_DEFAULT_MODEL` | no | `claude-sonnet-4-6` | Tool-routing / agent model. |
| `BIOFORGE_DEFAULT_PROJECT_ID` | no | `default-project` | Bootstrap project. |
| `BIOFORGE_ENTREZ_EMAIL` | recommended | empty | NCBI Entrez courtesy email (BLAST / ClinVar / dbSNP lookups). |
| `BIOFORGE_TASK_QUEUE` | no | `inline` | `inline` runs tools in-process; `celery` offloads expensive tools to the worker. |
| `BIOFORGE_REDIS_URL` | if celery | `redis://redis:6379/0` | Celery broker. |
| `BIOFORGE_STORAGE_BACKEND` | no | `local` | `local` (in-container) or `minio` (S3). |
| `BIOFORGE_MINIO_*` | if minio | see compose | Endpoint / keys / bucket. |
| `BIOFORGE_OTEL_ENABLED` | no | `false` | OpenTelemetry trace export. |

The optional legacy model images (DeepCRISPR, FORECasT, Lindel, Azimuth, DeepVariant) are **opt-in**
via per-model `BIOFORGE_*_ENABLED` + `*_DOCKER_IMAGE` env vars and are **not needed** for the core
app — they power the benchmark-regeneration / heavy-scoring paths only.

---

## 3. Services & profiles

| Service | Started by default? | Bring up with | Role |
|---|---|---|---|
| `backend` | yes | (default) | FastAPI + agent loop |
| `frontend` | yes | (default) | nginx SPA + API proxy |
| `redis` + `worker` | no | `docker compose --profile workers up` | Celery offload for expensive tools (also set `BIOFORGE_TASK_QUEUE=celery`) |
| `minio` | no | `docker compose --profile storage up` | S3-compatible store for large outputs (also set `BIOFORGE_STORAGE_BACKEND=minio`) |

All external images are **digest-pinned** (`@sha256:`), never `:latest` — reproducibility starts at
the image layer.

---

## 4. Persistence: SQLite vs Postgres

- **SQLite (default).** Zero-config, single-node, in the `bioforge-data` volume. Fine for a
  single-user prototype / demo host. It does **not** support multiple backend replicas writing
  concurrently.
- **Postgres (for durability / multiple replicas).** Set
  `BIOFORGE_DB_URL=postgresql+asyncpg://user:pass@host:5432/bioforge` and run the Alembic migrations
  (`alembic.ini` at `backend/`; `alembic upgrade head`). The migration path is covered by
  `backend/tests/test_migrations.py`. Provide Postgres yourself (a managed instance or an added
  compose service) — it is intentionally not bundled in the default compose.

---

## 5. Exposing it safely (do not skip)

BioForge has **no built-in authentication, authorization, or rate-limiting** yet. Before any
network-reachable deploy:

1. **Put it behind an authenticating reverse proxy** (e.g. an OAuth2 proxy, your gateway's auth, or
   basic-auth) **or** restrict it to a private network / VPN. The agent endpoint costs real money per
   call — an open endpoint is an open invoice.
2. **Terminate TLS** at that proxy (the bundled nginx serves plain HTTP on port 80 → host 5173).
3. **CORS** is configured via middleware in the backend — review and tighten the allowed origins for
   your deployment rather than running permissive defaults.
4. **Keep secrets out of the image** — pass `ANTHROPIC_API_KEY` (and any DB/MinIO creds) via the
   host's secret manager / env, never baked into a built image or committed `.env`.

---

## 6. Health & observability

- **Health:** `GET /health` (the compose healthchecks already poll it). Use it for your
  orchestrator's liveness/readiness probes.
- **Tracing:** set `BIOFORGE_OTEL_ENABLED=true` and point `BIOFORGE_OTEL_ENDPOINT` at your collector
  for per-run OpenTelemetry traces.
- Every agent run also persists a structured trace + lineage manifest (DB / RO-Crate export) — the
  "show me what you did and why" surface, independent of OTEL.

---

## 7. What is NOT production-hardened (honest scope)

This is a **research prototype**, not a hardened SaaS. Known gaps a production deploy must own:

- **No auth / multi-tenancy / billing** (deferred — blueprint Phase 6). See §5.
- **SQLite by default** — switch to Postgres (§4) for anything beyond a single-node demo.
- **The gated benchmarks** (CRISPR scoring, GIAB, edit-outcome) need Docker-in-Docker or host Docker
  access + opt-in model images + datasets; the core app does not.
- **No horizontal autoscaling story** beyond the single Celery worker profile.

For what *is* solid — the grounding stack, the four self-measured benchmarks in the Accuracy Report,
provenance/RO-Crate, digest-pinned reproducibility — see `docs/DEMO.md` and `docs/handoff.md`.
