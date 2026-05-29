"""§13 — ClinVar interpretation-fidelity benchmark (BioForge v4).

Measures whether the platform reports a variant's ClinVar clinical significance
**faithfully**: the significance verbatim (no remapping), `Pathogenic` and `Likely
pathogenic` kept distinct, and the review-status star rating preserved. This is the §17
"never remap ClinVar significance" rule expressed as a release-gating metric.

A fidelity *failure* is a silent change between what ClinVar asserts (`gold_*`) and what the
platform reports (`reported_*`) — exactly the corruption that would mislead a clinical
inference. The scorer is gold-data-agnostic: feed it real ≥2★ ClinVar records paired with
the platform's output, or the bundled reference cases that lock the guard logic.

NOTE: the bundled `REFERENCE_CASES` are hand-authored to exercise the guard (faithful +
adversarial). Wiring this to score live `annotate_variant` / `lookup_clinvar` output against
a real high-confidence ClinVar subset is the next step (see docs/handoff).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# The four-plus clinical-significance classes that must never be conflated with one another.
DISTINCT_SIGNIFICANCES: frozenset[str] = frozenset(
    {
        "pathogenic",
        "likely pathogenic",
        "uncertain significance",
        "likely benign",
        "benign",
    }
)


class FidelityViolation(BaseModel):
    variant: str
    issue: str = Field(description="What went wrong, e.g. 'significance remapped' or 'star rating dropped'.")
    gold: str
    reported: str


class FidelityReport(BaseModel):
    n: int
    agreements: int
    agreement_rate: float = Field(description="Fraction of variants reported with full fidelity.")
    violations: list[FidelityViolation] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def _norm(s: str) -> str:
    return " ".join(s.split()).strip().lower()


def score_clinvar_fidelity(cases: list[dict]) -> FidelityReport:
    """Score each (gold vs reported) ClinVar interpretation for fidelity.

    Each case: `variant`, `gold_significance`, `reported_significance`, and optionally
    `gold_stars` / `reported_stars` (0–4 review-status). A case agrees iff the reported
    significance equals the gold significance verbatim AND the star rating is preserved.
    Verbatim matching inherently keeps `Pathogenic` distinct from `Likely pathogenic`.
    """
    violations: list[FidelityViolation] = []
    agreements = 0
    for case in cases:
        variant = case.get("variant", "?")
        gold = case["gold_significance"]
        reported = case["reported_significance"]
        case_ok = True

        if _norm(gold) != _norm(reported):
            violations.append(
                FidelityViolation(
                    variant=variant, issue="significance remapped or changed", gold=gold, reported=reported
                )
            )
            case_ok = False

        gold_stars = case.get("gold_stars")
        reported_stars = case.get("reported_stars")
        if gold_stars is not None and reported_stars != gold_stars:
            violations.append(
                FidelityViolation(
                    variant=variant,
                    issue="review-status star rating not preserved",
                    gold=str(gold_stars),
                    reported=str(reported_stars),
                )
            )
            case_ok = False

        if case_ok:
            agreements += 1

    n = len(cases)
    return FidelityReport(
        n=n,
        agreements=agreements,
        agreement_rate=(agreements / n if n else 1.0),
        violations=violations,
    )


# Hand-authored guard cases: faithful reports must pass; the relabeling / star-dropping
# cases must be caught. (Not live ClinVar truth — see module docstring.)
REFERENCE_CASES: list[dict] = [
    {
        "variant": "faithful-pathogenic",
        "gold_significance": "Pathogenic",
        "gold_stars": 3,
        "reported_significance": "Pathogenic",
        "reported_stars": 3,
    },
    {
        "variant": "faithful-vus",
        "gold_significance": "Uncertain significance",
        "gold_stars": 1,
        "reported_significance": "Uncertain significance",
        "reported_stars": 1,
    },
    {
        "variant": "relabel-likely-to-pathogenic",
        "gold_significance": "Likely pathogenic",
        "gold_stars": 2,
        "reported_significance": "Pathogenic",
        "reported_stars": 2,
    },
    {
        "variant": "dropped-star-rating",
        "gold_significance": "Pathogenic",
        "gold_stars": 2,
        "reported_significance": "Pathogenic",
        "reported_stars": 0,
    },
]
