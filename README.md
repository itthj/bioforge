# BioForge

Agentic AI bioinformatics platform. Current state: Phase 1 (in progress) — full plan→approval→execute→critique→replan loop. Three tools (`gc_content`, `reverse_complement`, `blast`). Structured-output planner and critic via Anthropic forced tool-use. Persisted traces with the approval gate wired through to a `POST /agent/{trace_id}/approve` endpoint.

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

> **DB schema note**: the `traces` table grew two columns (`awaiting_approval_plan`, `approval_reasons`) in Phase 1. If you have a `bioforge.db` from before this slice, delete it before restarting — `init_db()` is `create_all` not `migrate`. Real migrations (Alembic) arrive when we have non-disposable data.

## Layout

```
backend/src/bioforge/
  api/agent.py            POST /agent/run, POST /agent/{id}/approve, GET /traces/{id}
  agent/
    planner.py            Plan / make_plan → forced submit_plan tool-use
    critic.py             CriticVerdict / evaluate → forced submit_verdict
    approval.py           requires_approval(plan, registry) → ApprovalRequirement
    loop.py               plan → approval → execute → critique → replan-once → respond
    llm.py                AsyncAnthropic wrapper, cost accounting
    prompts/              system.md, planner.md, critic.md (markdown, not strings)
  tools/                  @register_tool registry
    sequence/gc_content       cheap
    sequence/reverse_complement  cheap
    sequence/blast            EXPENSIVE — triggers approval gate
  db/                     SQLAlchemy async; Trace with project_id + pending plan
backend/tests/            57 tests: unit + adversarial + multi-step + approval flow
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
- Tests use committed fixtures generated by `tests/fixtures/regenerate.py`. Online suite (`pytest -m online`) hits real APIs and is deselected by default.
