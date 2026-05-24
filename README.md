# BioForge

Agentic AI bioinformatics platform. Current state: Phase 1 (in progress) — full plan→approval→execute→critique→replan loop with SSE streaming. Five tools (`gc_content`, `reverse_complement`, `blast`, `recall_memory`, `remember`). Projects + persistent project memory with audit/edit endpoints. Structured-output planner and critic via Anthropic forced tool-use.

## Quickstart

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

> **DB schema note**: the schema gained two new tables (`projects`, `project_memory`) plus columns on `traces` over Phase 1. If you have a `bioforge.db` from before this slice, delete it before restarting — `init_db()` is `create_all` not `migrate`. Real migrations (Alembic) arrive when we have non-disposable data.

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
- **BLAST is remote-only** for now (NCBI public API via `Bio.Blast.NCBIWWW`, wrapped in `asyncio.to_thread`). Local BLAST+ binary integration and a job queue (Celery) are deferred until the synchronous round-trip becomes the bottleneck.
- **Streaming via `on_step` callback.** Every step-producing function in the agent loop accepts `on_step: Callable[[AgentStep], Awaitable[None]]`. The SSE endpoints plug a queue-backed callback in and drain it into `text/event-stream`. Default `None` preserves the synchronous JSON path. Callback errors are swallowed so a disconnected SSE client doesn't abort the agent run.
- **Memory injected into the planner, NOT the system prompt.** System prompt is cached (Anthropic prompt-caching); injecting per-project context there would break the cache. Instead memory rides on the planner's user message, which is per-run anyway. `load_relevant_memory()` returns the empty string when there's nothing useful, so the planner's input stays unchanged for empty projects.
- **Memory tools reach DB via ContextVars, not parameters.** `recall_memory` and `remember` read `get_current_project_id()` / `get_current_db_session()` set by `AgentContextScope` in the API layer. Bio tools that don't need DB access ignore the ContextVars entirely. Tools called outside a scope raise `ToolError` rather than silently no-op'ing.
- Tests use committed fixtures generated by `tests/fixtures/regenerate.py`. Online suite (`pytest -m online`) hits real APIs and is deselected by default.
