# BioForge — Session handoff (Phase-2 ML + accuracy-hardening session, 2026-05-29)

Pick up cold from here. Read `docs/grounding.md` and `docs/license_audit.md` next (both
updated this session). This continues the odyssey from the prior grounding-hardening handoff.

## Repo state
- **GitHub:** https://github.com/itthj/bioforge — **everything is on `main`** (fast-forwarded).
- **Local:** `C:\Users\james\OneDrive\Documents\BIOTECH 101\bioforge` (Windows; Docker Desktop + WSL2 available).
- **`origin/main` HEAD: `64d20c4`.** Working tree clean.
- **Suite:** `846 passed, 2 skipped, 11 deselected`. Lint + format clean.
- This session's feature branches were all FF-merged into `main` and **deleted**. The prior
  session's branches (`chore/license-audit`, `feat/clinvar-fidelity-benchmark`,
  `feat/grounding-validator`, `feat/registry-metadata`) still exist (local + origin) — safe to delete.

## What this session built (8 commits on `main`, oldest -> newest)
1. **`2aabdfc` section 4.2 metadata mop-up + DeepCRISPR Spearman sourced.** Populated `reference_data_keys`
   on the DB-backed tools (ncbi_blast/clinvar/dbsnp, gnomad, ensembl_vep, ensembl_variant_recoder,
   rcsb_pdb, alphafold_db, interpro, sifts) + model metadata on `find_offtargets` (Hsu-2013 MIT) and
   `edit_outcome`. Resolved the DeepCRISPR Spearman `VERIFY:` in `license_audit.md` via the PMC mirror
   (on-target ROC-AUC **0.857**; exact regression rho is in Additional file 3). Pure transforms stay empty
   (honest). Rule: a tool carries metadata iff it owns a model/heuristic OR depends on a reference dataset.
2. **`527ba7d` DeepCRISPR on-target scaffold** (later superseded by the validated version, #8).
3. **`2bf6e55` section 6 OOD gate** (`agent/grounding/ood.py`): deterministic `check_ood(tool_calls)` flags
   inputs outside a model's stated envelope (e.g. non-20-nt guide vs `find_offtargets`' Hsu weights);
   `collect_model_uncertainty(tool_names)` surfaces each ran model's `uncertainty_note`. Both ride the
   `validation` verdict (`ood`, `model_uncertainty`); advisory appended in annotate/enforce, silent in
   shadow. Detector/recorder only (acting-on-flag deferred, like L7).
4. **`f7a4d66` section 13 ClinVar fidelity adapter** (`benchmarks/clinvar_fidelity.py`):
   `review_status_to_stars` (NCBI scale, verified) + `case_from_clinvar_record` to turn a live
   `lookup_clinvar` record + caller-supplied gold into a fidelity case. Live >=2-star gold-set still
   deferred (network; never hardcode ClinVar truth from memory).
5. **`212d795` section 10 reproducibility research-object** (`provenance/research_object.py`):
   `build_run_manifest(result)` -> content-hashed RO-Crate-inspired lineage (hashed tool I/O,
   reference-build pins, NON-SECRET settings fingerprint). `export_research_object`. Pure read, never
   auto-invoked. Digest-pinned containers / JSON-LD / repro CI deferred.
6. **`04f3e55` Lindel edit-outcome model** (scaffold) -- `edit_outcome(model="lindel")`.
7. **`2d8807c` FORECasT edit-outcome model** (scaffold) -- `edit_outcome(model="forecast")`.
8. **`64d20c4` DeepCRISPR VALIDATED end-to-end** + rewired to the authors' official image.

### Phase-2 ML status (the headline)
All three ML models are **opt-in, off by default, graceful when absent** (behavioral equivalence),
**faithful** (raw distributions surfaced verbatim, never remapped), out-of-process, with section 4.2
metadata + section 10 provenance pins. Each wrapper routes library chatter to stderr so **only JSON**
hits stdout.
- **DeepCRISPR** (on-target, Apache-2.0) -- **VALIDATED end-to-end** through the real bioforge path
  (`predict_on_target` -> runner -> live Docker -> wrapper -> scores). Recipe: thin image
  **`bioforge/deepcrispr:legacy`** (already built locally) FROM `michaelchuai/deepcrispr:latest`
  (py3.6.5, **TF 1.13.2**; base digest `sha256:812deef9...`); weights bundled at
  `/root/DeepCRISPR/trained_models/ontar_cnn_reg_seq`. Encoding uses DeepCRISPR's **own** `Sgt`
  (1-col `.rsgt`, `with_y=False`) -> `[N,4,1,23]` -> `DCModelOntar(is_reg=True, seq_feature_only=True)`.
  Validated scores: `ACGTTAGCAGTTTGATGGCATGG,ACCTCCAATCGGCCCACGGCTGG,CATTGACAGGATAGTGGCCAGGG`
  -> `[0.0307, 0.1461, 0.191]`. Output is a regression head (~`-0.01..0.19`), **not** strict `[0,1]`.
  **To enable:** `BIOFORGE_DEEPCRISPR_ENABLED=true`, `BIOFORGE_DEEPCRISPR_DOCKER_IMAGE=bioforge/deepcrispr:legacy`.
- **Lindel** (edit-outcome, MIT) -- scaffolded, **NOT yet env-validated**. numpy/scipy + bundled weights;
  API `gen_prediction(seq60, weights, prereq) -> (y_hat array, fs)`; 60 bp window (PAM `NGG` near pos 33);
  weights `Model_weights.pkl` + `model_prereq.pkl` in the Lindel package. VERIFY at validation: the
  weight/prereq filenames + how `rev_index` is stored in `model_prereq.pkl` (the wrapper guesses; it
  fails loudly if wrong). `edit_outcome` builds the 60 bp window via `_lindel_window` (hard-validated).
- **FORECasT** (edit-outcome, MIT) -- scaffolded, **NOT yet env-validated**. Py3 + C++ `indelmap`; official
  image **`quay.io/felicityallen/selftarget`**; CLI `python FORECasT.py <seq> <pam_index> <prefix>` -> 2 TSVs.
  VERIFY at validation: the `FORECasT.py` path in the image (set `FORECAST_SCRIPT`) + the profile-TSV
  column layout the wrapper parses. `edit_outcome` passes the PAM index `_locate_guide` already found.

## Decisions made (do NOT re-litigate)
- **All external ML models run out-of-process** over a tiny JSON stdin/stdout protocol. Each model package:
  `manifest`/`schema`/`runner`/`inference`/`__init__` + a `legacy/` dir (wrapper + Dockerfile + README)
  that is **ruff-excluded** (`**/tools/sequence/models/*/legacy`).
- **Use the authors' official images** where they exist (DeepCRISPR `michaelchuai/deepcrispr:latest`,
  FORECasT `quay.io/felicityallen/selftarget`) via a **thin image** (FROM official + bake the wrapper),
  NOT a from-scratch TF1 build (the README pins are loose/fragile; the official image is the source of truth).
- **Faithful, never remap:** ML wrappers emit the upstream label->frequency / score **verbatim**; the
  edit-outcome models populate `lindel_distribution` / `forecast_distribution` (raw) and leave `outcomes=[]`.
- **Wrapper stdout guard:** `_REAL_STDOUT = sys.stdout; sys.stdout = sys.stderr; _emit()` -- library prints
  must never corrupt the JSON protocol. (A real bug found + fixed in all three wrappers.)
- **No self-computed accuracy numbers from unknown-split data.** DeepCRISPR accuracy stays anchored to the
  paper's sourced figures (ROC-AUC 0.857). The bundled example set (10 guides) is too small/narrow.
- **Section 6 OOD / section 10 research-object are detectors/recorders;** acting-on-flag (replan) +
  calibration + digest-pinned containers are deeper changes, deliberately deferred.
- **Grounding stays ON by default in `annotate` mode** (carried over). DeepCRISPR/Lindel/FORECasT default OFF.

## Completion: ~72% of the full v4 vision
(base ~95% · grounding section 4 ~88% · section 6 uncertainty/OOD ~55% · section 10 provenance ~45% ·
section 13 benchmarks ~35% · Phase-2 ML ~70% [DeepCRISPR validated; Lindel/FORECasT scaffolded; CFD pending]
· frontend ~40%).

## Next steps (priority order)
1. **Validate Lindel + FORECasT end-to-end** (same playbook as DeepCRISPR #8): build/pull the env, confirm
   the wrappers' `VERIFY` items, run `predict_lindel` / `predict_forecast` through the real runner, then a
   parity check. FORECasT: `docker pull quay.io/felicityallen/selftarget`, build the thin `Dockerfile`,
   confirm `FORECasT.py` path + TSV columns. Lindel: build its `Dockerfile` (numpy/scipy + `setup.py install`),
   confirm the `rev_index` decoding in `lindel_infer.py`.
2. **DeepCRISPR provenance polish:** `model_version` currently reads `...@master`; pin
   `BIOFORGE_DEEPCRISPR_UPSTREAM_COMMIT` (or repoint the section 10 pin) to the image digest.
3. **Section 13 real gold-sets:** wire `case_from_clinvar_record` against a real >=2-star ClinVar subset
   (online test) + GIAB/GUIDE-seq; build the in-product **Accuracy Report** page.
4. **CFD off-target** (`offtarget_scoring.py` ships MIT/Hsu only): add the Doench-2016 CFD matrix from a
   **trustworthy committed data file** (320 values -- do NOT transcribe from memory).
5. **Section 6 calibration/reliability diagrams** + wiring `check_ood` into the loop (replan on OOD).
6. **Section 10 deeper:** digest-pinned containers, JSON-LD RO-Crate export, a repro CI job.
7. **v4 frontend:** surface grounding / OOD / uncertainty / accuracy / model distributions in React.

## Commands (Windows; venv at `bioforge\.venv`)
Run git-bash from the repo root OR `bioforge`. The Bash-tool cwd **flips** between the two -- normalize with
an absolute `cd` or `git -C`:
```
cd "/c/Users/james/OneDrive/Documents/BIOTECH 101/bioforge"
.venv/Scripts/python.exe -m pytest backend/tests/ -q
.venv/Scripts/python.exe -m ruff check backend/
.venv/Scripts/python.exe -m ruff format --check backend/
```
DeepCRISPR (validated): the image `bioforge/deepcrispr:legacy` is built; enable via the two env vars above.
End-to-end smoke (real Docker): `predict_on_target(guides, settings=settings.model_copy(update={enabled,image,...}))`.

## Environment gotchas (learned this session)
- **Docker works from the Bash tool** (Docker Desktop 29.4.3 / WSL2). You can build + run images directly.
- **git-bash MSYS path mangling:** a `/opt/...` arg typed on the git-bash command line becomes
  `C:/Program Files/Git/opt/...`. Use the image `CMD`, or `MSYS_NO_PATHCONV=1`. This does **not** affect
  the runner (Windows-python `subprocess` -> docker passes args literally; works on the target WSL2/Linux too).
- **In-image scripting:** `docker run --rm -i --entrypoint bash <img> -lc 'cd ...; python - ' <<'PY' ... PY`
  feeds a script via stdin (avoids mount/space issues; the repo path has spaces + OneDrive).
- **Bind-mounting the repo into a container is flaky** on this host (spaces + OneDrive) -- prefer baking the
  wrapper into a thin image.
- **Commit messages via bash heredoc** `git commit -F - <<'EOF' ... EOF` work; keep them ASCII (write
  "section 6" not the section symbol, "->" not arrows) to be safe.
- **ruff:** `E501` is ignored (long citation/desc strings OK). The model `legacy/` dirs are ruff-excluded.
- **CRLF warnings** on `git add` are harmless (autocrlf).
- **`settings.model_copy(update={...})`** is the clean way to make per-test/per-call Settings (alias-free).

## Hard rules still in force
Plan before coding · vertical slices · no heavy agent frameworks · real biology in tests
(lambda/BRCA1/HBB/CFTR/EMX1/SARS-CoV-2) · provenance from day one · typed everything · never silently
truncate · **AI never fabricates biology** · **no unsourced scientific constants** (cite or `# VERIFY:`) ·
**no license claims from memory** · **no ML training code** (use published weights only) · **faithful, never
remap upstream** · **validation-gated legacy wrappers are marked `VERIFY:` and fail loudly, never silently** ·
behavioral equivalence is the gate.
