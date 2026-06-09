# Plan — Durable job model + Celery queue (roadmap Phase 1)

**Status: PLAN for review. No code written.** This is the deliverable requested before any
implementation. The platform deep-dive thread (P0–P2b) is shipped; this is the one remaining
non-blocked roadmap item (P3 file-upload stays blocked on auth). It is **phase-sized** — design
it as its own phase, not a quick slice.

---

## 0. Goal and what flips

Today a run is **synchronous**: `POST /agent/run[/stream]` executes `run_agent` *inside the request*
and streams progress over SSE from that same in-process coroutine. If the client disconnects or the
API restarts mid-run, the run is lost; a slow tool (BLAST) holds the HTTP worker; nothing scales past
one process.

The phase makes a run a **durable, asynchronous job**: submitting a run enqueues it, returns a job id
immediately, executes it in a **Celery worker**, persists progress as it goes, and lets the client
**stream OR reconnect-and-catch-up** to a still-running or finished job. This is the DNAnexus/“runs
are first-class, survive disconnects, scale horizontally” property, building on the run-history work
already shipped (P0).

**Honesty rail (carry the project’s discipline):** Inline stays the default and behavioral-equivalent;
Celery is opt-in. We never fake job state — a job’s status is the worker’s real state, persisted.

---

## 1. What ALREADY exists (do NOT redo — verified in-repo)

The skeleton is real and must be reused, not rebuilt:

- **`backend/src/bioforge/tasks/celery_app.py`** — a `Celery("bioforge")` app (broker+backend =
  `BIOFORGE_REDIS_URL`, default `redis://redis:6379/0`), sane long-task tunables (`acks_late`,
  `prefetch_multiplier=1`, 15-min hard / 14-min soft limits), and one **generic dispatcher task**
  `bioforge.tasks.run_tool(tool_name, tool_input)` that routes through `execute_tool` and returns
  `model_dump()`.
- **`backend/src/bioforge/tasks/queue.py`** — a `TaskQueue` Protocol (`submit` / `result`) with
  `InlineTaskQueue` (default, runs in-process, behavior-preserving) and `CeleryTaskQueue` (enqueues +
  polls `AsyncResult` off the event loop via `asyncio.to_thread`). Selected by
  `BIOFORGE_TASK_QUEUE` (`inline` | `celery`) via `get_task_queue()` / `reset_task_queue()`.
- **`docker-compose.yml`** — already defines `redis` (7-alpine, digest-pinned) and a `worker` service
  (`celery -A bioforge.tasks.celery_app worker --concurrency=2`, `depends_on: redis`), alongside
  `backend`, `minio`, `frontend`.
- **`backend/tests/test_task_queue.py`** — covers the queue abstraction.
- **`db.models.Trace`** — already persists a run: `status` (String(32)), `goal`, `response_text`,
  `steps` (JSON), token/cost columns, `awaiting_approval_plan`, `approval_reasons`, `created_at`,
  `completed_at`. This is our durable job row.

**Gaps (what this phase actually builds):**
1. The executor (`agent/loop.py:548`) calls `await execute_tool(...)` **directly** — the task queue is
   not wired in anywhere.
2. There is **no durable RUN job** — the API runs the agent in-request; SSE streams from memory only.
3. **No cross-process progress streaming** — `on_step` callbacks fire in the agent process; a worker
   in another process can’t reach the API’s SSE generator.
4. **No config integration** — `queue.py` / `celery_app.py` read `os.environ` directly, bypassing the
   pydantic `Settings`. Cancellation (P1b Stop) has no worker-side revoke path.

---

## 2. The architectural decision (the crux) — RECOMMENDED: job = the whole run

Two levels are possible; pick deliberately:

- **(A) Per-tool queueing** — wire `get_task_queue().submit()` at `loop.py:548` so individual tool
  calls run on the worker. *Small, but low value:* the agent coroutine still blocks awaiting each
  tool, still holds the HTTP worker, the run still dies on disconnect. The soundness/grounding gates
  that wrap `execute_tool` stay in the agent process. This does **not** deliver durability.
- **(B) Per-run job (RECOMMENDED)** — the whole `run_agent` executes in a Celery task. `POST /agent/run`
  enqueues and returns `{trace_id, status:"queued"}` immediately; the worker runs the agent, writing
  each `AgentStep` to durable storage as it goes; the client streams or reconnects via
  `GET /agent/{trace_id}/stream`. This is the durability/scale win and the honest “runs are jobs.”

**Recommendation: build (B).** (A)’s generic `run_tool` task stays available for a later
“fan-out heavy tools” optimization, but it is not the phase’s point. The unit of durability is the
**run**, matching the run-history/provenance model already shipped.

---

## 3. The hard problem: cross-process progress streaming

With the agent in a worker, the API can no longer read `on_step` from memory. Two viable designs:

- **(B1) DB-polling (simplest, no new infra):** the worker appends each step to `Trace.steps`
  (incrementally) and bumps `Trace.status`; the API’s `GET /agent/{id}/stream` **polls the Trace row**
  every ~500 ms and emits new steps as SSE until `status` is terminal. Pros: zero new moving parts,
  reuses Postgres, naturally supports reconnect/catch-up (just read the row). Cons: ~0.5 s latency,
  write amplification on `steps`.
- **(B2) Redis pub/sub (lower latency):** the worker publishes each step to a Redis channel keyed by
  `trace_id`; the API subscribes and relays to SSE, **and** persists to the Trace for durability/
  catch-up. Pros: near-real-time. Cons: more moving parts; must still persist for reconnect.

**Recommendation: ship (B1) first** (polling) — it’s honest, durable, reconnect-friendly, and needs no
new infra beyond what exists; **(B2) is a follow-up** if latency matters. Redis is already a dependency
(broker), so (B2) is incremental later.

Either way, the **on_step contract already exists** (`run_agent(on_step=…)`), so the worker just swaps
the in-memory callback for a “persist step to Trace (+ optionally publish)” sink. Minimal surface.

---

## 4. Job lifecycle + persistence

Reuse `Trace` as the job row. Extend the `status` lifecycle (it’s a free-form String(32) today):

```
queued -> running -> {completed | completed_after_replan | refused | error | iteration_cap
                      | cancelled | pending_approval}
```

- `queued`: row created at submit, before the worker picks it up.
- `running`: worker started; steps stream in.
- terminal states are the existing `AgentStatus` values (no new vocabulary downstream).

New columns (one Alembic migration; all nullable/defaulted for back-compat):
- `job_backend` (String(16), default `"inline"`) — provenance: which queue ran it.
- `task_id` (String(64), nullable) — the Celery task id, for cancel/inspection.
- `started_at` (DateTime, nullable) — when the worker began (distinct from `created_at`/`completed_at`).

`pending_approval` already persists `awaiting_approval_plan`; the resume path (P2b) must also enqueue
the resumed execution as a job (the approve endpoint becomes “enqueue resume,” same pattern).

---

## 5. Cancellation (integrate with P1b Stop)

P1b’s Stop currently aborts the SSE fetch; the backend cancels the in-flight **in-process** task on
disconnect. With a worker, Stop must **revoke the Celery task**: a `POST /agent/{trace_id}/cancel`
that calls `celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")`, sets
`Trace.status="cancelled"`, and the worker’s signal handler flushes a partial trace. The frontend Stop
button points at this endpoint when `job_backend=="celery"` (inline keeps today’s disconnect-cancel).

---

## 6. Slice breakdown (each its own `feat/*` branch, FF-merged, suite green)

1. **Config + settings integration** — add `task_queue`, `redis_url`, celery tunables to the pydantic
   `Settings`; have `celery_app.py` / `queue.py` read settings (keep env override). Digest-pin already
   done in compose. Hermetic tests. *No behavior change (inline default).* 
2. **Durable job persistence + the step sink** — `Trace` migration (§4); a `persist_step` `on_step`
   sink that incrementally writes `steps`/`status`/`started_at`. Unit-tested against the in-process
   path first (still inline) so the persistence is proven before the worker exists.
3. **Enqueue a run as a job** — new Celery task `run_agent_job(trace_id, goal, project_id, autonomy)`
   in `celery_app.py` that loads/creates the Trace, runs `run_agent` with the `persist_step` sink, and
   writes the terminal state. `POST /agent/run` (celery mode) creates a `queued` Trace + enqueues +
   returns immediately. Inline mode unchanged.
4. **Stream/reconnect endpoint** — `GET /agent/{trace_id}/stream` (B1 polling) emits persisted steps as
   SSE until terminal; works for a live job AND a finished one (catch-up). Frontend points the run view
   at it in celery mode; History already renders finished traces.
5. **Cancellation** — `POST /agent/{trace_id}/cancel` → revoke + `cancelled`; wire P1b Stop to it in
   celery mode; worker SIGTERM handler flushes partial trace.
6. **Approval resume as a job** — the P2b approve/resume path enqueues the resumed execution (same
   pattern as #3) so review-mode runs are durable too.
7. **Infra verification + docs** — a `-m docker` e2e that brings up redis+worker (compose) and runs a
   real job end-to-end (submit → poll → terminal), digest-pinned; README/handoff “running with Celery”
   section; mark the roadmap item done.

Slices 1–2 are pure/hermetic and land with zero behavior change; the worker only becomes load-bearing
at slice 3.

---

## 7. Testing strategy (real biology; hermetic where possible)

- **Hermetic (default CI):** run Celery in **eager mode** (`task_always_eager=True`) so
  `run_agent_job` executes in-process with a fake LLM — proves the enqueue→persist→terminal path and
  the polling stream WITHOUT Redis or a worker. Plus the existing `test_task_queue.py` for the queue.
- **`-m docker` gated e2e:** compose up `redis` + `worker`, submit a real cheap run (e.g. GC of a
  lambda fragment), poll the stream to a terminal state, assert the persisted Trace matches. Mirrors
  the model-image e2e discipline; deselected by default, runnable locally / in a gated CI job.
- **No new fakes for biology** — tools still run real fixtures; only the transport (inline vs worker)
  changes, and behavioral equivalence between the two is itself an assertion.

---

## 8. Migration / backward-compat (the safety rail)

- `BIOFORGE_TASK_QUEUE=inline` stays the **default**; every existing test + the single-user local path
  is byte-for-byte unchanged. Celery is opt-in (`=celery` + Redis + worker).
- The `Trace` migration is additive (nullable/defaulted) — old rows load fine.
- The new `/stream` + `/cancel` endpoints are additive; the existing sync `/agent/run` + `/run/stream`
  keep working in inline mode.

---

## 9. Risks and guards (none papered over)

- **Serialization across the broker:** tool inputs/outputs are JSON-safe (already `model_dump()`ed for
  the generic task). Guard: assert JSON-round-trip in the job-enqueue path; reject non-serializable
  early rather than failing in the worker.
- **AsyncSession in the worker:** the worker is a separate process; it needs its own DB engine/session
  lifecycle (not the request-scoped one). Guard: a worker-local sessionmaker; tested in eager mode
  with the test DB.
- **Lost worker / stuck `running`:** a job whose worker dies stays `running` forever. Guard: the
  Celery soft-time-limit (14 min) flips it to `error`; a reconnect that sees `running` past the limit
  reports staleness honestly (never a fake `completed`).
- **Streaming latency (B1 polling):** ~0.5 s — acceptable for a research tool; documented; B2 pub/sub
  is the upgrade path.
- **Approval + jobs interaction:** a `pending_approval` job is terminal-for-now and resumes as a NEW
  enqueue (slice 6); we never leave a worker blocked waiting on a human.

---

## 10. Open decisions for the user (own these before slice 3)

1. **Scope = per-run job (B), confirm?** (Recommended.) Or also wire per-tool queueing (A) now?
2. **Streaming = DB-polling (B1) first, Redis pub/sub (B2) later — OK?** Or go straight to B2?
3. **CI for the `-m docker` Celery e2e:** add a gated job now, or keep it local-only initially?
4. **Postgres vs SQLite for the worker’s DB in dev:** compose uses Postgres; confirm the worker shares
   that DB (it must, to persist the same Trace the API reads).

---

## 11. Out of scope (explicitly deferred)
- Per-tool fan-out / parallel tool execution via the generic `run_tool` task (optimization, later).
- Redis pub/sub low-latency streaming (B2) — follow-up after B1 lands.
- Multi-worker autoscaling, priority queues, rate limits, dead-letter handling (ops maturity, later).
- Auth-gated per-user job isolation — tied to the (blocked) auth phase, not this one.
- File/dataset upload (P3) — blocked on auth, unchanged by this phase.
