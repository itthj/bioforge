# BioForge — Session handoff (v4 finalization)

## ★ START HERE — session 5 entry point (written 2026-06-02, end of session 4)

**Repo:** https://github.com/itthj/bioforge -- **main @ `de1efa1`**, working tree clean, everything pushed.
**Local:** `C:\Users\james\OneDrive\Documents\BIOTECH 101\bioforge` (Windows; Docker Desktop + WSL2).
**Suite -- all green:** backend **980 passed**, 2 skipped, 16 deselected (online+docker+nextflow gated);
frontend **116 vitest**; `tsc --strict` + `ruff check` + `ruff format` all clean.

### What session 4 shipped (2 slices, both FF-merged to main + pushed)
The IGV.js genome browser -- the handoff's #1 remaining buildable feature -- is now DONE on both arms.

1. `896c131` **IGV.js guide viewer (Slice A)** -- renders each candidate guide's protospacer + PAM
   (+ cut site when the edit outcome was simulated) on the **submitted target sequence as its own
   igv.js reference** (inline non-indexed FASTA, no hosted genome). Backend: `crispr_edit_report`
   now echoes `target_sequence` (the caller's own input) + a test locking the coordinate contract
   (+ guides index directly into the sequence, - guides via reverse-complement). Frontend: `igv`
   added as an installed optionalDependency (MolstarViewer pattern -- lazy import, install-hint
   fallback; zero new npm advisories); pure tested adapter `igvGuideTrack.ts`; lazy `IgvGuideViewer.tsx`;
   mounted in `CrisprReportCard`. Honesty: the submitted locus is its OWN coordinate system -- no
   genome build, nothing it can misplace on a chromosome.
2. `de1efa1` **hg38 genomic off-target view (Slice B)** -- renders off-target hits on the hosted
   GRCh38 (hg38) genome, but ONLY hits that provably sit on a GRCh38 primary chromosome. Backend:
   `data/grch38_chromosome_accessions.json` (chromosome RefSeq -> UCSC-name map, 24 chr + MT,
   **derived live from the NCBI GRCh38.p14 assembly report**, committed with provenance + sha256
   `64318ddf...`; no constants from memory). `genomic_placement.py`: `resolve_genomic_placement`
   places only version-matched GRCh38 chromosomes (`NC_000001.11`, NOT GRCh37 `.10`), normalizes
   1-based BLAST coords incl. minus-strand order to 0-based half-open, refuses degenerate coords;
   everything else (gene/transcript records, scaffolds, wrong build, non-human) -> None. New
   `genomic_placement` field on `OfftargetHit` + honest "N of M placed" caveat. UCSC names match
   igv hg38 contigs, so a placed hit lands correctly with no remapping. Frontend: typed
   `OfftargetHit`/`GenomicPlacement` + coercion; pure `igvOfftargetTrack.ts`; `IgvOfftargetViewer.tsx`
   (lazy igv on `genome:"hg38"` for placeable hits + a table listing non-placeable hits by accession,
   never a locus); mounted on the recommended guide's off-target section.

### The data-plumbing reality (verified this session -- carry forward)
- `design_guides` coordinates are SEQUENCE-RELATIVE only (0-based half-open on the forward strand of
  the submitted input) -- no chromosome, no build. Hence Slice A renders the input as its own reference.
- `find_offtargets` hits carry `accession` + `subject_start/end` on whatever BLAST subject matched --
  which may be a GRCh38 chromosome, a gene/transcript record, a scaffold, a different build, or a
  non-human subject. Only GRCh38 chromosome RefSeqs are hg38-placeable -- hence the Slice B gate.

### RESUME HERE -- next-step priority (session 5), in order
IGV (old #1) is DONE. Remaining work, by KIND (this matters -- some is data-gated, some needs your call):
1. **GIAB variant-concordance end-to-end** -- the big rock, multi-hour. Scorer built+tested; goes
   live the moment a caller feeds it. Needs: (a) YOUR DECISION + license-audit on a variant CALLER
   (bcftools = lighter/license-clean vs DeepVariant = heavier/more accurate), (b) digest-pin it,
   (c) GRCh38 ~3 GB + index, (d) GIAB HG002 truth VCF + high-conf BED, (e) build the missing
   variant-CALLING path (variant tools are annotation-only today), (f) publish a real number ->
   flip GIAB ledger `not_yet_wired -> live/guard_only`.
2. **Edit-outcome live number** -- medium. TVD/JSD scorer built; needs a license-clean held-out
   indel-distribution dataset (Lindel/inDelphi/FORECasT) -> publish via `published.py` -> flip that
   row `guard_only -> published`. (On-target rho=0.1299 and off-target rho=0.3132 already published.)
3. **DeepSpCas9 sign-off** -- NOT a code gap. The blueprint names it primary; dropped on license
   (CC-BY-NC/unlicensed), DeepCRISPR (Apache-2.0) substituted. Needs YOUR call: accept the deviation
   + document in `docs/license_audit.md`, or re-investigate a license path. Do not silently "fix".
4. **MSA viewer (Phase 4)** -- minor, buildable now, no external data. Same pattern as the IGV slices
   (optional dep + lazy viewer + pure adapter). `react-msa-viewer` not integrated.
5. **GRCh37-specific refusal message** -- tiny polish. GRCh37 off-target accessions currently get the
   generic "not a GRCh38 chromosome" message (honest + safe, just not specific). Source the GRCh37
   chromosome accessions to say "looks like the wrong build".
6. **Agent/grounding depth (optional)** -- execution-time replan on L7 violation; L5 iterative-rewrite
   re-validation; structured-claim emission (the validator's recall ceiling); proceed-with-OOD-flag HITL.
7. **Presentation/productionization** -- see `docs/DEMO.md` (the real-vs-gated walkthrough, written this
   session); finalize the v4 conformance scorecard; live demo screenshots; CI for gated suites; deploy.

### Loose ends from session 4
- **Manual browser eyeball of both IGV viewers** -- NOT auto-verified. happy-dom can't run igv's
  canvas, so the component tests MOCK igv. The pure coordinate/placement adapters ARE fully tested.
  Confirm the inline-FASTA render (Slice A) and the hosted-hg38 load (Slice B, needs network) work
  in a real browser before relying on them visually.
- `igv@^3.8.1` is now an INSTALLED optionalDependency in `frontend/package.json` (+ package-lock).
  It pulled in zero transitive deps and zero new npm advisories (the 6 npm flags are pre-existing
  dev tooling: vite/vitest/happy-dom).

---

## Session 4 entry point (historical -- superseded by the START HERE above; written 2026-06-01, end of session 3)

### Session 4 kickoff prompt (copy-paste this into the new session)
```
Continuing BioForge (github.com/itthj/bioforge, main @ 32d6b6e, working tree clean, ~90% of the
v4 blueprint). Backend 968 tests (+ docker/online e2e, gated), frontend 91 vitest -- all green;
tsc --strict + ruff clean. Local repo: C:\Users\james\OneDrive\Documents\BIOTECH 101\bioforge;
venv at bioforge\.venv; Docker works (deepcrispr/azimuth/lindel/forecast legacy images built);
node not on PATH (prepend C:\Users\james\AppData\Local\Programs\nodejs); gh is in WSL Ubuntu, not
Windows (wsl -d Ubuntu -- bash -lc "gh ...").

First: read docs/handoff.md, especially the "★ START HERE -- session 4 entry point" section at the
top. That's the live resume point.

Last session (3) shipped 8 slices, all FF-merged to main: the full section-13 benchmark suite
(on-target DeepCRISPR x Chari-2015; off-target CFD vs GUIDE-seq/validated sites; edit-outcome
TVD/JSD; GIAB variant-concordance scorer) + section-6 calibration (reliability diagrams) + a
hardened, primary-source-gated leakage system + TWO real published benchmark results (on-target
rho=0.130 held_out; off-target rho=0.313 unknown) now shown in the Accuracy Report with their
reliability curves. The blueprint's differentiating core (grounding / benchmarking / calibration /
provenance) is substantially done and demonstrated with real numbers.

Where I'm picking up -- next priorities IN ORDER (none can be faked; the integrity IS the product):
  1. IGV.js genome browser (section 5 / Phase 2 guide viz) -- the main remaining buildable feature.
     Add the igv npm dep + a React wrapper; render guide position + PAM + off-target sites on the
     USER-CONFIRMED reference build. First check what genomic coordinates design_guides /
     find_offtargets actually carry; IGV.js can load a hosted hg38 to avoid the 3 GB download.
  2. GIAB end-to-end -- the concordance scorer (benchmarks/variant_concordance.py) is built+tested;
     needs a digest-pinned variant CALLER (bcftools/DeepVariant -- license-audit first), GRCh38
     ~3 GB, and the HG002 truth VCF + high-conf BED. ~half the remaining work; multi-hour.
  3. Edit-outcome live data -- the TVD/JSD scorer is built; needs a license-clean held-out
     indel-distribution dataset (Lindel/inDelphi/FORECasT) to publish a real number via published.py.
  4. DeepSpCas9 -- the blueprint NAMES it the primary on-target model; it was dropped on a license
     audit (rule 15) and DeepCRISPR substituted. A deliberate deviation needing my sign-off, NOT a
     silent fix.
  5. MSA viewer (Phase 4) -- react-msa-viewer not integrated. Minor.

One decision is mine before the next build: which to tackle first -- IGV.js (recommended; the main
user-facing feature left) or the GIAB caller (heavier). If IGV.js: confirm I'm OK adding the igv
dependency + using a hosted hg38 reference. If GIAB: pick the caller (bcftools = lighter /
license-clean; DeepVariant = heavier / more accurate) and license-audit it before building.

Plan before coding; vertical slices end-to-end (never horizontal layers); no heavy agent frameworks;
no faked benchmarks -- the gated remainder is earned with real data.
```

**Repo:** https://github.com/itthj/bioforge — **main @ `32d6b6e`**, working tree clean, everything pushed.
**Local:** `C:\Users\james\OneDrive\Documents\BIOTECH 101\bioforge` (Windows; Docker Desktop + WSL2).
**Suite — all green:** backend **968 passed**, 2 skipped, 16 deselected (online+docker+nextflow gated);
frontend **91 vitest**; `tsc --strict` + `ruff check` + `ruff format` all clean.

### Where the project is: ~90% of the v4 blueprint
The blueprint's differentiating CORE — grounding (§0/§4), benchmarking (§13), calibration (§6),
provenance (§10) — is substantially complete and **demonstrated with real measurements**. Session 3
closed the two biggest gaps: §13 benchmark suite (~55% → ~85%) and §6 calibration (~60% → ~90%).
The blueprint itself is NOT in the repo — the user has it; ask for it / re-paste if needed.

### What session 3 shipped (8 slices, all FF-merged to main, oldest → newest)
1. `8726b79` **on-target efficiency benchmark** — DeepCRISPR × Chari-2015. Fetch-on-first-use effData
   loader `benchmarks/effdata.py` (sha256 + commit pinned, unlicensed data NEVER vendored), tie-aware
   numpy Spearman `benchmarks/on_target_efficiency.py`.
2. `3b32a7f` **calibration + reliability diagrams** — `benchmarks/reliability.py` (numpy quantile bins) +
   frontend `ReliabilityDiagram.tsx` (inline SVG, no chart dependency).
3. `c25a10f` **leakage gate hardened** — typed `LeakageAssessment(status, evidence, caveat)`; Chari-2015
   promoted to `held_out` vs Chuai-2018 (PMC6020378); `held_out`/`contaminated` now STRUCTURALLY require
   primary-source evidence (`test_every_leakage_claim_is_sourced`).
4. `176dc2a` **off-target recall** — CFD vs annotOfftargets readFraction; loader gained `EffDataKind`.
5. `ff269f1` **edit-outcome distribution agreement** — TVD + JSD (numpy); refuses to renormalize a
   malformed distribution.
6. `13ab525` **GIAB variant-concordance metric** — `benchmarks/variant_concordance.py`: stratified
   precision/recall/F1, high-confidence-region restricted, "not haplotype-aware like hap.py" caveat.
   The SCORING half of GIAB.
7. `900d49c` **published live calibration** — `benchmarks/published.py` runs the REAL DeepCRISPR×Chari
   benchmark offline → committed artifact (ρ=0.1299, n=1234, held_out) → served in the Accuracy Report
   → frontend renders the headline + reliability curve.
8. `31026b7` **published off-target** — REAL CFD vs annotOfftargets (ρ=0.3132, n=717, unknown). Both
   arms now show real measured numbers + reliability curves in-product.

### RESUME HERE — next-step priority (session 4), in order
None of these can be faked (§0 / rule 18). Each is heavy-by-nature, distinct UX work, or a deliberate deviation.
1. **IGV.js genome browser** (§5 / Phase 2 guide viz) — THE main remaining buildable feature. Add the
   `igv` npm dep + a React wrapper; render guide position + PAM + off-target sites on the
   USER-CONFIRMED reference build. Data plumbing: check what genomic coordinates `design_guides` /
   `find_offtargets` actually carry (on-target scoring is sequence-relative; `find_offtargets` BLAST
   hits carry genomic positions). IGV.js can load a hosted hg38 → avoids the 3 GB local download.
2. **GIAB end-to-end** — the concordance SCORER is built + tested. Still needs (a) a digest-pinned
   variant CALLER (bcftools/DeepVariant — none integrated; variant tools are annotation-only),
   (b) GRCh38 ~3 GB, (c) GIAB HG002 truth VCF + high-conf BED. License-audit the caller first. The
   metric goes live the moment a caller feeds it real VCFs. ~half the remaining work; multi-hour.
3. **Edit-outcome live data** — the TVD/JSD scorer is built; needs a license-clean held-out
   indel-distribution dataset (Lindel/inDelphi/FORECasT) to publish a real number via `published.py`.
4. **DeepSpCas9 as primary on-target** — the blueprint NAMES it primary; it was dropped on a license
   audit (rule 15) and DeepCRISPR substituted. A deliberate deviation needing license clearance, NOT a
   code gap — flag for the user's sign-off, do not silently "fix".
5. **MSA viewer** (§5 / Phase 4) — react-msa-viewer not integrated. Minor.

### Decisions made this session (do NOT re-litigate)
- **crisporPaper effData = fetch-on-first-use** (consent-gated, sha256 + commit pinned, NEVER vendored
  — it is unlicensed). The loader also accepts a local file / alternate URL, so the posture is not a
  one-way door.
- **Leakage labels are primary-source-gated.** `held_out`/`contaminated` require a verified citation;
  `unknown` is the only label allowed without one. Chari-2015 IS held-out from DeepCRISPR (verified).
- **Published artifacts are committed REAL measurements** (`benchmarks/published/*.json`),
  provenance-stamped, reproducible via `python -m bioforge.benchmarks.published`. Served in the report,
  but the ledger rows stay **guard_only** (a run is offline, not on page load). Never faked, never
  computed on the fly.
- **GIAB row stays `not_yet_wired`** even though its scorer is built — the end-to-end benchmark can't
  RUN without a caller. Honest.
- A §13 benchmark is **`live`** only when it is pure CPU over a committed corpus; anything needing a
  network fetch + a model call is **`guard_only`** (same reasoning as ClinVar fidelity).

### Benchmark architecture (the session-3 footprint)
- `benchmarks/effdata.py` — fetch-on-first-use loader; `EffDataKind` ('on_target'|'off_target');
  `DATASETS` registry (chari2015Train, annotOfftargets), each sha256 + commit pinned. crisporPaper
  commit `33a8225c7bc3be7f937786f6b151ffa7d7e29e84`.
- `benchmarks/{on_target_efficiency,off_target_recall,edit_outcome_agreement,variant_concordance}.py`
  — the four §13 scorers, each with typed honesty rails (`LeakageAssessment` + `assess_leakage*`).
- `benchmarks/reliability.py` — reliability curve from any (predicted, observed) pairs.
- `benchmarks/published.py` — offline generators + `load_published_benchmarks()`; artifacts in
  `benchmarks/published/`.
- `benchmarks/accuracy_report.py` — the §13 ledger + `published` list; served at `GET /benchmarks/accuracy`.
- Frontend: `AccuracyReport.tsx` (ledger + `PublishedResults`), `ReliabilityDiagram.tsx`,
  `types/benchmarks.ts`.

### Environment gotchas (carry forward)
- **Docker works** (29.4.3). Legacy images BUILT locally: `bioforge/deepcrispr:legacy` (c420bda, 7.8 GB),
  `bioforge/azimuth:legacy`, `bioforge/lindel:legacy`, `bioforge/forecast:legacy`. Enable via per-model
  `BIOFORGE_*_ENABLED` + image env vars.
- **gh CLI lives in WSL Ubuntu** (`/usr/bin/gh` v2.46, authed as `itthj`), NOT on Windows. Use
  `wsl -d Ubuntu -- bash -lc "gh ..."` — bare `wsl` defaults to the `docker-desktop` distro and fails.
- **node/npm** at `C:\Users\james\AppData\Local\Programs\nodejs` — NOT on PATH; prepend it (PowerShell).
- **Network reachable** from the venv (NCBI eutils, raw.githubusercontent, api.github.com).
- **Bash-tool cwd flips** repo-root ↔ bioforge. A `cd` inside a bash command persists and shifts cwd for
  later FILE-tool calls — use absolute paths for Read/Edit/Write and `git -C` / absolute `cd` for bash.
- **The auto-mode classifier blocks `git push origin main` and remote branch deletes** unless the
  instruction is specific. Workflow: branch → commit → push branch → `checkout main && merge --ff-only`
  (local, allowed) → `push origin main` (separate; needs an explicit go). Commit messages: ASCII only
  ("section 13", "->", no § / em-dash). CRLF warnings on `git add` are harmless.

### Commands (Windows; venv at `bioforge\.venv`)
```
cd "/c/Users/james/OneDrive/Documents/BIOTECH 101/bioforge"
.venv/Scripts/python.exe -m pytest backend/tests/ -q              # 968 passed
.venv/Scripts/python.exe -m pytest backend/tests/ -m docker -q    # real-image e2e (needs images)
.venv/Scripts/python.exe -m ruff check backend/ ; .venv/Scripts/python.exe -m ruff format --check backend/
# Frontend (PowerShell): $env:PATH = "C:\Users\james\AppData\Local\Programs\nodejs;" + $env:PATH
#   Set-Location ...\bioforge\frontend ; npm run typecheck ; npm test     # tsc --strict ; 91 vitest
# Regenerate published artifacts (offline; deepcrispr image + effData consent):
#   BIOFORGE_DEEPCRISPR_ENABLED=true BIOFORGE_DEEPCRISPR_DOCKER_IMAGE=bioforge/deepcrispr:legacy \
#   BIOFORGE_CRISPOR_EFFDATA_CONSENT=true .venv/Scripts/python.exe -m bioforge.benchmarks.published
```

### Hard rules still in force
Plan before coding · vertical slices · no heavy agent frameworks · real biology in tests · provenance
from day one · typed everything · never silently truncate · **AI never fabricates biology** · **no
unsourced constants** (cite or `# VERIFY:`) · **no license claims from memory** · **no ML training code**
(published weights only) · **faithful, never remap upstream** · behavioral equivalence is the gate ·
**leakage / accuracy labels are primary-source-gated — a `held_out` claim from memory is impossible** ·
**the gated remainder must be earned with real data, never faked — the integrity IS the product.**

---
*Historical session logs (sessions 1-3, newest first) follow below.*

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
- **CLOSED 2026-06-01 — leakage gate:** Chari-2015 vs DeepCRISPR promoted from `unknown` to
  `held_out` against Chuai 2018 primary source (PMC6020378): training corpus is Wang 2014 +
  Hart 2015 + Doench 2016 across HCT116/HEK293T/HeLa/HL60; Chari 2015 = reference [12], used only
  as independent validation. Hardened the design at the same time: `_LEAKAGE` is now typed
  `LeakageAssessment(status, evidence, caveat)`, every `held_out`/`contaminated` MUST carry a
  primary-source citation (test_every_leakage_claim_is_sourced enforces this), and the result
  carries `leakage_evidence` + `leakage_caveat` alongside the status. One residual caveat travels
  with the result: incidental guide overlap with the Doench-2016 HEK293T training subset is
  not sequence-level checked. +3 tests, 930 passed.
- **Live calibration shipped (2026-06-01, session 3):** `benchmarks/published.py` runs the REAL
  DeepCRISPR x Chari-2015 benchmark offline (Docker `bioforge/deepcrispr:legacy` over all 1234
  guides) and writes a provenance-stamped JSON artifact
  (`benchmarks/published/on_target_chari2015_deepcrispr.json`) -- measured **Spearman rho 0.1299**,
  Pearson 0.1162, n=1234, leakage held_out. `build_accuracy_report()` loads it into
  `AccuracyReport.published`; the frontend renders each as a headline card (rho/n/leakage badge +
  measured-date) plus the real reliability curve (bin-means rise 0.83->1.36, monotonicity 0.83 --
  good ranking, noisy per-guide, the textbook cross-dataset story). Regenerate with
  `python -m bioforge.benchmarks.published` (env: BIOFORGE_DEEPCRISPR_ENABLED/IMAGE +
  CRISPOR_EFFDATA_CONSENT). This turns §6 from "capability built" to "capability DEMONSTRATED with
  real numbers". +2 backend tests; 967 passed + 91 vitest.
  - **Second published arm (off-target, no Docker):** `generate_off_target_artifact` publishes the
    REAL CFD vs annotOfftargets discrimination -- **Spearman rho 0.3132**, n=717, leakage unknown
    (honest), reliability curve cleanly monotonic (bin-means 0.002->0.062 as CFD 0.017->0.654). CFD
    is in-platform, so this needs only the network fetch (no Docker). Both arms now show real numbers
    + reliability curves in the Accuracy Report. `published/off_target_annotofftargets_cfd.json`. 968 passed.
- **GIAB concordance metric shipped (2026-06-01, session 3):** `benchmarks/variant_concordance.py`
  is the SCORING half of the GIAB benchmark -- stratified precision/recall/F1 (SNV/INDEL/ALL)
  restricted to high-confidence regions, with parsimonious normalized-allele matching, a
  bisect-indexed region lookup, and a `parse_vcf.Variant` adapter (explodes multiallelic, skips
  symbolic ALTs). Pure stdlib + pydantic (no pysam, consistent with parse_vcf). Honest caveat
  travels with every result: genotype-agnostic exact-match, NOT haplotype-aware like hap.py.
  **GIAB ledger row deliberately stays not_yet_wired** -- the metric is built + tested but the
  end-to-end benchmark still needs (1) a variant-CALLING path (no caller integrated) and (2) the
  GIAB HG002 truth-set + BED download. +11 tests; 965 passed. THIS is the honest maximum toward
  GIAB without the 3GB reference + a caller; the metric goes live the moment a caller feeds it.
- **Edit-outcome distribution-agreement arm shipped (2026-06-01, session 3):**
  `benchmarks/edit_outcome_agreement.py` provides typed Total Variation Distance + Jensen-Shannon
  divergence (numpy-only) between a predicted indel distribution and an observed one. Same honesty
  rails as on/off-target: typed `LeakageAssessment` registry (starts empty, never claims `held_out`
  from memory); refuses to silently renormalize a malformed distribution; per-label
  `(predicted, observed)` pairs feed the same reliability diagram. Accuracy Report row flipped
  not_yet_wired -> guard_only. The live distribution-vs-distribution wiring against a held-out
  Lindel/inDelphi/FORECasT dataset is still a follow-up (needs a license-clean source). +15 tests;
  954 passed; ruff clean.
- **Off-target recall arm shipped (2026-06-01, session 3):** `benchmarks/off_target_recall.py`
  scores every (sgRNA, validated off-target) pair from the crisporPaper aggregated `annotOfftargets`
  table (718 sites across Tsai 2015 / Frock 2015 / Cho 2014 / Kim 2015 / Ran 2015, pinned commit
  33a8225, sha256 0a27d1ab3d5c6a57cb5c55ecb89cc86e5262e12caccd1fb55c4e3e8c8008d815) with the
  platform's full Doench-2016 CFD (mismatch x PAM) and correlates against the upstream
  `readFraction` (Spearman + Pearson + recall@quantile). effdata.py loader extended to dispatch
  on `EffDataKind` ('on_target'|'off_target'), with a typed `EffOfftargetRow` and a strict
  parser that drops malformed rows with a recorded `n_skipped` rather than silently scoring zero.
  Leakage = 'unknown' until verified against Doench 2016 (same gate discipline as on-target).
  Accuracy Report row flipped not_yet_wired -> guard_only. +9 tests; 939 passed; ruff clean.
- **Calibration shipped (2026-06-01, session 3):** `benchmarks/reliability.py` turns the on-target
  `(predicted, observed)` pairs into a typed `ReliabilityCurve` (numpy quantile bins + Spearman
  monotonicity_rho; honest `kind="regression_ranking"` — NOT probability calibration, y=x is not the
  target). Frontend `ReliabilityDiagram` (inline SVG + accessible bin table, no chart dependency)
  renders it in the Accuracy Report's new Calibration section; populated offline (guard_only), with an
  honest note when no curve is attached. 8 backend + 6 frontend tests. Closes the §6/rule-11 gap.
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
