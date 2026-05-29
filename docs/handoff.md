# BioForge — Session handoff (grounding-hardening session, 2026-05-29)

Pick up cold from here. Read `docs/grounding.md` and `docs/license_audit.md` next.

## Repo state
- **GitHub:** https://github.com/itthj/bioforge — **everything is on `main`** (fast-forwarded).
- **Local:** `C:\Users\james\OneDrive\Documents\BIOTECH 101\bioforge` (Windows; WSL Ubuntu mirror under `/mnt/c/...`).
- **Suite:** `770 passed, 2 skipped, 11 deselected`. Lint + format clean.
- Feature branches (all already merged into `main`): `feat/grounding-validator`, `chore/license-audit`, `feat/registry-metadata`, `feat/clinvar-fidelity-benchmark`. Safe to delete on origin.

## What this session built
Scope was set by the user: **harden the existing (v2) platform for accuracy / anti-hallucination toward the v4 blueprint — NOT a rebuild.** Capability-expansion groups were parked unless explicitly unblocked.

1. **Grounding stack (v4 §4)** in `backend/src/bioforge/agent/grounding/`:
   - **L0** — hardened `prompts/system.md` + `prompts/critic.md` (all claim kinds trace; findings≠background; caveats-first; never fabricate uncertainty). Scoped to current tools only (no DeepSpCas9/OOD clauses that reference unbuilt things).
   - **L3** `numeric.py` — deterministic numeric grounding; conservative extractor (Cas9/BRCA1/5'/SARS-CoV-2 not treated as quantities); percent/rounding aware.
   - **L3+** `entities.py` — deterministic structured-identifier grounding (rsID/RefSeq/Ensembl/ClinVar/PDB), echo-safe vs the user goal, inline-redactable.
   - **L4** `judge.py` (+ `prompts/grounding_judge.md`) — Opus entity/mechanistic judge, may only cite tool fields. **Opt-in** (`BIOFORGE_GROUNDING_JUDGE_ENABLED`).
   - **L6** `metrics.py` (+ `corpus/numeric_l3.json`) — validate-the-validator; `evaluate_corpus()` reports precision/recall for numeric **and** identifier layers. Release gate: 1.0 / 1.0.
   - **L7** `soundness.py` — deterministic range/sanity detector (GC∈[0,100], scores∈[0,1], pLDDT∈[0,100], e-value≥0).
   - `render.py` — `summarize_grounding()` scientist-facing trust line.
   - Wired in `agent/loop.py` via `_apply_grounding` (+ `_enforce`); emits a `validation` trace step (report rides in `AgentStep.verdict`).
2. **Modes & defaults** (`config.py`): `BIOFORGE_GROUNDING_ENABLED=true`, `BIOFORGE_GROUNDING_MODE=annotate` (default). `shadow`=record only, `annotate`=visible summary (removes nothing), `enforce`=redact unsupported numeric/identifier inline + audit footer, judged entity/mechanistic flagged in footer. **Free deterministic layers run by default; the Opus judge is opt-in.**
3. **§4.2 registry metadata + §6 honesty helper** (`tools/base.py`, `registry.py`): optional `model_versions / emits_instance_uncertainty / published_accuracy / training_distribution / reference_data_keys` on `ToolSpec` (defaults preserve all 28 tools). `uncertainty_note()` = the honesty rule as code (report emitted uncertainty → else sourced published accuracy → else explicit point-estimate; never fabricate). Populated on `score_guide_on_target` + `design_guides` as exemplars.
4. **Verified license audit** (`docs/license_audit.md`) — done with the web tools, against upstream LICENSE files.
5. **§13 ClinVar fidelity harness** (`benchmarks/clinvar_fidelity.py`) — `score_clinvar_fidelity()`, the no-remap / star-preservation guard as a gating metric.

## Decisions made (do NOT re-litigate)
- **Grounding ON by default in `annotate` mode** — a scientific instrument should prove itself on every answer (free, deterministic, precision 1.0). Judge stays opt-in (Opus cost).
- **On-target primary = DeepCRISPR (Apache-2.0)** — user chose "option 2": swap the primary off DeepSpCas9 (CC-BY-NC + its code repo has *no* license → all-rights-reserved). DeepSpCas9 → optional, non-commercial gated. **inDelphi** non-commercial posture **confirmed** (keep the consent gate). **Lindel + FORECasT = MIT** (clear to integrate/redistribute). Full table in `docs/license_audit.md`.
- **Enforcement = visible redaction + audit note** (never a silent rewrite; never a vague qualitative substitution).
- **Post-hoc grounding** (matches v4 §4 text). **Structured-claim emission is deferred** — it's the validator's true recall ceiling (you can't ground a claim never extracted); flagged in `docs/grounding.md`.

## Completion: ~62% of the full v4 vision
(base platform ~95% · grounding §4 ~85% · uncertainty/§6 ~25% · provenance/§10 ~25% · benchmarks/§13 ~20% · Phase-2 ML ~15% · frontend additions ~40%). The anti-hallucination core is largely done; the accuracy-proving and ML-capability layers are the frontier.

## Next steps (priority order)
1. **Phase-2 ML integration.** Primary on-target = **DeepCRISPR** (`bm2-lab/DeepCRISPR`, Apache-2.0). It's **TensorFlow 1.x** → integrate **out-of-process via a pinned legacy-TF env**, mirroring the existing `tools/sequence/models/indelphi/` fetch-on-first-use pattern **minus the consent gate** (Apache-2.0 is clean). **Use existing weights — do NOT retrain** (project rule). Needs a TF1-capable environment to validate (can't be tested in the current `.venv`). Also: add **CFD** off-target (`offtarget_scoring.py` currently ships MIT/Hsu-2013 as primary with **no CFD** — load the Doench 2016 CFD matrix from a committed data file); add **FORECasT + Lindel** edit-outcome models (both MIT).
2. **§13 real gold-sets.** Wire `score_clinvar_fidelity()` to live `annotate_variant`/`lookup_clinvar` output against a real ≥2★ ClinVar subset; add GIAB (variant calling) + GUIDE-seq (off-target) sets; build the in-product **Accuracy Report** page.
3. **§6 OOD gate + calibration.** Metadata rails now exist — add `check_ood()` (input vs `training_distribution`) wired into the loop, plus calibration/reliability diagrams.
4. **§10 reproducibility research-object** — hashed I/O, reference-build pin+checksum, digest-pinned containers, recorded seeds, RO-Crate-style lineage manifest export, a repro CI test.
5. **v4 frontend** — surface grounding / uncertainty / accuracy report in the React UI (validation step is already in the trace).
6. **Mop-up:** populate §4.2 metadata across the remaining ~26 tools; extend L6 to measure the **L4 judge** precision/recall against a labeled corpus; pull **DeepCRISPR's published Spearman** (still a `VERIFY:` — Springer's auth wall blocked it; try the PMC mirror / supplementary).

## Commands (Windows PowerShell; venv at `bioforge\.venv`)
```
bioforge\.venv\Scripts\python.exe -m pytest bioforge\backend\tests\ -q
bioforge\.venv\Scripts\python.exe -m ruff check bioforge\backend\
bioforge\.venv\Scripts\python.exe -m ruff format --check bioforge\backend\
```
Enable grounding judge / enforce in tests via `monkeypatch.setattr(settings, "grounding_*", ...)`.

## Environment gotchas (learned the hard way this session)
- **PowerShell here-string commits:** keep commit messages **ASCII — no `"` double-quotes and no backticks**; they break `@'...'@` parsing and git reads the message as pathspecs. (Use `--` for em-dashes, words for symbols.)
- **CRLF warnings** on `git add` are harmless (autocrlf).
- **ruff** trips `I001` on freshly-written test files (blank line after docstring) → run `ruff check --fix <file>` after creating one.
- **git push from Windows** works (creds cached); set `$env:GIT_TERMINAL_PROMPT=0` to fail fast instead of hang. `gh` CLI is **WSL-only**.
- **Web tools:** `WebSearch`, `WebFetch` (fails on auth-redirect/paywall — Springer bounced repeatedly; use PMC/BMC mirrors), and the academic search `mcp__4a27a32c-...__search` (**no `max_results` arg** — query + optional filters only). These unblock license/accuracy sourcing — use them rather than asserting facts from memory.
- **Grounding default-on** changed 3 general tests (a `validation` step now trails `final`): `test_agent_run.py`, `test_streaming.py` (expect last step `validation`), and `test_no_validation_step_when_disabled` (now monkeypatches grounding off). Any new full-loop test will see a trailing `validation` step.

## Hard rules still in force
Plan before coding · vertical slices · no heavy agent frameworks · real biology in tests (lambda/BRCA1/HBB/CFTR/SARS-CoV-2) · provenance from day one · typed everything · never silently truncate · **AI never fabricates biology** · **no unsourced scientific constants** (cite or `# VERIFY:`) · **no license claims from memory** (verify upstream) · **no ML training code** (use existing weights) · behavioral equivalence is the gate for refactors.
