"""section 13 ClinVar interpretation-fidelity benchmark wired to LIVE ClinVar (online, nightly).

The platform must report ClinVar's clinical significance FAITHFULLY -- verbatim, with the
review-status star rating preserved and Pathogenic kept distinct from Likely pathogenic (the
section 17 "never remap ClinVar" rule). This reads a small curated set of stable, high-
confidence variants TWICE: an independent raw NCBI esummary read (GOLD) and the platform's
`lookup_clinvar` tool (REPORTED), then scores fidelity via the section 13 harness.

No significance is hardcoded from memory -- only Variation IDs are committed; the gold
significance + star rating come live. The >=2-star (high-confidence) subset is selected from
the live gold, so the test is robust to a variant's status drifting upstream.

Deselected by default; runs via `-m online` (nightly workflow).
"""

from __future__ import annotations

import httpx
import pytest
from bioforge.benchmarks.clinvar_fidelity import (
    case_from_clinvar_record,
    review_status_to_stars,
    score_clinvar_fidelity,
)
from bioforge.tools.variants.lookup_clinvar import LookupClinvarInput, lookup_clinvar

pytestmark = pytest.mark.online

# Stable ClinVar Variation IDs, expert-panel reviewed (>=3 star). ONLY the IDs are committed
# -- significance + stars are read live, never memorized. 17661 is the classic BRCA1 185delAG.
_CURATED_UIDS = ["17661", "4849940", "4849524", "4850068"]

_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"


async def _gold(uid: str) -> tuple[str, str | None]:
    """Independent raw NCBI esummary read -> (germline classification description, review_status).

    A SEPARATE code path from lookup_clinvar's parser, so the comparison actually tests the
    platform's transformation rather than tautologically agreeing with itself.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            _ESUMMARY,
            params={"db": "clinvar", "id": uid, "retmode": "json", "tool": "BioForgeFidelityTest"},
        )
    resp.raise_for_status()
    rec = resp.json()["result"][uid]
    gc = rec.get("germline_classification") or {}
    return gc.get("description") or "", gc.get("review_status")


async def test_clinvar_live_fidelity_high_confidence_no_relabeling() -> None:
    cases: list[dict] = []
    for uid in _CURATED_UIDS:
        gold_desc, gold_review = await _gold(uid)
        gold_stars = review_status_to_stars(gold_review)
        if gold_stars is None or gold_stars < 2:
            continue  # restrict to the >=2-star high-confidence subset (data-driven, not memorized)
        reported = await lookup_clinvar(LookupClinvarInput(query=uid))
        records = reported.model_dump().get("records") or []
        assert records, f"lookup_clinvar returned no record for ClinVar {uid}"
        cases.append(case_from_clinvar_record(records[0], gold_significance=gold_desc, gold_stars=gold_stars))

    assert len(cases) >= 2, "expected at least two >=2-star ClinVar variants in the curated set"
    report = score_clinvar_fidelity(cases)
    # Every high-confidence variant must be reported with full fidelity: significance verbatim
    # (Pathogenic != Likely pathogenic) and star rating preserved.
    assert report.ok, f"ClinVar fidelity violations: {report.violations}"
    assert report.agreement_rate == 1.0
