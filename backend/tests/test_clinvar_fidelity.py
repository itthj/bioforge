"""§13 ClinVar interpretation-fidelity benchmark — the relabeling guard as a gate.

A faithful report must score 100%; a remapped significance ('Likely pathogenic' reported as
'Pathogenic') or a dropped star rating must be caught.
"""

from __future__ import annotations

from bioforge.benchmarks import score_clinvar_fidelity
from bioforge.benchmarks.clinvar_fidelity import (
    REFERENCE_CASES,
    case_from_clinvar_record,
    review_status_to_stars,
)


def test_faithful_reports_score_full_fidelity() -> None:
    cases = [
        {
            "variant": "v1",
            "gold_significance": "Pathogenic",
            "gold_stars": 3,
            "reported_significance": "Pathogenic",
            "reported_stars": 3,
        },
        {
            "variant": "v2",
            "gold_significance": "Benign",
            "gold_stars": 2,
            "reported_significance": "Benign",
            "reported_stars": 2,
        },
    ]
    report = score_clinvar_fidelity(cases)
    assert report.ok
    assert report.agreement_rate == 1.0


def test_pathogenic_and_likely_pathogenic_are_never_interchangeable() -> None:
    report = score_clinvar_fidelity(
        [{"variant": "v", "gold_significance": "Likely pathogenic", "reported_significance": "Pathogenic"}]
    )
    assert not report.ok
    assert report.violations[0].issue == "significance remapped or changed"


def test_dropped_star_rating_is_a_violation() -> None:
    report = score_clinvar_fidelity(
        [
            {
                "variant": "v",
                "gold_significance": "Pathogenic",
                "gold_stars": 2,
                "reported_significance": "Pathogenic",
                "reported_stars": 0,
            }
        ]
    )
    assert not report.ok
    assert "star rating" in report.violations[0].issue


def test_verbatim_match_tolerates_only_whitespace() -> None:
    report = score_clinvar_fidelity(
        [
            {
                "variant": "v",
                "gold_significance": "Uncertain significance",
                "reported_significance": "  Uncertain   significance ",
            }
        ]
    )
    assert report.ok  # whitespace normalization only — not semantic remapping


def test_reference_cases_split_into_faithful_and_caught() -> None:
    report = score_clinvar_fidelity(REFERENCE_CASES)
    # Two faithful, two adversarial -> exactly two violations, agreement rate 0.5.
    assert report.n == 4
    assert report.agreements == 2
    assert len(report.violations) == 2


# --- Adapter: live ClinVar tool output -> fidelity cases ----------------------------


def test_review_status_to_stars_matches_ncbi_scale() -> None:
    assert review_status_to_stars("practice guideline") == 4
    assert review_status_to_stars("reviewed by expert panel") == 3
    assert review_status_to_stars("criteria provided, multiple submitters, no conflicts") == 2
    assert review_status_to_stars("criteria provided, single submitter") == 1
    assert review_status_to_stars("criteria provided, conflicting classifications") == 1
    assert review_status_to_stars("no assertion criteria provided") == 0
    # Case/whitespace-insensitive; unknown/empty -> None (never a guessed rating).
    assert review_status_to_stars("  Reviewed By Expert Panel ") == 3
    assert review_status_to_stars("something brand new") is None
    assert review_status_to_stars(None) is None
    assert review_status_to_stars("") is None


def test_case_from_faithful_clinvar_record_scores_full_fidelity() -> None:
    record = {
        "accession": "VCV000017661",
        "germline": {
            "description": "Pathogenic",
            "review_status": "criteria provided, multiple submitters, no conflicts",
        },
    }
    case = case_from_clinvar_record(record, gold_significance="Pathogenic", gold_stars=2)
    assert case["reported_significance"] == "Pathogenic"
    assert case["reported_stars"] == 2
    assert case["variant"] == "VCV000017661"
    assert score_clinvar_fidelity([case]).ok


def test_case_from_record_catches_platform_significance_drift() -> None:
    # Platform reports a different significance than gold -> caught (never silently remapped).
    record = {"germline": {"description": "Likely pathogenic", "review_status": "criteria provided, single submitter"}}
    case = case_from_clinvar_record(record, gold_significance="Pathogenic", gold_stars=1, variant="v")
    report = score_clinvar_fidelity([case])
    assert not report.ok
    assert report.violations[0].issue == "significance remapped or changed"


def test_case_from_record_flags_unrecognized_review_status_against_gold_stars() -> None:
    record = {"germline": {"description": "Pathogenic", "review_status": "totally-bogus-status"}}
    case = case_from_clinvar_record(record, gold_significance="Pathogenic", gold_stars=2, variant="v")
    assert case["reported_stars"] is None  # never guessed
    report = score_clinvar_fidelity([case])
    assert not report.ok
    assert "star rating" in report.violations[0].issue


def test_case_from_record_without_gold_stars_skips_star_check() -> None:
    record = {"germline": {"description": "Benign", "review_status": "criteria provided, single submitter"}}
    case = case_from_clinvar_record(record, gold_significance="Benign", variant="v")
    assert "gold_stars" not in case  # significance-only gold -> star check skipped
    assert score_clinvar_fidelity([case]).ok
