# Phase 5: Multi-Agent Split + Workflow Engine

This document captures the architectural plan for Phase 5 of BioForge. It is a
companion to the contracts in `bioforge.agent.roles` and `bioforge.workflows`,
which already ship the Protocol interfaces these designs target.

Status: **foundations laid (this slice)**. Actual split + Nextflow integration
follow in Phase 5.1–5.5 slices.

## Why Phase 5

The Phase 0–4 agent is a single monolithic loop in `agent/loop.py`. It works
fine for single-protein questions (~10 tool calls, ~30 s wall clock), but
hits its limits when:

1. **Long-running workflows.** A full RNA-seq pipeline takes hours. Running it
   inside the agent loop blocks the event loop, fills the LLM's context with
   irrelevant intermediate state, and gives the user nothing to do while it
   runs.
2. **Multi-protein / multi-experiment questions.** "Compare the structures of
   all 15 cancer-related kinases" wants 15 sub-runs in parallel, each
   producing its own short report. One Claude context can't hold 15 plans at
   once.
3. **Heterogeneous compute.** AlphaFold predictions belong on a GPU. BLAST
   against UniRef90 belongs on a fat node with a local copy. The agent doesn't
   care WHERE the work runs; the workflow engine does.

## The split

Phase 5 splits the loop's three responsibilities into three independently-
managed roles, each with its own Anthropic context and budget:

```
┌─────────────────────────────────────────────────────────────────┐
│                         User Request                            │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
                ┌────────────────────────┐
                │     Coordinator        │  ← thin router, holds the
                │  (agent/coordinator.py)│    user's full intent
                └─┬──────────┬──────────┬┘
                  │          │          │
        ┌─────────▼───┐  ┌──▼─────┐  ┌─▼────────┐
        │   Planner   │  │Executor│  │  Critic  │
        │  (sub-agent)│  │ (sub-) │  │  (sub-)  │
        └─────────────┘  └────┬───┘  └──────────┘
                              │
                              ▼
                  ┌─────────────────────────┐
                  │     Workflow Engine     │
                  │  (workflows/engine.py)  │
                  └────────┬────────────────┘
                           │
                ┌──────────┼──────────────┐
                ▼          ▼              ▼
            (BLAST)   (AlphaFold)   (RNA-seq pipeline)
```

### Roles (`bioforge.agent.roles`)

Defined as `Protocol`s today so the existing Phase 0–4 in-loop functions can
be wrapped without breaking changes:

* `Planner` — produces a Plan from a goal. Context: prior memory, available
  tools. **Phase 5.1: extract `agent/loop.py::_try_plan` into a `Planner`
  implementation, swap the call site to dispatch through the role.**
* `Executor` — runs a Plan, returns ExecutionResult. **Phase 5.2: same shape
  refactor for `agent/loop.py::_execute`.**
* `Critic` — judges whether the response satisfies the goal. **Phase 5.3:
  same shape for `agent/loop.py::_try_critique`.**

After 5.1–5.3, swapping any role for a remote sub-agent (e.g. a smaller/faster
model just for planning) is a config change, not a code change.

### Workflow engine (`bioforge.workflows.engine`)

`WorkflowEngine` is a Protocol with four methods:

* `submit(steps) → WorkflowRun` — returns immediately with a run handle.
* `stream_progress(run_id) → AsyncIterator[WorkflowEvent]` — for SSE.
* `cancel(run_id) → None` — interrupts a running workflow.
* `get_run(run_id) → WorkflowRun` — retrieves current state + final outputs.

`LocalWorkflowEngine` is the in-process baseline (ships now). Steps run
sequentially in the current Python process; deps are topologically sorted;
events flow through an `asyncio.Queue`. Loses state on restart.

`NextflowEngine` is the planned scaled implementation:

1. Translate `list[WorkflowStep]` → a `.nf` script. Each step → one Nextflow
   `process` block; `depends_on` → channel wiring; `inputs` → script
   parameters.
2. Shell out to `nextflow run -with-trace -with-report` and capture the run ID.
3. Stream progress by tailing the trace file. Each line corresponds to one
   process completion.
4. Cancel by sending SIGINT to the Nextflow process and reading its cleanup
   markers.
5. Implement caching by hashing the workflow definition + inputs; on cache hit,
   skip submission and replay the previous output via `get_run`.

Once Nextflow lands, the agent loop never touches it directly — workflow tools
(future `submit_blast_workflow`, `submit_alphafold_workflow`) take a
`WorkflowEngine` dependency and the binding is set at app construction time.

## Migration order (non-binding sketch)

1. **Phase 5.0 (this slice):** Protocols + LocalWorkflowEngine + NextflowEngine
   stub + tests + this doc.
2. **Phase 5.1:** Wrap `_try_plan` in a `LocalPlanner(Planner)` class. The
   coordinator dispatches plan calls through the role; everything else
   unchanged. Test for behavioral equivalence.
3. **Phase 5.2:** Same for `_execute` → `LocalExecutor(Executor)`.
4. **Phase 5.3:** Same for `_try_critique` → `LocalCritic(Critic)`. At this
   point each role has a stable Protocol-typed boundary.
5. **Phase 5.4:** First workflow-using tool — `submit_alphafold_batch` —
   takes a `WorkflowEngine` dep and uses `LocalWorkflowEngine`. Test the
   full submit → stream → results path with multiple UniProt IDs.
6. **Phase 5.5:** Implement `NextflowEngine.submit` + `stream_progress`.
   Wire the agent's `WorkflowEngine` dependency to it behind a feature flag.
   Re-run the Phase 5.4 test against the Nextflow path.

## Non-goals for Phase 5

* **Distributed planner/executor/critic.** The role split happens in-process
  in 5.1–5.3. Cross-process roles are a Phase 6+ concern (would need a
  message bus, auth, etc.).
* **A new LLM per role.** All three start sharing the configured Claude
  Sonnet model. Per-role model selection is a tuning step after the split
  proves out.
* **Replacing Celery for short-running tool calls.** Workflows are for
  multi-minute work. Sub-second tool calls (gc_content, reverse_complement)
  stay in the agent's direct tool-call path. The engine is opt-in per tool.

## What this slice ships

* `backend/src/bioforge/agent/roles.py` — Planner/Executor/Critic Protocols
  + dataclasses (`PlanContext`, `ExecutorContext`, `CriticContext`,
  `ExecutionResult`).
* `backend/src/bioforge/workflows/engine.py` — `WorkflowEngine` Protocol +
  full working `LocalWorkflowEngine` + `WorkflowStep`/`WorkflowEvent`/
  `WorkflowRun`/`WorkflowStatus` + topological sort.
* `backend/src/bioforge/workflows/nextflow_engine.py` — stub that satisfies
  the Protocol so dependent code can be typed today.
* `backend/tests/test_workflow_engine.py` — happy path, dependency ordering,
  cycle detection, cancellation, step failure propagation, multi-step
  fanout.
* `backend/tests/test_agent_roles.py` — verifies the existing in-loop
  functions satisfy the Protocol shapes.
* This doc.

Nothing in the agent loop changes. Existing 325 backend tests still pass.
