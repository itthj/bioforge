# Handoff — Platform deep-dive recommendations (frontend/UX + provenance)

**Purpose:** continue, seamlessly, the multi-slice effort that came out of a competitive
deep-dive (Benchling, LatchBio, DNAnexus, Geneious, IGV, agentic-UX 2026, FAIR/RO-Crate).
Read this together with the authoritative `docs/handoff.md` (backend/grounding state).

> **START HERE (first actions, in order):**
> 1. Read this doc + `docs/handoff.md`.
> 2. `git status` / `git branch` — confirm state (below).
> 3. **Merge P1b**: open a PR for `feat/stop-recovery` → `main` and merge via `gh` in WSL
>    Ubuntu (see "Workflow"), then sync local `main`.
> 4. Start **P2a** (linked viewers + figure/data export) on a fresh `feat/*` branch.

---

## 1. Where things stand (git)

- **Remote:** `https://github.com/itthj/bioforge.git` (owner `itthj`). Repo is **public**.
- **`main`** is at the PR #4 merge (`feat: provenance exports + run history + reproduce-in-code`).
- **`feat/stop-recovery`** (HEAD ~`4bb9f67`) — **P1b stop/recovery, PUSHED but UNMERGED, no PR yet.**
- Merged already: PR #1 dark-console redesign, PR #2 showcase page, PR #3 methods-report
  backend, PR #4 provenance links + run history + reproduce-in-code.
- **Live public showcase:** https://itthj.github.io/bioforge/showcase.html (served from the
  `gh-pages` branch; it's a static build of `frontend/showcase.html`, rebuild+force-push to update).
- Untracked `docs/plan_edit_outcome_benchmark.md` is **pre-existing, not ours — leave it.**

## 2. The recommendations and their status

| # | Recommendation | Status | Where |
|---|---|---|---|
| **P0** | Run history + permalink (browsable runs) | ✅ DONE, merged (PR #4) | see §4 |
| **P1a** | Reproduce-in-code (runnable script from a run) | ✅ DONE, merged (PR #4) | see §4 |
| **P1b** | Stop button + recovery routing | ✅ DONE, on `feat/stop-recovery`, **needs PR+merge** | see §4 |
| **P2a** | Linked viewers + figure/data export | ⏳ TODO (next) | see §5 |
| **P2b** | Edit the plan before approving | ⏳ TODO | see §5 |
| **P3** | File/dataset upload + registry | ⛔ **BLOCKED on auth** — do NOT build yet | see §5 |
| **P3** | Durable job model + queue (Celery) | ⏳ TODO, phase-sized (roadmap Phase 1) | see §5 |

**Differentiators to protect (don't dilute):** the grounding/anti-hallucination layer (unique
vs. all surveyed platforms), provenance/RO-Crate (already FAIR-aligned), and the agentic NL
interface. Do NOT chase DNAnexus's app-count or rebuild Benchling's ELN/LIMS.

## 3. Environment & workflow (CRITICAL — the next session needs these exact facts)

**Paths**
- Repo root / working dir: `C:\Users\james\OneDrive\Documents\BIOTECH 101\bioforge`
- Backend: `backend/` · Frontend: `frontend/`

**Node is NOT on PATH.** Node lives at `C:\Users\james\AppData\Local\Programs\nodejs`.
In every PowerShell call that needs npm/node, prepend it:
```
$env:Path = "C:\Users\james\AppData\Local\Programs\nodejs;" + $env:Path
```
Frontend commands (PATH set):
```
npm --prefix "C:\Users\james\OneDrive\Documents\BIOTECH 101\bioforge\frontend" run typecheck
npm --prefix "...\bioforge\frontend" test        # vitest (currently 139 passing)
npm --prefix "...\bioforge\frontend" run build
```

**Python venv:** `C:\Users\james\OneDrive\Documents\BIOTECH 101\bioforge\.venv\Scripts\python.exe`
```
Set-Location "...\bioforge\backend"
& "...\.venv\Scripts\python.exe" -m pytest tests/<file> -q          # run a subset; full suite is large
& "...\.venv\Scripts\python.exe" -m pytest tests/ -q -k "report or history or reproduce or streaming"
& "...\.venv\Scripts\python.exe" -m ruff check src/... tests/...    # ruff must be clean before commit
```

**`gh` CLI is NOT on Windows. It IS in WSL Ubuntu** (authed as `itthj`, `repo` scope, https).
- Default WSL distro is `docker-desktop` (no bash) — you MUST target Ubuntu: `wsl -d Ubuntu -- bash -lc "..."`.
- `gh pr create --fill` fails (login shell cwd ≠ repo); pass `-R itthj/bioforge --base main --head <branch>
  --title '...' --body-file /mnt/c/Users/james/AppData/Local/Temp/<file>.md`.

**Pushing to `main` is BLOCKED by the auto-mode classifier.** The required workflow (which the
user follows):
1. `git checkout -b feat/<slice>` (off `main`), implement, verify.
2. Commit locally. Push EXPLICITLY to the branch: `git push origin feat/<slice>` (NOT `git push`).
3. Create PR + merge via `gh` in Ubuntu: `gh pr create ... ; gh pr merge <n> -R itthj/bioforge --merge`.
4. Sync local: `git fetch origin; git checkout main; git merge --ff-only origin/main`.
- The user gates merges — typically build the slice, then they say "merge it". Ask before merging
  unless they've said to.

**Commit messages:** embedded double-quotes break PowerShell `git commit -m` (PS 5.1 native-arg
quirk). Write the message to a temp file with the Write tool (UTF-8 LF) and `git commit -F <file>`.
End commit bodies with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

**Preview/screenshots:** Claude_Preview MCP. `.claude/launch.json` is at
`C:\Users\james\OneDrive\Documents\BIOTECH 101\.claude\launch.json` (OUTSIDE the repo;
machine-specific node path; runs `node vite.js` rooted at `frontend`). Port 5173 is sometimes
held by `wslrelay`/`docker` — free it: `Get-NetTCPConnection -LocalPort 5173 -State Listen` →
`Stop-Process -Id <pid> -Force`. To see the showcase: `preview_start` → `preview_eval`
`window.location.href='/showcase.html'` → wait ~3s (Vite compiles on first hit) → `preview_screenshot`.
Screenshots of the main app hang against the down backend; the **showcase page has no backend
calls** and screenshots cleanly. Computed-style checks via `preview_inspect` are reliable.

**Build gotcha:** `vite.config.ts` is compiled to a **gitignored** `vite.config.js` by `tsc -b`.
Vite loads the `.js` first. If you edit `vite.config.ts` and run `node vite.js build` directly,
delete the stale `vite.config.js`/`.d.ts` first (or use `npm run build`, which runs `tsc -b`).
`postcss.config.js` + `tailwind.config.js` are cwd-robust (anchored to their file dir).

**Conventions:** vertical slices; each slice its own `feat/*` branch; suite green before commit;
typed everything (Pydantic v2 / TS strict); real-biology test fixtures; no faked tool calls.

## 4. What's already built (so you don't redo it)

**Backend (all merged to `main`):**
- `GET /projects/{id}/traces` — run history list (`TraceSummary`; paginated; `?q=` goal search).
  In `backend/src/bioforge/api/agent.py`.
- `GET /traces/{id}` (full), `/manifest` (JSON), `/ro-crate` (JSON-LD), `/report` (Markdown
  methods record), `/script` (runnable Python reproduce). All in `api/agent.py`.
- `backend/src/bioforge/provenance/`: `research_object.py` (manifest + RO-Crate),
  `methods_report.py` (`render_methods_report`), `reproduce.py` (`render_reproduce_script`),
  `__init__.py` (exports). `_result_from_trace()` in `api/agent.py` rehydrates `AgentResult`
  from a stored `Trace`.
- Autonomy: `requires_approval(..., force_review=)` (`agent/approval.py`) + `autonomy`
  ("auto"|"review") arg on `run_agent` (`agent/loop.py`) + on `AgentRunRequest`.

**Frontend (merged to `main` except P1b):**
- Design tokens: `src/index.css` (dark-console CSS vars), `tailwind.config.js`. Helper
  `src/lib/cn.ts`. Primitives `src/components/ui/` (Card, Chip, StatusDot).
- `src/App.tsx`: tabs (Chat/History/Memory/Accuracy), run state machine, autonomy toggle,
  **Stop/Retry + `cancelled` state (P1b)**, History wiring + `?run=<id>` permalink deep-link.
- `src/components/`: `TraceView`+`StepCard` (progressive-disclosure timeline), `FinalCard`
  (status + grounding chip + `GroundedResponse` + **Provenance footer links**: Reproduce.py /
  Methods report.md / RO-Crate.json / Manifest JSON), `GroundedResponse` (inline hover-to-verify
  grounding + clickable rsID/RefSeq/Ensembl/ClinVar/PDB → source DB), `ApprovalCard`
  (review-mode plan approval), `ChatInput` (example-goal chips), `RunHistory` (history list,
  debounced search), `RunDetail` (read-only past-run view reusing TraceView+FinalCard).
- `src/api/agent.ts` (SSE consumer + optional `AbortSignal`), `src/api/traces.ts`
  (`listTraces`, `getTrace`). Types in `src/types/agent.ts`, `src/types/traces.ts`.
- `frontend/showcase.html` + `src/showcase.tsx`: standalone mock-data demo (the gh-pages site).
  Multi-page entry is wired in `vite.config.ts` (`rollupOptions.input`).

## 5. What to build next (concrete guidance)

### P1b — merge it first (already implemented on `feat/stop-recovery`)
PR + merge via `gh`, sync `main`. Then:

### P2a — Linked viewers + figure/data export (do next)
Two parts; can be split into sub-slices.
- **Linked selection:** clicking a guide row in `CrisprReportCard.tsx` highlights/centers that
  guide in the embedded `IgvGuideViewer.tsx`. The IGV track builder is `igvGuideTrack.ts`
  (off-target equivalent: `igvOfftargetTrack.ts`). Lift selection state to the card; pass a
  `selectedGuideId` down to the viewer; on change, call the igv.js browser API to navigate/locus.
- **Figure/data export:** add a small shared `downloadBlob(name, mime, data)` helper. Then:
  - SVG export (PNG/SVG) for the inline-SVG viz: `ReliabilityDiagram.tsx`, the histogram in
    `AccuracyReport.tsx` (serialize the `<svg>` → blob).
  - CSV export for tabular cards: `PrimerPairsCard.tsx`, `OnTargetScoreCard.tsx`, the guide
    table in `CrisprReportCard.tsx`.
  Geneious/IGV set the bar here — scientists need figures/data out for papers.
- Keep it on-token (accent links like the provenance footer). Add focused vitest per card.

### P2b — Edit the plan before approving (Review mode)
- Backend: `AgentApproveRequest` (in `api/agent.py`) currently has `approved` + `reason`. Add an
  optional `plan: dict | None`; in `agent_approve` / `_stream_agent_approve`, if a (validated)
  edited plan is supplied, persist it as `trace.awaiting_approval_plan` before calling
  `resume_agent` (which already takes a `Plan`). Re-validate with `Plan.model_validate`.
- Frontend: in `ApprovalCard.tsx`, make the plan steps editable (edit description / delete /
  reorder) before Approve; thread the edited plan through `App.tsx` `handleApproval` →
  `streamAgentApprove`.
- **Honesty note:** the executor is a free-form tool-use loop (the plan is *context*, "you may
  deviate"), so an edited plan changes guidance, not hard control. Represent this honestly in the
  UI copy (don't imply the executor is constrained to the edited steps).

### P3 — File/dataset upload + registry (BLOCKED — do not build yet)
Needs auth. The storage layer exists (`backend/src/bioforge/storage/adapter.py`: Protocol +
Local + MinIO, project-isolated) but is **unwired** ("until auth lands"). This is a real
prerequisite; building file upload before auth would be premature. Defer to the auth phase.

### P3 — Durable job model + queue (Celery) (phase-sized, last)
On the roadmap (Phase 1: Celery + Redis). A run becomes a persisted job; long tools (BLAST)
run async and stream status; pairs naturally with the run-history work already shipped. This is
infra-sized — design it as its own phase, not a quick slice.

## 6. The deep-dive synthesis (rationale, for context)
- **Benchling** → linked, browsable, searchable artifacts (drove P0). Avoid its "overwhelming"
  complexity.
- **LatchBio** → GUI↔code bridge + reproducibility (drove P1a; also the user's own principle).
- **DNAnexus** → reproducibility + scale/jobs (drives the P3 queue).
- **Geneious/IGV** → visualization craft + figure export (drives P2a).
- **Agentic-UX 2026** → planning visibility, tool-use disclosure, memory surfacing, multi-step
  tracking, **recovery routing**, "intervention as a core feature" (drove P1b; autonomy toggle
  already shipped).
- **FAIR/RO-Crate** → already on the right standard (provenance work shipped); P0 added the
  Findable piece (stable, browsable, shareable run identity).

The single highest-leverage move was P0 (runs first-class) — done. Next-highest open item is
P2a (make the science viz feel first-class and exportable).
