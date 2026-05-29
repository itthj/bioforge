"""§13 ClinVar interpretation-fidelity benchmark — the relabeling guard as a gate.

A faithful report must score 100%; a remapped significance ('Likely pathogenic' reported as
'Pathogenic') or a dropped star rating must be caught.
"""

from __future__ import annotations

from bioforge.benchmarks import score_clinvar_fidelity
from bioforge.benchmarks.clinvar_fidelity import REFERENCE_CASES


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
