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


# --- Adapters: live tool output -> fidelity cases -----------------------------------
#
# ClinVar review_status -> gold-star rating. Source: NCBI ClinVar "Review status"
# documentation (https://www.ncbi.nlm.nih.gov/clinvar/docs/review_status/), germline /
# oncogenicity aggregate-record scale. An unrecognized status maps to None — we never
# guess a star rating (that would be its own unsourced-constant sin).
_REVIEW_STATUS_STARS: dict[str, int] = {
    "practice guideline": 4,
    "reviewed by expert panel": 3,
    "criteria provided, multiple submitters, no conflicts": 2,
    "criteria provided, conflicting classifications": 1,
    "criteria provided, conflicting interpretations": 1,  # legacy phrasing, same 1-star tier
    "criteria provided, single submitter": 1,
    "no assertion criteria provided": 0,
    "no classification provided": 0,
    "no classification for the individual variant": 0,
    "no assertion provided": 0,  # legacy phrasing
}


def review_status_to_stars(review_status: str | None) -> int | None:
    """Map a ClinVar review_status string to its 0-4 gold-star rating (NCBI scale).

    Case- and whitespace-insensitive. Returns None for an empty or unrecognized status
    rather than guessing — the scorer then treats a None reported-star as 'not preserved'
    against a known gold rating, instead of inventing a value.
    """
    if not review_status:
        return None
    return _REVIEW_STATUS_STARS.get(_norm(review_status))


def case_from_clinvar_record(
    record: dict,
    *,
    gold_significance: str,
    gold_stars: int | None = None,
    variant: str | None = None,
) -> dict:
    """Build a fidelity case from a `lookup_clinvar` record (the platform's REPORTED view)
    paired with the GOLD ClinVar truth supplied by the caller.

    `record` is one `LookupClinvarOutput.records[i]` dict (or its `.model_dump()`): the
    reported significance is `germline.description`, and the reported star rating is derived
    from `germline.review_status` via the NCBI scale. The gold values must come from an
    independent read of ClinVar truth (e.g. the raw esummary), NEVER from memory — this
    keeps the benchmark a real fidelity check rather than a tautology.

    Star fields are included only when the caller provides `gold_stars`, so significance-only
    gold data still produces a valid (significance-only) case.
    """
    germline = record.get("germline") or {}
    case: dict = {
        "variant": variant or record.get("accession") or record.get("uid") or "?",
        "gold_significance": gold_significance,
        "reported_significance": germline.get("description") or "",
    }
    if gold_stars is not None:
        case["gold_stars"] = gold_stars
        case["reported_stars"] = review_status_to_stars(germline.get("review_status"))
    return case


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
