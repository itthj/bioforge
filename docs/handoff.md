# BioForge — Session handoff (v4 finalization, 2026-05-30)

## Update 2026-06-01 (session 3): on-target efficiency benchmark — slice 1 (DeepCRISPR × Chari-2015)

Built the first arm of the §13 on-target accuracy benchmark. Suite now **920 passed, 2 skipped,
16 deselected** (+15 new); ruff check + format clean. NOT yet committed — on the working tree only.

- **Decision made (user, this session): fetch-on-first-use** for the unlicensed crisporPaper effData.
  Implemented so it's **not a one-way door**: the loader is source-agnostic + sha256-verified, so a
  user-supplied `local_path` (option b) or an alternate mirror URL (option c) drop in with no code
  change, and a network fetch is still consent-gated (no silent fetch of unlicensed data).
- **New files:** `benchmarks/effdata.py` (loader: consent gate `BIOFORGE_CRISPOR_EFFDATA_CONSENT`,
  commit-pin + committed `expected_sha256`, `local_path` bypass, `.tab` parser) and
  `benchmarks/on_target_efficiency.py` (tie-aware **numpy** Spearman/Pearson — no scipy; typed
  `OnTargetEfficiencyResult` carrying honesty labels + the per-guide `(predicted, observed)` pairs
  that unblock calibration). Config: 3 `crispor_effdata_*` settings. `benchmarks/__init__.py` exports.
- **Provenance pinned (verified live this session):** crisporPaper commit
  `33a8225c7bc3be7f937786f6b151ffa7d7e29e84`, `chari2015Train.tab` sha256
  `6a6254a3966c53aa5eceb46cddf57e940466632ebee277d7b0450b662485e576` (1234 rows). The data is
  **never vendored** — fetched into `~/.bioforge/data/crispor_effdata/<commit>/` on first use.
- **Tests:** `test_on_target_efficiency.py` (15, hermetic: consent gate, fetch/verify/cache,
  local-path bypass, sha256 + row-count guards, tie-aware ranks, known Pearson=0.6, honesty labels,
  a guard that the registry never says `held_out`). Real run = `test_deepcrispr_chari2015_on_target_efficiency_e2e`
  in `test_models_docker_e2e.py` (`-m docker` + `-m online`, skips if image/network absent; asserts
  n=1234, leakage `unknown`, cross-dataset, ρ in [0.05, 0.25] bracketing the live 0.130).
- **Accuracy Report:** the on-target row flipped `not_yet_wired` → **`guard_only`** (NOT `live`:
  needs fetch + Docker, must not run on page load — same honest reasoning as ClinVar fidelity) and
  renamed off "held-out" → "cross-dataset guide-efficiency". Frontend renders it unchanged (data-driven).
- **STILL OPEN — the leakage gate (do before any `held_out`/`live` promotion):** the result labels
  `leakage_status="unknown"` because it's not verified whether Chari-2015 was in DeepCRISPR's
  (Chuai 2018) training data. Verify against the Chuai 2018 training-set description, then update
  `_LEAKAGE[("chari2015Train","deepcrispr")]` in `on_target_efficiency.py`. Until then it is honestly
  a cross-dataset correlation, never a held-out accuracy claim.
- **Next (in order):** (1) leakage verification gate above; (2) **slice 2 = RS2/Azimuth arm** — same
  rails, but needs the 30-mer (efetch 4-up/3-down flanks via the guide-name coords, e.g.
  `ABCC8_chr11_17483303`) AND an hg19-vs-hg38 build check before trusting the flanks; (3) off-target
  recall (GUIDE-seq sites, same repo + `verify_pam` CFD path); (4) GIAB variant-calling (still the
  one genuinely heavy item).

## Update 2026-05-30 (session 2): repro-determinism + Doench Rule Set 2

Five slices landed on `main` (each its own branch, FF-merged, suite green, ruff/tsc clean),
moving ~89% → ~92% of the v4 vision; **all pushed to `origin`**. Backend ~905 tests + 3 docker e2e;
frontend 85 vitest (`tsc --strict` clean).

1. **Repro-determinism guard** (`7d72a63`, rule 19 / §10): a cross-process / cross-`PYTHONHASHSEED`
   test that the run-manifest `content_hash` is byte-identical across re-runs, plus a named CI step.
   Closes remainder item #6 below. (Found + fixed a latent leak: `test_migrations` sets a sync
   `BIOFORGE_DB_URL` in `os.environ`; the determinism subprocess now pins its own in-memory async URL.)
2. **Doench RS2 license audit** (`cd9c3a1`): Azimuth is **BSD-3-Clause** (verified — the file is
   `LICENSE.txt`, not `/LICENSE`, which is why the prior pass marked it unverified). Cleared; weights
   are vendorable with attribution, no consent gate. See `docs/license_audit.md`.
3. **Doench RS2 (Azimuth) secondary on-target scorer** (`a0ea732`): `score_guide_on_target(model=
   "azimuth_rs2")`, out-of-process like DeepCRISPR/Lindel/FORECasT, **off by default**. Requires the
   real 30-nt `thirtymer` context (refuses to fabricate flanks; soundness-checks the protospacer
   offset). **VALIDATED end-to-end** (later same session): built `bioforge/azimuth:legacy` (Biomatters
   py3 port @ `dbd30b9`, scikit-learn 0.23.2), `V3_model_nopos.pickle` loads, deterministic
   (EMX1 30-mer → 0.4889), covered by `test_azimuth_real_image_end_to_end` (`-m docker`). Off by default.
4. **Full CFD off-target via verified PAM** (`99af6a1`): `find_offtargets(verify_pam=true)` fetches each
   clean hit's genomic flank (Entrez efetch), reads + verifies the PAM (plus/minus strand), and reports
   the FULL CFD in `cfd_full_score`. New `offtarget_pam.py` with a SOUNDNESS GATE — the off-target
   protospacer is reconstructed from the window and must match the BLAST subject, so a strand bug
   degrades to mismatch-only rather than a wrong PAM (§0). Off by default. 14 tests.
5. **Frontend on-target uncertainty** (`f46a9cf`): `OnTargetScoreCard` renders the rule-based + opt-in
   DeepCRISPR + RS2 scorers SIDE BY SIDE with rule-10/§6 framing (point estimates, not per-guide
   intervals; disagreement = signal). Renders only existing backend output; nothing fabricated. +4 vitest.

Everything below is the session-1 handoff, still accurate except where the above supersedes it
(remainder #3 Doench RS2 is done + validated; #6 repro-determinism is done; the Phase-2 off-target
PAM/full-CFD item is done; the "render existing on-target signals" half of the deeper-frontend item is done).

## RESUME HERE — benchmark data IS sourceable (investigated 2026-05-30, NOT yet built)

> **Superseded for the on-target arm by the 2026-06-01 (session 3) update at the top:** the decision
> is made (fetch-on-first-use) and slice 1 (DeepCRISPR × Chari-2015) is BUILT + green. The detail
> below remains the accurate record of the off-target arm and the RS2 30-mer flank approach.

Correction to the "Next-step priority" below: the on-target and off-target **benchmark arms are NOT
blocked on a big download** — only GIAB variant-calling is. Proven live this session (no code committed
yet — this section is the only record):

- **Source:** `https://raw.githubusercontent.com/maximilianh/crisporPaper/master/effData/<file>.tab`
  (same author as the CRISPOR repo the CFD matrices came from). `chari2015Train.tab` = 1234 rows,
  tab-separated `guide \t seq \t modFreq`, where `seq` is the **23-mer** (20-nt protospacer + 3-nt PAM,
  ends in NGG) and `modFreq` is measured efficiency. Other datasets present: doench2014-*, concordet2,
  chari2015Train293T/K562, alena*. The repo also has GUIDE-seq off-target sites (354 sites / 9 sgRNAs).
- **Proven live:** downloaded chari2015Train.tab in the venv (`urllib`, network OK), ran
  `bioforge/deepcrispr:legacy` over all 1234 via `predict_on_target`, computed **Spearman ρ = 0.130**
  (n=1234) vs `modFreq` with a tie-aware **numpy** rank (scipy is NOT installed — don't add it, use numpy).
- **LICENSE CATCH (rule 15):** `crisporPaper` has **no LICENSE file** (`api.github.com/.../license` → 404)
  → all-rights-reserved → **do NOT vendor** these files. Use **fetch-on-first-use** (download at runtime,
  record source URL + sha256 + provenance, never commit) — the inDelphi-weights pattern already in the repo.
  **OPEN DECISION (user owns this — product license posture):** (a) fetch-on-first-use [recommended],
  (b) require the user to supply the file, or (c) find a cleanly-licensed mirror. Do NOT build until chosen.
- **Before any ρ ships (rule 18 diligence):** (1) leakage — was Chari-2015 in DeepCRISPR's training? verify
  against Chuai 2018 before calling it "held-out". (2) ρ=0.13 is plausibly REAL — cross-dataset on-target
  correlation is known to be low (Haeussler 2016); label it cross-dataset, don't over-sell. (3) PAM
  convention looked right (seqs end AGG/TGG).
- **RS2 (Azimuth) benchmark** needs the **30-mer** (4+20+3+3); effData has only the 23-mer. The guide
  *names* carry genomic coords (e.g. `ABCC8_chr11_17483303`) → use the new `offtarget_pam.efetch_flank`
  to fetch 4-nt-up + 3-nt-down flanks (VERIFY the coords' genome build first — hg19 vs hg38).

**Exact next build (once the license decision is made):** `benchmarks/on_target_efficiency.py` — a
fetch-on-first-use loader (per-dataset URL + sha256 + provenance + leakage label), tie-aware numpy
Spearman/Pearson, run DeepCRISPR (+ RS2 via efetch flanks), return a typed result; unit tests (mock the
fetch + the model), a `-m docker` e2e for the real run; then wire into `benchmarks/accuracy_report.py` +
`AccuracyReport.tsx`. **This also produces the (prediction, observed) pairs that unblock calibration
(rule 11)** — so it knocks down §13-on-target AND sets up the calibration/reliability-diagram arm. The
off-target arm (GUIDE-seq sites, same repo) is the same pattern + the `verify_pam` CFD path. **GIAB
variant-calling remains the one genuinely heavy item** (a caller + GRCh38 ~3 GB + truth set) — not tried.

---

Pick up cold from here. Read `docs/grounding.md`, `docs/license_audit.md`, and the v4 blueprint
(the user has it; **it is NOT committed** — ask for it / re-paste it. Until then assess against
its in-repo footprint: `grounding.md` + the §-references in code).

## Repo state
- **GitHub:** https://github.com/itthj/bioforge — everything on **`main`**, pushed.
- **Local:** `C:\Users\james\OneDrive\Documents\BIOTECH 101\bioforge` (Windows; Docker Desktop + WSL2).
- **HEAD = the `feat(benchmarks): … ClinVar live fidelity + handoff` commit** (run `git log --oneline -14`). Working tree clean. (Session 2 advanced HEAD to `f46a9cf` — all session-2 commits pushed to `origin`; see the Update at the top.)
- **Suite:** ~905 passed, 2 skipped, ~15 deselected (online+nextflow+docker; +1 azimuth docker e2e). Lint + format clean. Frontend 85 vitest, `tsc --strict` clean.
- **Stale prior-session branches** still exist (local+origin): `chore/license-audit`, `feat/clinvar-fidelity-benchmark`, `feat/grounding-validator`, `feat/registry-metadata` — safe to delete.

## What this session built (all on `main`, oldest→newest; ~74%→~89% of the v4 vision)
A full conformance audit against the v4 blueprint, then 9 vertical slices (each its own branch, FF-merged, tested green, ruff/tsc clean):
1. **Lindel + FORECasT validated end-to-end** — out-of-process Docker models, parity-checked. Images built locally: `bioforge/lindel:legacy` (pinned `fdcad58`, **editable install** so weights resolve; `rev_index=prereq[1]`), `bioforge/forecast:legacy` (thin FROM `selftarget`, cwd=predictor dir for theta, drops the `-` null). Opt-in, off by default.
2. **Accuracy Report** (§13/§5) — `GET /benchmarks/accuracy` + a React "Accuracy" tab. Publishes the **real** L6 validator metrics + registry `published_accuracy` + an honest ledger (live/guard_only/not_yet_wired). `benchmarks/accuracy_report.py`, `api/benchmarks.py`.
3. **Digest-pinning** (rule 19) — all external images `@sha256`, `:latest` forbidden; guard test `test_digest_pinning.py`.
4. **CFD off-target** (Phase 2, rule 16) — Doench-2016 matrix **sourced verbatim** from CRISPOR (`data/cfd_doench2016.json`, sha256+provenance, NEVER memory) + parity-tested engine in `offtarget_scoring.py`. `find_offtargets` reports `cfd_mismatch_score` (PAM factor omitted — off-target PAM unverified; full CFD awaits the PAM-verification slice).
5. **OOD pre-gate** (§0/§4.1, rule 12) — `BIOFORGE_OOD_GATE=block` refuses out-of-envelope inputs before a tool runs (`ood_refusal` in `grounding/ood.py`, wired in `loop.py`). Off by default.
6. **PolyPhen HumVar naming** (rule 16) — `VariantConsequence.polyphen_model="HumVar"`; verified Ensembl VEP default + thresholds against the Ensembl protein-function docs.
7. **Frontend trust-surfacing** (§5) — the `validation` step (grounding/OOD/uncertainty) now renders in the trace via `StepCard` (was silently dropped).
8. **Close-the-loop soundness gate** (§0/§4.1) — `BIOFORGE_SOUNDNESS_GATE=block` rejects impossible tool outputs before they feed downstream (`soundness_refusal`). Off by default.
9. **RO-Crate 1.1 JSON-LD export** (§10) — `to_ro_crate` / `export_ro_crate` in `provenance/research_object.py`.
10. **§13 ClinVar fidelity wired to LIVE ClinVar** (latest slice) — `test_clinvar_fidelity_online.py` (`-m online`, nightly): gold from an independent NCBI esummary read vs `lookup_clinvar`, scored via the §13 harness, ≥2★ subset data-driven. **Verified green against real ClinVar.** Accuracy Report ledger updated to say so.

## Decisions made (do NOT re-litigate)
- **The honestly-gated remainder must NOT be faked.** Fabricating gold-sets / coefficients / calibration would destroy the platform's whole reason to exist. Source-or-stub, never a confident wrong number. This is the line.
- New gates (`ood_gate`, `soundness_gate`) are **opt-in, default "off"** → behavioral equivalence; the post-response detector still records flags. Interactive "proceed-with-OOD-flag" HITL (§4.3) + L5 iterative rewrite re-validation remain deferred (enforce uses in-place redaction, which sidesteps the rewrite trap).
- CFD wired as the **mismatch component only** until off-target PAM verification exists.
- DeepSpCas9 stays dropped (license); DeepCRISPR is the on-target deep primary (opt-in).

## Completion ≈ 89% of the v4 vision
Scorecard rules now ✅: 1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 14, 15, 16, 17, 19, 20. §5 Accuracy page + trust-trace ✅; §10 RO-Crate + digest-pin ✅; §13 has 3 live benchmarks (numeric L3, identifier L3+, ClinVar fidelity).

## The honestly-gated remainder (~11%) — each needs real external data, NOT a sprint
1. **§13 GIAB (variant calling)** — gated on a variant-CALLING path that doesn't exist (tools are annotation-only) + a huge truth-set/reference download. Build variant calling first, then wire GIAB.
2. **§13 GUIDE-seq/CIRCLE-seq off-target recall** — needs validated off-target sites + full-genome off-target search + CFD-with-PAM (PAM verification slice).
3. **Doench Rule Set 2** (two-scorer secondary, rest of #5) — a full trained model. Do it the **DeepCRISPR way**: published weights/image, out-of-process, validated — NOT a reimplement (banned ML-training code). License-audit first.
4. **Calibration + reliability diagrams** (rule 11) — needs real (prediction, observed-outcome) pairs, which only exist AFTER §13 gold-sets produce scored predictions. Downstream of #1/#2 by construction.
5. **Deeper frontend** — reliability diagrams + per-ML-prediction uncertainty on result cards (downstream of #4).
6. **Repro-determinism CI test** (§10/rule 19) — a CI test asserting `build_run_manifest` content_hash is byte-stable for a fixed run; wire into `.github/workflows/ci.yml`. **Small, buildable now — good first pick next session.**

## Next-step priority (recommended)
Session 2 finished everything that was buildable WITHOUT new external data:
- ~~Repro-determinism test + CI wiring~~ — **DONE** (`7d72a63`).
- ~~Doench RS2 (scaffold + validate end-to-end)~~ — **DONE** (`a0ea732`, `c77cdc2`).
- ~~CFD off-target PAM verification~~ — **DONE** (`99af6a1`): `verify_pam`, soundness-gated, off by default.
- ~~Frontend "render existing on-target signals"~~ — **DONE** (`f46a9cf`): `OnTargetScoreCard`.

**Every remaining item needs REAL external data (or is downstream of it) — none may be faked (§0/rule 18):**
1. **Variant-calling path** → unlocks §13 GIAB. Needs a caller (bcftools/DeepVariant, digest-pinned) +
   GRCh38 (~3 GB) + a GIAB truth set (HG002 VCF + high-conf BED) + hap.py/vcfeval. The biggest rock,
   ~half the remaining work, license-audit first.
2. **GUIDE-seq/CIRCLE-seq off-target recall** — needs published validated off-target site tables + a
   genome-wide search (e.g. Cas-OFFinder) on GRCh38 (shared with #1). Reuses the `verify_pam` CFD path.
3. **Calibration + reliability diagrams** (rule 11) — needs (prediction, observed-outcome) pairs that
   only exist once #1/#2 produce scored predictions; the reliability-diagram frontend follows it.

## Commands (Windows; venv at `bioforge\.venv`)
```
cd "/c/Users/james/OneDrive/Documents/BIOTECH 101/bioforge"
.venv/Scripts/python.exe -m pytest backend/tests/ -q            # ~873 passed
.venv/Scripts/python.exe -m pytest backend/tests/ -m online -q  # live-API suite (nightly)
.venv/Scripts/python.exe -m ruff check backend/ ; .venv/Scripts/python.exe -m ruff format --check backend/
```
Frontend (node is NOT on PATH — prepend it; PowerShell):
```
$env:PATH = "C:\Users\james\AppData\Local\Programs\nodejs;" + $env:PATH
Set-Location "C:\Users\james\OneDrive\Documents\BIOTECH 101\bioforge\frontend"
npm run typecheck ; npm test   # tsc --strict ; 81 vitest
```

## Environment gotchas
- **Docker works** (Desktop 29.4.3/WSL2). Legacy model images built locally: `bioforge/deepcrispr:legacy`, `bioforge/lindel:legacy`, `bioforge/forecast:legacy`. Enable via per-model `BIOFORGE_*_ENABLED` + image env vars.
- **node/npm** at `C:\Users\james\AppData\Local\Programs\nodejs` — NOT on the bash/PowerShell PATH; prepend it.
- **Network reachable** from the venv (NCBI eutils, raw.githubusercontent) — that's how the live ClinVar test + CFD sourcing worked.
- Bash-tool cwd flips between repo root and `bioforge`; normalize with an absolute `cd` or `git -C`. CRLF warnings on `git add` are harmless. Commit messages: keep ASCII ("section 13" not the symbol, "->" not arrows).
- ruff: `E501` ignored; `**/tools/sequence/models/*/legacy` excluded. `ruff check --fix` fixes import-order (I001).
- The Bash-tool **safety classifier occasionally goes down** ("temporarily unavailable") — read-only tools still work; retry, or use WebFetch/Read.

## Hard rules still in force
Plan before coding · vertical slices · no heavy agent frameworks · real biology in tests · provenance from day one · typed everything · never silently truncate · **AI never fabricates biology** · **no unsourced constants** (cite or `# VERIFY:`) · **no license claims from memory** · **no ML training code** (published weights only) · **faithful, never remap upstream** · behavioral equivalence is the gate · **the gated ~11% must be earned with real data, never faked — the integrity IS the product.**
