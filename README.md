# BioForge

Agentic AI bioinformatics platform — a research prototype built so a result it produces is one a scientist could put in a methods section. Full plan→approval→execute→critique→replan loop with SSE streaming and a React frontend that renders the live trace, over a ~30-tool registry spanning sequence basics, CRISPR design / on- & off-target scoring / NHEJ edit outcomes / integrated edit reports, PCR primer design (primer3), MSA, VCF parsing + variant annotation (ClinVar / dbSNP / gnomAD, VEP consequences), and protein structures (AlphaFold / PDB / InterPro, with per-residue pLDDT). The differentiator is the **7-layer grounding stack** (deterministic numeric grounding, an Opus entity/mechanistic judge, rewrite re-validation, and a validated validator) plus an **Accuracy Report** that publishes the platform's own measured numbers — four real section-13 benchmarks today: CRISPR on-target (DeepCRISPR × Chari, ρ=0.130), off-target discrimination (CFD, ρ=0.313), GIAB variant-calling concordance (DeepVariant, precision 0.98 / recall 1.00 on a build-matched region), and edit-outcome agreement (FORECasT vs measured K562 profiles, median TVD 0.546) — each honestly leakage-labeled, never faked. Projects + persistent, user-editable memory. Run manifests + RO-Crate export for provenance; digest-pinned external images. OpenTelemetry tracing on every run. CI runs ruff + pytest + the frontend build on every push. Local BLAST+ alongside the remote NCBI default. The full stack runs via `docker compose up`.

**New here?** See [`docs/DEMO.md`](docs/DEMO.md) for an end-to-end walkthrough and the "what's real vs honestly-gated" scorecard — the integrity (grounding / benchmarking / calibration / provenance) is the product, and that doc states exactly which numbers are real measurements.

## Quickstart

### Docker (one command)

```powershell
# Set your Anthropic API key in a .env beside docker-compose.yml first.
# Then:
docker compose up --build
```

Open http://localhost:5173. The backend lives in the `bioforge-backend` container on the compose network; the SQLite DB persists in the named volume `bioforge-data`. To wipe state: `docker compose down -v`.

> **Want to see it work without an API key?** Open the **Accuracy** tab — it renders the four real, self-measured benchmarks (served from `GET /benchmarks/accuracy`) and needs no Anthropic key. The agent chat itself requires `ANTHROPIC_API_KEY`.

> **Deploying to a host?** See [`docs/deploy.md`](docs/deploy.md) — configuration, persistence (SQLite vs Postgres + Alembic), the optional worker/storage profiles, and **how to expose it safely** (it has no auth layer yet, and the agent endpoint spends your API budget).

### Running with Celery (durable jobs)

By default a run executes in-process and streams over SSE — fine for single-user local. For **durability** (a run survives an API restart or client disconnect, executes in a worker, and can be reconnected to or cancelled), bring up the `workers` profile, which adds **Redis** (broker) + **Postgres** (the shared run DB the API and worker both read/write) + a **Celery worker**:

```powershell
# In the .env beside docker-compose.yml: your ANTHROPIC_API_KEY, plus the two switches that
# point the API at the shared stack (the worker already defaults to them):
#   BIOFORGE_TASK_QUEUE=celery
#   BIOFORGE_DB_URL=postgresql+asyncpg://bioforge:bioforge@postgres:5432/bioforge
docker compose --profile workers up --build
```

`POST /agent/run` then enqueues the run and returns a `trace_id` immediately (status `queued`); `GET /agent/{trace_id}/stream` streams its progress (a live job, or catching up a finished one); `POST /agent/{trace_id}/cancel` revokes it. The UI uses these automatically in celery mode. Inline (`BIOFORGE_TASK_QUEUE=inline`, the default) stays byte-for-byte the previous behavior. The full enqueue → worker → persist → stream path is covered hermetically (Celery eager) in `test_celery_jobs.py`; a real cross-process round-trip is an opt-in docker test:

```powershell
$env:BIOFORGE_CELERY_E2E = "1"   # plus ANTHROPIC_API_KEY in the environment
.\.venv\Scripts\python.exe -m pytest backend/tests/test_celery_e2e_docker.py -m docker -q
```

### Accounts & your own data (opt-in)

By default BioForge is single-user with no login. Set **`BIOFORGE_AUTH_ENABLED=true`** to turn on **accounts**: `POST /auth/register` then `POST /auth/login` returns a bearer token; send it as `Authorization: Bearer <token>`. Every project — and its runs, memory, and files — then belongs to the user who created it; another user gets a 404. With auth off, everything resolves to a built-in default user, so the single-user experience is byte-for-byte unchanged (passwords are argon2id; tokens are stored only as a SHA-256).

**Bring your own data:** upload files into a project and the agent works on them.

```bash
# Upload a FASTA/VCF/table (≤50 MB; allowed: fasta, vcf, bed, csv, tsv, gb, txt, …)
curl -F "file=@guides.fasta" http://localhost:8000/projects/default-project/files
# List / download / delete
curl http://localhost:8000/projects/default-project/files
```

Then just ask the agent: *"read guides.fasta and design CRISPR guides for the first sequence."* The `read_uploaded_file` tool loads it (parsing FASTA into sequences) and feeds it into the design/scoring tools. (Add `-H "Authorization: Bearer <token>"` to every call when auth is on.)

### Backend

```powershell
# from the repo root
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env
# edit .env and set ANTHROPIC_API_KEY

# regenerate test fixtures (one-time, requires network + BIOFORGE_ENTREZ_EMAIL set)
python backend/tests/fixtures/regenerate.py

# run tests (no API key needed — Anthropic is mocked)
pytest

# start the server
uvicorn bioforge.main:app --app-dir backend/src --reload
```

### Frontend (Vite dev server)

```powershell
cd frontend
npm install            # first run only
npm run dev            # serves on http://localhost:5173 with proxy to backend on :8000
npm test               # vitest run — component tests against happy-dom
npm run typecheck      # tsc --noEmit, strict mode
npm run build          # production bundle in dist/
```

Open http://localhost:5173 — type a goal, watch the agent reason. The dev server proxies `/agent`, `/projects`, `/traces`, `/health`, `/benchmarks` to the backend so the SPA uses relative URLs.

Then in another shell:

```powershell
# trivial — planner short-circuits, executor calls gc_content, no critic
curl.exe -X POST http://localhost:8000/agent/run -H "Content-Type: application/json" -d "{\"goal\":\"GC content of ATGCATGC\"}"

# multi-step — planner emits 2-step plan, executor chains tools, critic evaluates
curl.exe -X POST http://localhost:8000/agent/run -H "Content-Type: application/json" -d "{\"goal\":\"What is the GC content of the reverse complement of ATGCATGC?\"}"

# expensive — BLAST pauses for approval; response carries trace_id + pending_plan
curl.exe -X POST http://localhost:8000/agent/run -H "Content-Type: application/json" -d "{\"goal\":\"BLAST ATGCATGCATGCATGCATGC against nt and tell me the top hit\"}"
# → status=pending_approval; copy the trace_id from the response, then approve:
curl.exe -X POST http://localhost:8000/agent/<TRACE_ID>/approve -H "Content-Type: application/json" -d "{\"approved\":true}"
# or cancel without running BLAST:
curl.exe -X POST http://localhost:8000/agent/<TRACE_ID>/approve -H "Content-Type: application/json" -d "{\"approved\":false}"
```

### Streaming (SSE)

Use the `*/stream` variants to see steps as they happen — essential for BLAST runs:

```powershell
# -N disables curl's output buffering; required to see events as they arrive
curl.exe -N -X POST http://localhost:8000/agent/run/stream -H "Content-Type: application/json" -d "{\"goal\":\"GC content of the reverse complement of ATGCATGC\"}"

# Streaming approve — watch BLAST progress live after approval
curl.exe -N -X POST http://localhost:8000/agent/<TRACE_ID>/approve/stream -H "Content-Type: application/json" -d "{\"approved\":true}"
```

Each event is one `event: <name>\ndata: <json>\n\n` block. Event types:

- `step` — one `AgentStep` (plan / tool_call / critique / final / approval_requested / etc.)
- `done` — final summary `{trace_id, status, response_text, usage, pending_plan?, approval_reasons?}`
- `error` — transport-level error (the agent loop's own errors arrive as `tool_error` step events)
- Comment lines `: keepalive` flush every ~15s to keep proxies from closing the connection

> **Database migrations**: schema changes now ship as Alembic revisions in `backend/alembic/versions/`. `init_db()` runs `alembic upgrade head` at FastAPI startup, so the dev path stays one-command. To work with migrations directly:
>
> ```powershell
> cd backend
> $env:BIOFORGE_DB_URL = "sqlite+aiosqlite:///./bioforge.db"
> ..\.venv\Scripts\alembic.exe revision --autogenerate -m "describe change"  # after editing a model
> ..\.venv\Scripts\alembic.exe upgrade head                                    # apply
> ..\.venv\Scripts\alembic.exe downgrade -1                                    # revert one
> ```
>
> A CI test (`test_migrations.py`) applies the full chain to an empty DB and asserts the resulting schema matches `Base.metadata` — catches model-vs-migration drift before merge.

### CI

`.github/workflows/ci.yml` runs on every push + PR to `main`:
  - **backend** — pip install, `ruff check backend/`, `pytest -v` (online-marker tests excluded by `pyproject.toml`'s `addopts`)
  - **frontend** — `npm ci`, `npm run typecheck`, `npm run build`
  - Jobs run in parallel; concurrency cancels superseded refs to save minutes.

`.github/workflows/nightly.yml` runs weekly (Mondays 06:00 UTC) and on manual dispatch:
  - Regenerates committed fixtures from live NCBI (`regenerate.py`)
  - Runs the `-m online` test suite — catches upstream API drift (renamed accession, schema change, deprecated endpoint) before users do
  - Uploads regenerated fixtures as an artifact on failure so you can diff what NCBI changed

### OpenTelemetry export

Tracing is disabled by default. Enable it when you want spans for agent runs, LLM calls, and tool calls:

```powershell
# Console spans for local debugging
$env:BIOFORGE_OTEL_ENABLED = "true"
$env:BIOFORGE_OTEL_EXPORTER = "console"

# OTLP/HTTP export to a local collector, Jaeger, Honeycomb, Tempo, etc.
$env:BIOFORGE_OTEL_ENABLED = "true"
$env:BIOFORGE_OTEL_EXPORTER = "otlp"
$env:BIOFORGE_OTEL_ENDPOINT = "http://localhost:4318/v1/traces"
$env:BIOFORGE_OTEL_HEADERS = "authorization=Bearer <token>"  # optional key=value,key2=value2
```

`BIOFORGE_OTEL_EXPORTER=none` installs a provider without exporting. That is mainly for tests and local instrumentation checks.

### Projects + memory

Every run is scoped to a `project_id` (default: `"default-project"`, auto-created on startup). Each project has its own memory store the agent can read via `recall_memory` and write via `remember`. The user can inspect and edit memory via the `/projects/{id}/memory` API.

```powershell
# Create a project
curl.exe -X POST http://localhost:8000/projects -H "Content-Type: application/json" -d "{\"id\":\"crispr-2026\",\"name\":\"CRISPR screen 2026\",\"organism\":\"Homo sapiens\",\"reference_genome\":\"GRCh38\"}"

# Run an agent goal scoped to that project — the planner sees the project's organism + memory entries
curl.exe -N -X POST http://localhost:8000/agent/run/stream -H "Content-Type: application/json" -d "{\"goal\":\"GC content of ATGCATGC\",\"project_id\":\"crispr-2026\"}"

# Inspect what the agent has remembered
curl.exe http://localhost:8000/projects/crispr-2026/memory

# Edit / override a memory entry as the user
curl.exe -X PUT http://localhost:8000/projects/crispr-2026/memory/preferred_organism -H "Content-Type: application/json" -d "{\"value\":\"Mus musculus\",\"kind\":\"preference\"}"
```

## Layout

```
backend/src/bioforge/
  api/agent.py            POST /agent/run[/stream], POST /agent/{id}/approve[/stream], GET /traces/{id}
  api/projects.py         POST/GET/PATCH/DELETE /projects + GET/PUT/DELETE /projects/{id}/memory[/{key}]
  api/sse.py              SSE format helpers (format_event, format_keepalive)
  agent/
    planner.py            Plan / make_plan → forced submit_plan tool-use
    critic.py             CriticVerdict / evaluate → forced submit_verdict
    approval.py           requires_approval(plan, registry) → ApprovalRequirement
    memory.py             load_relevant_memory(session, project_id, goal) → planner context
    context.py            ContextVars (project_id, db_session) + AgentContextScope
    loop.py               plan → approval → execute → critique → replan-once → respond
    llm.py                AsyncAnthropic wrapper, cost accounting
    prompts/              system.md, planner.md, critic.md (markdown, not strings)
  tools/                  @register_tool registry
    sequence/gc_content              cheap   — GC% with N-aware denominator
    sequence/reverse_complement      cheap   — Biopython rev-comp
    sequence/translate               cheap   — 6 frames, all NCBI codes, leftover-aware
    sequence/find_orfs               cheap   — 6-frame ORF scan, fwd-strand coords
    sequence/codon_usage             cheap   — codon counts + per-AA fractions
    sequence/design_guides           cheap   — Cas9 / Cas12a guide RNA candidates
    sequence/edit_outcome            cheap   — NHEJ outcome enumeration + frameshift flags
    sequence/blast                   EXPENSIVE — triggers approval gate
    meta/memory_tools.recall_memory  cheap, reads via ContextVar
    meta/memory_tools.remember       cheap, upserts via ContextVar
  db/                     SQLAlchemy async
    Project                          project workspaces
    ProjectMemory                    (project_id, key) UPSERT; ondelete=CASCADE
    Trace                            agent run history with project_id
  observability/          OpenTelemetry tracing — agent.run → agent.plan → tool.call.*

frontend/                 Vite + React 19 + TS strict + Tailwind 3
  src/api/agent.ts        fetch+ReadableStream SSE consumer (async generators)
  src/api/projects.ts     CRUD wrappers: projects + per-project memory
  src/components/
    ChatInput.tsx                  textarea + Ctrl+Enter submit
    TraceView / StepCard.tsx       live step rendering, type-styled badges
    ApprovalCard.tsx               shows on pending_approval; Approve / Cancel
    FinalCard.tsx                  status badge + response text + token / cost
    ProjectSwitcher.tsx            header dropdown + create-project dialog
    MemoryInspector.tsx            list / inline-edit / delete / add memory entries
backend/tests/            152 tests: + edit_outcome (Cas9 NHEJ simulation)
backend/tests/fixtures/   regenerate.py (NCBI Entrez), committed FASTA + meta.json
```

## Agent loop shape

```
goal
  │
  ▼
PLANNER ── forced tool_use(submit_plan) ──► Plan
  │
  ├── refusal-shaped plan (steps=[]) ──► status=refused
  │
  ▼
APPROVAL GATE ── any step.tool has cost_hint=expensive or destructive? ──┐
  │                                                                       │
  │ no                                                                    │ yes
  ▼                                                                       ▼
EXECUTOR (manual tool-use loop) ──► draft                          status=pending_approval
  │                                                                pending_plan persisted
  ▼   (skipped if trivial)                                         /agent/{id}/approve
CRITIC ── forced tool_use(submit_verdict) ──► CriticVerdict        ────────────┐
  │                                                                            │
  ├─ satisfies_goal=true  ──► return, status=completed             approved? ◄─┘
  │                                                                  │
  └─ satisfies_goal=false ──► REPLAN → EXECUTE → CRITIQUE         ┌──┴──┐
                                                  │              yes   no
                                       satisfies? │              │     │
                                       ┌──────────┘              ▼     ▼
                                       ▼              resume_agent   status=cancelled
                              completed_after_replan       │
                                  | OR |                   ▼
                              critique_failed     (continues into executor → critic)
                              (draft + concerns)
```

## Architecture notes

- **Structured outputs via forced tool-use.** Planner and critic each define a single tool (`submit_plan`, `submit_verdict`) whose `input_schema` IS the Pydantic model. `tool_choice={"type":"tool","name":...}` forces the call. This is the idiomatic way to get reliable structured JSON from the Anthropic API.
- **Approval gate is plan-level, not loop-level.** Once a plan is approved, the executor runs without further pauses. Replans inherit the original approval scope — the replanner prompt is responsible for staying inside the approved tool set.
- **Manual tool-use loop**, not the SDK's beta tool runner — gives control over the planner / executor / critic split. The executor never sees `submit_plan` or `submit_verdict`; the planner/critic never see bio tools.
- **Trivial plans short-circuit the critic.** Paying for critic evaluation on "GC content of ATGC" is wasted tokens.
- **One replan attempt, then honest failure.** If the critic rejects both attempts, the response includes the second draft + a "remaining concerns" note. No silent loops, no fabrication.
- **Prompt caching markers** on the last tool definition + last system block; activate automatically once the prefix crosses Sonnet 4.6's 2048-token minimum.
- **Every persisted row carries `project_id`** (hardcoded to `default-project` until project CRUD lands).
- **Trace step types**: `plan` / `replan` / `approval_requested` / `approval_decision` / `llm_call` / `tool_call` / `tool_error` / `refusal` / `critique` / `final`. Each carries its own structured payload.
- **BLAST is remote-only** for now (NCBI public API via `Bio.Blast.NCBIWWW`, wrapped in `asyncio.to_thread`). Local BLAST+ binary integration is deferred until the synchronous round-trip becomes the bottleneck.
- **Durable jobs (opt-in Celery).** With `BIOFORGE_TASK_QUEUE=celery` (+ Redis + Postgres + a worker; the `workers` compose profile) a run becomes a persisted job: `POST /agent/run` enqueues and returns immediately, the worker executes it and streams each step into the shared `Trace` row, and `GET /agent/{id}/stream` polls that row (live or catch-up) while `POST /agent/{id}/cancel` revokes it. The default `inline` backend runs everything in-process, behaviorally unchanged. See [Running with Celery](#running-with-celery-durable-jobs).
- **Streaming via `on_step` callback.** Every step-producing function in the agent loop accepts `on_step: Callable[[AgentStep], Awaitable[None]]`. The SSE endpoints plug a queue-backed callback in and drain it into `text/event-stream`. Default `None` preserves the synchronous JSON path. Callback errors are swallowed so a disconnected SSE client doesn't abort the agent run.
- **Memory injected into the planner, NOT the system prompt.** System prompt is cached (Anthropic prompt-caching); injecting per-project context there would break the cache. Instead memory rides on the planner's user message, which is per-run anyway. `load_relevant_memory()` returns the empty string when there's nothing useful, so the planner's input stays unchanged for empty projects.
- **Memory tools reach DB via ContextVars, not parameters.** `recall_memory` and `remember` read `get_current_project_id()` / `get_current_db_session()` set by `AgentContextScope` in the API layer. Bio tools that don't need DB access ignore the ContextVars entirely. Tools called outside a scope raise `ToolError` rather than silently no-op'ing.
- Tests use committed fixtures generated by `tests/fixtures/regenerate.py`. Online suite (`pytest -m online`) hits real APIs and is deselected by default.
