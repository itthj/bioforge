# Grounding & anti-hallucination architecture

BioForge defends correctness with defense-in-depth, organized around one principle:

> **Grounding is not correctness.** A real tool value attached to a wrong interpretation is
> more dangerous than an invented number — the fabricated value fails a naive check, the
> misinterpreted-but-real value passes it. So we defend at three boundaries: inputs,
> execution, and output.

The validator lives in `backend/src/bioforge/agent/grounding/` and is wired into the agent
loop after the response is produced. **The free, deterministic layers (numeric, identifier,
soundness) run by default in `annotate` mode** — every answer is validated and carries a
visible grounding summary, at zero model cost. The L4 LLM judge is **opt-in** (it makes an
extra model call), and `shadow` (record-only) / `enforce` (redact) are opt-in alternatives
to `annotate`.

## Layers implemented

| Layer | Module | What it does | LLM? |
|---|---|---|---|
| **L0** system prompt | `prompts/system.md`, `prompts/critic.md` | Instructs the agent: every claim (numeric, entity, mechanistic) must trace to a tool field; separate findings from background; state caveats before the result; never fabricate uncertainty. Necessary but never sufficient. | — |
| **L3** numeric grounding | `numeric.py` | Deterministic. Extracts numeric claims from the draft and matches them against a numeric inventory built from the run's structured tool outputs (percent/fraction duality, precision-aware rounding). Conservative extractor: identifier-embedded digits (`Cas9`, `BRCA1`, `5'`, `SARS-CoV-2`) are not treated as quantities. **The floor — ~100% block precision.** | no |
| **L3+** identifier grounding | `entities.py` | Deterministic. Grounds structured biological identifiers (rsID, RefSeq/Ensembl accessions, ClinVar/PDB IDs) by exact membership against tool outputs **and the user's request** (echoing an input ID is not a fabrication). Free and always-on alongside numeric; these shapes are never background prose. Gene symbols and free-text entities are left to L4. Unsupported identifiers are redacted in place (they carry exact offsets). | no |
| **L4** entity/mechanistic judge | `judge.py`, `prompts/grounding_judge.md` | Classifies and judges named-entity and mechanistic claims, constrained to support a claim **only** by citing a field that exists in the tool outputs — never its own knowledge. Lossy and *measured*, never trusted blindly. | yes |
| **L6** validate-the-validator | `metrics.py`, `corpus/numeric_l3.json` | Hand-labeled corpus + precision/recall metrics for **both** deterministic layers (numeric and identifier), incl. echo-from-input cases. Release-gated (`test_grounding_metrics.py`): block precision 1.0, fabrication recall 1.0 for each. | no |
| **L7** execution-time soundness | `soundness.py` | Deterministic range/sanity checks on tool outputs: GC% ∈ [0,100], unit-interval scores ∈ [0,1], pLDDT ∈ [0,100], e-value ≥ 0. An impossible value is a failure, not a finding. Precision-first: only fields with certain bounds are checked. | no |

The grounding result rides on a `validation` trace step (in the existing `AgentStep.verdict`
field, so no serialization change). It carries the numeric report, judged claims, the
`soundness` report, and an `enforced` flag.

## Configuration

| Env var | Default | Effect |
|---|---|---|
| `BIOFORGE_GROUNDING_ENABLED` | `true` | Master switch for the free deterministic layers (L3, L3+, L7). Set `false` to disable grounding entirely. |
| `BIOFORGE_GROUNDING_MODE` | `annotate` | **`annotate`** (default) = append a visible grounding summary to the response (affirms traced claims, flags untraceable ones — removes nothing); `shadow` = observe/record only (no text change); `enforce` = redact unsupported numeric/identifier claims in place (`[unverifiable]`) with an audit note. |
| `BIOFORGE_GROUNDING_JUDGE_ENABLED` | `false` | Enables the L4 LLM judge (an extra model call per response). Independent of the free numeric layer. |
| `BIOFORGE_GROUNDING_JUDGE_MODEL` | `""` | Model id for the judge (Opus recommended). Empty = reuse the run model. |

Enforcement is **visible redaction** (policy a): never a silent rewrite, never a vague
qualitative substitution for a stripped number. Grounded claims and fully-grounded
responses are left untouched.

## Deliberately deferred (not yet implemented)

- **Execution-time replan on a soundness/L7 violation** — currently detected and recorded; failing the step and replanning (§4.1 loop) is a deeper executor change.
- **L5 iterative rewrite re-validation** — redaction is designed to be re-validation-safe (the marker carries no number), but the rewrite is not re-run through the layers in a loop.
- **Registry uncertainty metadata (§4.2)** — the schema (`model_versions`, `emits_instance_uncertainty`, `published_accuracy`, `training_distribution`, `reference_data_keys`) and the `uncertainty_note()` honesty helper (§6 rule: report only the uncertainty a model emits; never fabricate an interval or figure) are **in place** in `tools/base.py`. **Now populated across the meaningful tools**, on a deliberate rule: a tool carries metadata iff it (a) owns a scoring model/heuristic, or (b) depends on an external reference dataset.
  - *Scoring/heuristic tools* (`model_versions` / `emits_instance_uncertainty` / `published_accuracy` / `training_distribution`): `score_guide_on_target`, `design_guides`, `find_offtargets` (Hsu-2013 MIT score), `edit_outcome` (rule-of-thumb + Bae-2014 MMEJ + opt-in inDelphi). Every `published_accuracy` is sourced or carries an explicit `VERIFY:` — never an invented figure.
  - *`reference_data_keys`* (provenance dependency). Now **consumed by the §10 research-object** (`provenance/research_object.py`) as a run's per-reference-build pins. Key vocabulary in use: `ncbi_blast`, `ncbi_clinvar`, `ncbi_dbsnp`, `gnomad`, `ensembl_vep`, `ensembl_variant_recoder`, `rcsb_pdb`, `alphafold_db`, `interpro`, `sifts`, `indelphi_weights`, `deepcrispr_weights`. Composite tools (`find_best_structure`, `compare_structures`, `crispr_edit_report`) declare the union of references whose content flows into their output.
  - *Deliberately empty* (the honest state — no model, no external reference): pure transforms (`gc_content`, `reverse_complement`, `translate`, `find_orfs`, `parse_vcf`, `format_hgvs`, `codon_usage`), the primer3 wrapper (`design_primers` — a deterministic third-party algorithm; a §10 version-pin candidate, not a §4.2 uncertainty case), and the memory tools. `test_existing_tool_without_metadata_defaults_empty` guards `gc_content` staying empty.
- **OOD gate (§6)** — the deterministic detector is **now implemented** (`grounding/ood.py`): `check_ood` flags inputs that fall outside a model's stated envelope (e.g. a non-20-nt guide against `find_offtargets`' Hsu-2013 SpCas9 off-target weights — precision-first, extended per trained scorer like L7's `_BOUNDS`), and `collect_model_uncertainty` surfaces, for each model-derived score that ran, its `uncertainty_note` honesty posture. Both ride the `validation` verdict (`ood`, `model_uncertainty`); the OOD advisory is appended in annotate/enforce, silent in shadow. **Acting on a flag** (refuse/replan at execution time, §4.1) and **calibration/reliability diagrams** remain deferred — like L7, this slice detects and records.
- **Benchmark gold-sets (GIAB/GUIDE-seq/ClinVar, §13)** — still pending; capability-expansion.
- **Phase-2 ML scorers** — DeepCRISPR on-target (Apache-2.0) is now **scaffolded**: an out-of-process TF1/py3.6 integration behind `score_guide_on_target(model="deepcrispr")`, opt-in via `BIOFORGE_DEEPCRISPR_ENABLED`, returning its score side-by-side with the transparent rule-based one and degrading gracefully when the legacy env is absent. **Numeric validation inside the legacy environment is still pending** — see `tools/sequence/models/deepcrispr/legacy/README.md`. CFD off-target and the FORECasT/Lindel edit-outcome models remain to be added.

## The honest risk

The validator's true recall ceiling is set by **claim extraction**, which sits above every
layer: a claim that is never extracted is never judged. The L6 corpus measures the whole
pipeline (extraction + grounding) by comparing predicted-vs-expected by value, so an
extraction miss counts as a false negative — but closing that hole fully would mean the
responder emitting structured claims at generation time, which is not yet done.
