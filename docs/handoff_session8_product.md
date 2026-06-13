# BioForge - Session 8 handoff (product-hardening arc: durable jobs + accounts + BYO-data + cost controls)

> **READ THIS FIRST.** This is the live resume point. It supersedes the older `docs/handoff.md`
> (benchmark/v4 finalization) and `docs/handoff_platform_recommendations.md` (P0-P3 deep-dive) for
> *what to do next*, though both remain accurate for their own history. The user's goal this arc:
> **take BioForge from "honest research prototype" to "a working product"** by solving, one by one,
> the limitations raised in the capabilities review (below).

---

## 0. Kickoff prompt for the next session (copy-paste)

```
Continuing BioForge (github.com/itthj/bioforge). main @ ce66003 (after PRs #15 + #16). Local repo:
C:\Users\james\OneDrive\Documents\BIOTECH 101\bioforge; venv at .venv; node NOT on PATH (prepend
C:\Users\james\AppData\Local\Programs\nodejs); gh is in WSL Ubuntu (wsl -d Ubuntu -- bash -lc
"gh ..."). USER GATES ALL MERGES but has said "merge each green phase + keep building to a working
product" -- so: build a phase on its own branch, suite green, push, open PR, get CI green, merge,
sync main, continue. Commit messages ASCII only. gh pr edit is BROKEN here (Projects-classic) -> use
gh api ... -X PATCH.

First: read docs/handoff_session8_product.md (this doc) -- it is the live resume point.

STATE: We are solving the 6 "what it needs for real research" limitations 1-by-1.
  #1 Logins + bring-your-own-data .... DONE + MERGED (PRs #15 backend, #16 frontend).
  #6 Cost controls (budgets/rate-limits) .. DONE on branch feat/cost-controls (pushed, green) --
     NEEDS: open PR, CI green, merge, sync main. (No PR opened yet.)
  #5 Pipelines (nf-core/Nextflow) ......... TODO (next).
  #2 Accuracy calibration ................. TODO (honest/partial).
  #4 Wet-lab feedback loop ................ TODO (software scaffold; data is the user's).
  #3 Always-on GPU ........................ NOT a code problem (hardware/$$); only a cloud-GPU
     execution path is buildable.

RESUME HERE: open the PR for feat/cost-controls (#6), merge it, then build #5 (pipelines). See
section 6 of this doc for the #6 PR steps and the #5 design sketch.

Plan before coding; vertical slices; opt-in flags keep default behavior byte-identical; suite green
before commit; verify in a real browser when a change is UI-observable (you can run the backend +
vite dev server without an API key for auth/upload/usage flows).
```

---

## 1. Exact git + CI state (as of this handoff)

- **`main` @ `ce66003`** (= "Merge pull request #16"). Everything below #16 is merged + CI-green.
- **Merged this arc (newest first):** PR #16 auth frontend (`ce66003`), PR #15 auth backend
  (`5facd03`), PR #14 Celery durable jobs + PR #13 plan (`1a63707`), then the pre-arc main.
- **`feat/cost-controls`** is PUSHED, **1 commit on top of `main`**, contains Limitation #6
  (cost controls). It is **green locally** (backend 1127 / frontend 180 / tsc / build) but **has NO
  PR yet** -> first action next session.
- Suite sizes on `feat/cost-controls`: **backend 1127 passed**, 2 skipped, 18 deselected; **frontend
  180 vitest**; `tsc --noEmit` clean; production build clean (the >500 kB chunk warning is
  pre-existing igv/molstar bundling, not ours).
- Untracked `docs/plan_edit_outcome_benchmark.md` is **pre-existing, not ours -- leave it.**
- New runtime deps added this arc (in `pyproject.toml`): **`argon2-cffi`**, **`python-multipart`**,
  and the **`postgres`** extra (`asyncpg`, `psycopg[binary]`). The local venv already has them; a
  fresh clone/CI gets them via `pip install .` / `.[postgres]`.

---

## 2. What this arc shipped (the story so far)

### Phase A - Celery durable jobs (roadmap Phase 1) -- MERGED (PRs #13 plan, #14 impl)
A run is now a durable, opt-in async job. Off by default (`BIOFORGE_TASK_QUEUE=inline`), byte-identical.
- `backend/src/bioforge/tasks/celery_app.py`: `run_agent_job` + `resume_agent_job` tasks + a
  `_run_async` sync->async bridge (works in a real worker AND under `task_always_eager` in tests).
- `backend/src/bioforge/agent/jobs.py`: `create_queued_trace`, `make_step_persister(commit=)`,
  `finalize_trace`, `finalize_resumed_trace`, `run_agent_job_async`, `resume_agent_job_async`
  (worker builds its OWN engine/sessionmaker from `settings.db_url`, disposes per task).
- `api/agent.py`: `POST /agent/run[/stream]` enqueue in celery mode (returns `queued` trace_id);
  `GET /agent/{id}/stream` (DB-poll the Trace ~0.5s, honest staleness backstop); `POST
  /agent/{id}/cancel` (revoke). Frontend: `cancelRun` + a `queued` SSE event + Stop->cancel.
- Infra: `docker-compose.yml` `workers` profile = redis + a Celery worker + **Postgres** (the shared
  run DB; SQLite-over-volume is unreliable for poll-while-write). `docker-compose.e2e.yml` +
  `backend/tests/test_celery_e2e_docker.py` (opt-in `-m docker` + `BIOFORGE_CELERY_E2E=1`).
- Tests: `test_celery_jobs.py`, `test_job_stream.py` (Celery eager, hermetic).

### Phase B - Limitation #1: accounts + bring-your-own-data (roadmap Phase 6) -- MERGED (PRs #15, #16)
Opt-in via `BIOFORGE_AUTH_ENABLED` (default false = anonymous single-user, byte-identical). There is
ALWAYS a "current user": the bootstrapped default user when auth is off, the authenticated user when on.
- **Models** (`db/models.py`): `User`, `AuthSession` (stores only the token's sha256), `UploadedFile`;
  `Project.user_id` (owner). Migrations `acc01a7b0c2d` (accounts; seeds the non-loginable default
  user + backfills projects) and `facc01117a2b` (uploaded_files). Drift test green.
- **Auth** (`auth/passwords.py` argon2id, `auth/tokens.py` opaque bearer; `api/auth.py`):
  `POST /auth/register|login|logout`, `GET /auth/me`, `GET /config` (lives in `main.py`),
  `get_current_user` dep, `owns()` / `require_project_access()` / `require_owned_project()` helpers.
  `register` also creates a **per-user starter project** (a new user doesn't own default-project).
- **Isolation**: every project/trace/memory/file/provenance endpoint in `api/projects.py` +
  `api/agent.py` is owner-checked (404, never 403) -- no-op when auth off.
- **Uploads** (`api/files.py`): `POST/GET/DELETE /projects/{id}/files` wiring the already-built
  `storage/adapter.py` (Local + MinIO, project-isolated); size cap (413) + extension allowlist (415)
  + empty guard (422); `uploaded_files` registry.
- **Agent reads your data**: `tools/meta/file_tools.py` `read_uploaded_file` tool (FASTA-aware) ->
  feeds `design_guides` / `parse_vcf` / `gc_content`.
- **Frontend**: `lib/auth.ts` (token store + a ONE-TIME global fetch interceptor that attaches the
  Bearer token to same-origin requests incl. SSE -- no per-call-site edits); `components/AuthGate.tsx`
  (reads /config; gates on login; fails open if backend down), `LoginScreen.tsx`, `FilesPanel.tsx`
  (a "Data" tab); `api/{auth,config,files}.ts`; `App.tsx` takes optional `auth` prop, shows user +
  Sign out, selects one of YOUR projects on login; `main.tsx` installs the interceptor + wraps in
  AuthGate. **Verified in a real browser** (register -> starter project -> upload -> reload).
- Tests: `test_auth.py`, `test_auth_isolation.py`, `test_files.py`, `test_read_uploaded_file.py`,
  `frontend/src/lib/__tests__/auth.test.ts`.

### Phase C - Limitation #6: cost controls -- DONE on feat/cost-controls (PUSHED, NOT MERGED)
Opt-in budgets + rate limits on the user model (both default off -> no-op).
- `config.py`: `rate_limit_enabled` / `rate_limit_runs_per_hour` (default 60) / `budget_enabled` /
  `monthly_budget_usd` (default 0 = unlimited).
- `api/usage.py`: `compute_usage` + `enforce_run_quota` (spend/run counts derived from `Trace` rows
  joined to the user's projects -- NO new tables) + `GET /usage`. `enforce_run_quota` is called at
  the top of `POST /agent/run` and `/agent/run/stream` (after the access check): 429 over rate, 402
  over budget. Pre-gate on spend-so-far (a run in flight finishes; the NEXT one is blocked).
- Frontend: `api/usage.ts` + `components/UsageChip.tsx` (header "$X this month", or "$X / $Y" with a
  budget). `/usage` added to the vite dev proxy + nginx.
- Tests: `backend/tests/test_usage.py` (402/429/no-op/under-cap/compute/endpoint).

---

## 3. The limitations roadmap (the "make it a working product" backlog)

From the capabilities review the user asked for. Status after this arc:

| # | Limitation | Status | Notes |
|---|---|---|---|
| 1 | Logins + bring-your-own-data | DONE + MERGED | PRs #15/#16. The biggest unlock. |
| 6 | Cost controls (budgets/rate-limits) | DONE + MERGED | PR #17. |
| 5 | Pipelines / instruments (nf-core, LIMS) | DONE + MERGED | PR #18. |
| 2 | Accuracy you can stake a decision on | DONE + MERGED | PR #19. benchmarks/calibration.py (ECE/MCE/Brier) + GIAB QUAL consumer + CalibrationDiagram. |
| 4 | Wet-lab feedback loop | DONE + MERGED | PR #20. Prediction model, record->outcome->agreement reusing #2 modules; FeedbackPanel. |
| 3 | Always-on GPU compute | EXECUTION PATH DONE, NEEDS MERGE | feat/gpu-execution-path: provider-agnostic opt-in cloud-GPU backend (Null refuses honestly; Http runnable). Live GPU still a hardware/$$ decision. |

Honest framing to keep using with the user: 1/6/5 are pure software we can ship; 2/4 are buildable
scaffolding whose VALUE needs external inputs; 3 is hardware/money. "The integrity IS the product" --
never fake accuracy or wet-lab data.

---

## 4. Architecture decisions + gotchas learned this arc (do not relitigate / re-discover)

- **Opt-in flags are the pattern.** Every new capability is default-off so the existing suite +
  showcase + single-user path stay byte-identical: `BIOFORGE_TASK_QUEUE`, `BIOFORGE_AUTH_ENABLED`,
  `BIOFORGE_BUDGET_ENABLED`, `BIOFORGE_RATE_LIMIT_ENABLED`. Tests that need a feature flip it via
  `monkeypatch.setattr(settings, "...", ...)`.
- **There is always a current user.** `get_current_user` returns the bootstrapped default user when
  auth is off, so isolation/quota code never special-cases "no user". `owns()` / quotas are no-ops
  when their flag is off.
- **Frontend token plumbing = a global fetch interceptor** (`lib/auth.ts installAuthFetch`), installed
  once in `main.tsx`. Adds `Authorization` to same-origin requests only; doesn't clobber an explicit
  header. This is why no per-call-site changes were needed (incl. the SSE consumer).
- **Every new backend route prefix must be added to BOTH proxies** or it 404s through the SPA
  fallback: `frontend/vite.config.ts` (dev) AND `frontend/nginx.conf` (prod regex). Prefixes now:
  agent, auth, projects, traces, config, usage, health, benchmarks. nginx also has
  `client_max_body_size 60m` for uploads (default 1 MB would 413 them).
- **SQLite + tz-aware datetimes:** `DateTime(timezone=True)` round-trips NAIVE on SQLite. When you
  READ one back and compare in Python, coerce: `if dt.tzinfo is None: dt = dt.replace(tzinfo=UTC)`
  (see `api/auth.py _user_for_token`). In-SQL comparisons (e.g. `usage.py`) are fine -- SQLAlchemy
  binds both sides through the same formatter.
- **Model `default=` (e.g. `_new_id`) only fires at flush**, not at object construction. `api/files.py`
  generates the file id up front so it can also key the storage object.
- **A freshly-registered user owns NO default-project** (isolation) -> `register` creates a starter
  project AND the frontend selects one of the user's own projects on login. (Caught by browser test.)
- **gh pr edit is BROKEN on this repo** (Projects-classic GraphQL deprecation aborts it). Use
  `gh api repos/itthj/bioforge/pulls/<n> -X PATCH -f title=... -F body=@/mnt/c/.../body.md`.
  `gh pr create` / `gh pr merge` / `gh pr checks` / `gh api .../check-runs` all work.
- **Browser verification is worth it** and possible without an API key: auth/upload/usage flows don't
  call Claude. Run the backend with `BIOFORGE_AUTH_ENABLED=true BIOFORGE_DB_URL=sqlite+aiosqlite:///
  ./authdemo.db BIOFORGE_STORAGE_ROOT=./authdemo_storage` (uvicorn, port 8000) + the vite dev server
  (preview). NOTE: the preview's synthetic click/fill does NOT reliably drive React controlled forms
  -- drive them via `preview_eval` with the native value setter + `form.requestSubmit()`. Clean up the
  temp db/storage + stop the backend afterwards.

---

## 5. Commands (Windows; venv at `.venv`)

```
cd "C:/Users/james/OneDrive/Documents/BIOTECH 101/bioforge"
.venv/Scripts/python.exe -m pytest backend/tests/ -q -p no:cacheprovider        # 1127 passed
.venv/Scripts/python.exe -m ruff format <files> ; .venv/Scripts/python.exe -m ruff check backend/
# Frontend (PowerShell): $env:Path = "C:\Users\james\AppData\Local\Programs\nodejs;" + $env:Path
#   npm --prefix ...\frontend run typecheck ; npm --prefix ...\frontend test ; npm --prefix ...\frontend run build
# gh (WSL): wsl -d Ubuntu -- bash -lc "gh pr create -R itthj/bioforge --base main --head <branch> --title '...' --body-file /mnt/c/.../body.md"
#   merge: wsl -d Ubuntu -- bash -lc "gh pr merge <n> -R itthj/bioforge --merge"
#   CI:    wsl -d Ubuntu -- bash -lc "gh api repos/itthj/bioforge/commits/<SHA>/check-runs --jq '.check_runs[]|\"\(.name): \(.status)/\(.conclusion)\"'"
# sync after merge: git fetch origin; git checkout main; git merge --ff-only origin/main
```

ASCII commit messages; write the message to a temp file + `git commit -F <file>` (embedded quotes
break PS). End commit bodies with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
CRLF warnings on `git add` are harmless.

---

## 6. RESUME HERE - next actions, in order

### Step 1 (immediate): land #6 (cost controls)
```
wsl -d Ubuntu -- bash -lc "gh pr create -R itthj/bioforge --base main --head feat/cost-controls \
  --title 'Cost controls: per-user budgets + rate limits (Phase 6, Limitation #6)' \
  --body-file /mnt/c/Users/james/AppData/Local/Temp/<write-a-body>.md"
```
Then poll CI on the head SHA (Monitor or `gh api .../check-runs`), `gh pr merge <n> --merge`, and
`git fetch; git checkout main; git merge --ff-only origin/main`. It's green locally; CI will mirror.

### Step 2: DONE - Limitation #5 pipelines on feat/nfcore-pipelines (PR #18, awaiting CI+merge)

### Step 3: build Limitation #2 - accuracy calibration, its own branch off main
There is already a `backend/src/bioforge/workflows/nextflow_engine.py` (gated by the `nextflow`
pytest marker / `BIOFORGE_NEXTFLOW_ENABLED`) -- READ IT FIRST; build on it, don't rebuild. Sketch:
- A tool (e.g. `run_nextflow_pipeline`) or an API + a durable job (reuse the Celery job machinery
  from Phase A!) that runs an nf-core pipeline (rnaseq/sarek/atacseq) over an uploaded samplesheet /
  the user's uploaded FASTQs (Limitation #1 storage). Stream progress as steps; persist to the Trace.
- Honesty rails: digest-pin any container; never fake a pipeline result; gate heavy runs (`-m
  docker`/`-m nextflow`, opt-in) so default CI stays fast. There is a `bio-research:nextflow-development`
  skill available in this environment that knows nf-core patterns -- consider it.
- Vertical slice + tests (hermetic where possible: mock the nextflow invocation; one gated real run).

### Step 3+: #2 accuracy calibration, then #4 wet-lab loop scaffold, then flag #3 (GPU) to the user.
- #2: the benchmark suite (`backend/src/bioforge/benchmarks/`) already publishes 4 real numbers +
  reliability curves. "Calibration" = scale the gated runs / add held-out splits / surface calibration
  honestly. Do NOT fabricate; do NOT add ML-training code (published weights only).
- #4: a software loop -- store predictions, let the user upload measured outcomes (reuse uploads),
  recompute agreement/calibration. Real experiments are the user's; or calibrate vs published data.

---

## 7. Pointers
- Older history: `docs/handoff.md` (v4 benchmark finalization, sessions 1-7),
  `docs/handoff_platform_recommendations.md` (P0-P3 deep-dive; its P3 row is now updated to BUILT),
  `docs/plan_celery_durable_jobs.md` (the Celery design, PR #13), `docs/DEMO.md`, `docs/deploy.md`
  (now documents auth + uploads + the Postgres/worker profile), `docs/license_audit.md`.
- README has "Running with Celery", "Accounts & your own data" sections.
- Hard rules still in force: plan before coding; vertical slices; no heavy agent frameworks; real
  biology in tests; provenance from day one; typed everything; AI never fabricates biology;
  no unsourced constants; no license claims from memory; no ML-training code; behavioral equivalence
  is the gate (opt-in flags); the gated remainder is earned with real data, never faked.
```
