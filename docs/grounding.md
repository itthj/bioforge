# Grounding & anti-hallucination architecture

BioForge defends correctness with defense-in-depth, organized around one principle:

> **Grounding is not correctness.** A real tool value attached to a wrong interpretation is
> more dangerous than an invented number — the fabricated value fails a naive check, the
> misinterpreted-but-real value passes it. So we defend at three boundaries: inputs,
> execution, and output.

The validator lives in `backend/src/bioforge/agent/grounding/` and is wired into the agent
loop after the response is produced. **Every layer is gated and off by default** — with the
flags unset, the loop is byte-for-byte identical to before.

## Layers implemented

| Layer | Module | What it does | LLM? |
|---|---|---|---|
| **L0** system prompt | `prompts/system.md`, `prompts/critic.md` | Instructs the agent: every claim (numeric, entity, mechanistic) must trace to a tool field; separate findings from background; state caveats before the result; never fabricate uncertainty. Necessary but never sufficient. | — |
| **L3** numeric grounding | `numeric.py` | Deterministic. Extracts numeric claims from the draft and matches them against a numeric inventory built from the run's structured tool outputs (percent/fraction duality, precision-aware rounding). Conservative extractor: identifier-embedded digits (`Cas9`, `BRCA1`, `5'`, `SARS-CoV-2`) are not treated as quantities. **The floor — ~100% block precision.** | no |
| **L3+** identifier grounding | `entities.py` | Deterministic. Grounds structured biological identifiers (rsID, RefSeq/Ensembl accessions, ClinVar/PDB IDs) by exact membership against tool outputs **and the user's request** (echoing an input ID is not a fabrication). Free and always-on alongside numeric; these shapes are never background prose. Gene symbols and free-text entities are left to L4. Unsupported identifiers are redacted in place (they carry exact offsets). | no |
| **L4** entity/mechanistic judge | `judge.py`, `prompts/grounding_judge.md` | Classifies and judges named-entity and mechanistic claims, constrained to support a claim **only** by citing a field that exists in the tool outputs — never its own knowledge. Lossy and *measured*, never trusted blindly. | yes |
| **L6** validate-the-validator | `metrics.py`, `corpus/numeric_l3.json` | Hand-labeled corpus + precision/recall metrics for the numeric layer. Release-gated (`test_grounding_metrics.py`): block precision 1.0, fabrication recall 1.0. | no |
| **L7** execution-time soundness | `soundness.py` | Deterministic range/sanity checks on tool outputs: GC% ∈ [0,100], unit-interval scores ∈ [0,1], pLDDT ∈ [0,100], e-value ≥ 0. An impossible value is a failure, not a finding. Precision-first: only fields with certain bounds are checked. | no |

The grounding result rides on a `validation` trace step (in the existing `AgentStep.verdict`
field, so no serialization change). It carries the numeric report, judged claims, the
`soundness` report, and an `enforced` flag.

## Configuration

| Env var | Default | Effect |
|---|---|---|
| `BIOFORGE_GROUNDING_ENABLED` | `false` | Master switch. When on, L3 + L7 run and a `validation` step is recorded. |
| `BIOFORGE_GROUNDING_MODE` | `shadow` | `shadow` = observe/record only; `enforce` = redact unsupported numeric claims in place (`[unverifiable]`) and flag entity/mechanistic claims, with an audit note. |
| `BIOFORGE_GROUNDING_JUDGE_ENABLED` | `false` | Enables the L4 LLM judge (an extra model call per response). Independent of the free numeric layer. |
| `BIOFORGE_GROUNDING_JUDGE_MODEL` | `""` | Model id for the judge (Opus recommended). Empty = reuse the run model. |

Enforcement is **visible redaction** (policy a): never a silent rewrite, never a vague
qualitative substitution for a stripped number. Grounded claims and fully-grounded
responses are left untouched.

## Deliberately deferred (not yet implemented)

- **Execution-time replan on a soundness/L7 violation** — currently detected and recorded; failing the step and replanning (§4.1 loop) is a deeper executor change.
- **L5 iterative rewrite re-validation** — redaction is designed to be re-validation-safe (the marker carries no number), but the rewrite is not re-run through the layers in a loop.
- **OOD detection & calibration (§6)**, **benchmark gold-sets (GIAB/GUIDE-seq/ClinVar, §13)**, **registry uncertainty metadata (§4.2)**, **Phase-2 ML scorers (DeepSpCas9/CFD/FORECasT/Lindel)** — these are capability-expansion groups, parked by scope (we are hardening the existing platform, not extending it).

## The honest risk

The validator's true recall ceiling is set by **claim extraction**, which sits above every
layer: a claim that is never extracted is never judged. The L6 corpus measures the whole
pipeline (extraction + grounding) by comparing predicted-vs-expected by value, so an
extraction miss counts as a false negative — but closing that hole fully would mean the
responder emitting structured claims at generation time, which is not yet done.
