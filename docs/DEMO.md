# BioForge -- demo walkthrough & honesty scorecard

This doc does two things:
1. A **step-by-step walkthrough** so anyone can see BioForge working end to end.
2. A **"what's real vs honestly-gated" scorecard** -- because the integrity *is* the product.
   BioForge's whole claim is that it is grounded, benchmarked, calibrated, and provenance-stamped,
   and that it never fabricates biology or accuracy numbers. This doc states exactly which numbers
   are real measurements and which capabilities are built but deliberately waiting on real external
   data (never faked).

> All benchmark numbers below are **real measurements**, reproducible from committed artifacts
> (`backend/src/bioforge/benchmarks/published/*.json`), not illustrative placeholders.

---

## 1. Run it

See `README.md` for the full quickstart. Shortest path:

```powershell
# Backend (venv): from repo root, with ANTHROPIC_API_KEY in .env
uvicorn bioforge.main:app --app-dir backend/src --reload

# Frontend (node not on PATH -> prepend it):
$env:PATH = "C:\Users\james\AppData\Local\Programs\nodejs;" + $env:PATH
cd frontend ; npm run dev    # http://localhost:5173
```

The genome-browser views use `igv` (an installed optional dependency). The hg38 off-target view
(step 4) loads the GRCh38 reference from igv.js's hosted genome registry -- it needs network but
**no 3 GB local download**.

---

## 2. Walkthrough -- and what each step proves

### Step 1 -- Natural-language CRISPR design (the agent loop)
Ask, in plain English: *"Design a CRISPR knockout for this sequence: <paste ~200+ nt of DNA>, and
check off-targets."*

You'll see the live **trace**: plan -> approval gate -> tool execution -> critic -> (replan if
needed). The tool chain runs `design_guides` -> `score_guide_on_target` -> `edit_outcome` ->
(optionally) `find_offtargets`, composed by `crispr_edit_report`.

**Proves:** the agentic core (plan/approve/execute/critique/replan over native tool-use), with every
step, input, output, tool version, and cost visible in the trace (provenance from day one).

### Step 2 -- The CRISPR edit report
The result renders as a `CrisprReportCard`: ranked guides, a recommendation label, on-target +
heuristic scores side by side, expected NHEJ edit outcomes, and caveats the responder is required
to surface (e.g. "on-target scoring is rule-based, not the Doench Rule Set 2 trained model").

**Proves:** honest framing -- the heuristic is labeled a heuristic, ML scores are point estimates
shown side by side (disagreement = signal), and nothing claims more confidence than it has.

### Step 3 -- IGV guide map on the submitted sequence (Slice A)
Click **Load genome browser** on the report. igv.js renders the *submitted sequence as its own
reference*, with each guide's protospacer + PAM (+ cut site, when the edit outcome was simulated)
at the exact coordinates the tools emit.

**Proves the honesty posture:** the submitted locus is its OWN coordinate system. There is no genome
build claim, so there is nothing the view can misplace on a chromosome. The note on the card says so.

### Step 4 -- Off-targets on hg38, honesty-gated (Slice B)
Re-run with off-target search on. The recommended guide gains an **Off-targets on GRCh38 (hg38)**
panel. Hits that provably sit on a GRCh38 primary chromosome are drawn on the hosted hg38 genome;
every other hit (gene/transcript records, scaffolds, a different build, a non-human BLAST subject)
is listed in a table **by accession, never given a locus**.

**Proves the core principle in one screen:** a wrong placement is worse than no placement. The
accession->chromosome map is sourced from the NCBI GRCh38 assembly report (committed with provenance
+ sha256), and placement is version-specific -- a GRCh37 `NC_000001.10` hit is refused, not silently
shown at GRCh38 coordinates.

### Step 5 -- The Accuracy Report (real benchmarks + calibration)
Open the **Accuracy** tab. You'll see the section-13 benchmark ledger (each row honestly labeled
`live` / `guard_only`; as of session 5 there are **no `not_yet_wired` rows left**) and the
**published results** with real measured numbers and their reliability curves:

| Benchmark | Model x dataset | Metric | n | Leakage / scope |
|---|---|---|---|---|
| On-target efficiency | DeepCRISPR x Chari-2015 | Spearman rho **0.1299** | 1234 | `held_out` (vs Chuai 2018, verified) |
| Off-target discrimination | CFD x annotOfftargets | Spearman rho **0.3132** | 717 | `unknown` (honest -- not verified) |
| Variant-calling concordance | DeepVariant x NIST/GIAB (NA12878, chr20:10-10.1Mb) | precision **0.980** / recall **1.000** (ALL) | 49 | small build-matched validation region, not genome-wide HG002 |

Regenerate offline (proves they're real, not hand-entered): the correlation arms via
`python -m bioforge.benchmarks.published`; the GIAB arm via `benchmarks.published.generate_giab_artifact`
over a real DeepVariant run on staged, build-matched inputs (digest-pinned image).

**Proves:** the differentiating core is not just *built* -- it is *demonstrated with real numbers*,
each carrying a leakage label that is structurally required to cite a primary source.

---

## 3. What's REAL vs HONESTLY-GATED (the scorecard)

### Real / live (demonstrated, not promised)
- **On-target benchmark** -- DeepCRISPR x Chari-2015, Spearman rho = 0.1299 (n=1234), `held_out`.
  Cross-dataset on-target correlation is known to be low (Haeussler 2016) -- the number is honest,
  not over-sold.
- **Off-target benchmark** -- CFD x annotOfftargets, Spearman rho = 0.3132 (n=717), `unknown` leakage.
- **GIAB variant-calling concordance** -- a REAL DeepVariant 1.6.1 (digest-pinned, BSD-3-Clause) run
  scored vs the NIST/GIAB truth: NA12878 (HG001), chr20:10-10.1Mb, within the high-confidence BED ->
  precision 0.980 / recall 1.000 (ALL; 49 truth variants in-region). **Honestly scoped:** a small,
  build-matched VALIDATION region demonstrating the wired pipeline end-to-end -- NOT a genome-wide
  HG002 accuracy claim, and genotype-agnostic exact-match (not haplotype-aware like hap.py). The
  full genome-wide HG002 run is the same code path over more data.
- **ClinVar fidelity** -- scored against LIVE ClinVar (`-m online`, nightly), not a frozen snapshot.
- **Calibration / reliability diagrams** -- real (predicted, observed) pairs from the benchmarks,
  rendered as reliability curves (honest `kind="regression_ranking"`, not probability calibration).
- **Grounding (annotate mode, on by default)** -- deterministic numeric + structured-identifier
  grounding + a soundness detector validate every answer at zero model cost; a visible grounding
  summary is appended.
- **Provenance** -- run manifests (byte-stable content hash, repro-determinism guarded) + RO-Crate
  1.1 JSON-LD export.
- **Genome browser (this session)** -- guide map on the submitted sequence (Slice A) and
  honesty-gated off-target view on hosted hg38 (Slice B).

### Honestly-gated -- built + tested, deliberately waiting on real external data (NEVER faked)
- **Edit-outcome distribution agreement** -- the TVD + JSD scorer is built + tested. A real published
  number awaits a license-clean held-out indel-distribution dataset (Lindel/inDelphi/FORECasT).

### Deliberate, documented deviations (license-driven, not shortcuts)
- **DeepSpCas9** -- the blueprint names it the primary on-target model; it was dropped on a license
  audit (CC-BY-NC / unlicensed) and **DeepCRISPR (Apache-2.0)** substituted as the deep on-target
  primary. Pending a final user sign-off; see `docs/license_audit.md`.
- **inDelphi weights** and **crisporPaper effData** -- non-commercial / unlicensed, so both are
  **fetch-on-first-use** (consent-gated, sha256 + provenance pinned, NEVER vendored), not redistributed.

### Why the gating is the point
Fabricating a gold-set, a coefficient, or a calibration curve would destroy the one thing that makes
BioForge worth using over a generic chatbot. So the rule is absolute: **source it or gate it -- never
a confident wrong number.** Every `held_out` / `contaminated` leakage label is structurally required
to carry a primary-source citation; a benchmark is `live` only when it is pure CPU over a committed
corpus. The honestly-gated remainder is earned with real data, in the open.

---

*Live project state and the next-step priority list live in `docs/handoff.md` (the authoritative
resume point). Architecture + grounding design: `docs/grounding.md`, `docs/phase5_architecture.md`.
License posture: `docs/license_audit.md`.*
