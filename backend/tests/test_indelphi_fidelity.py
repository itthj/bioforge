"""End-to-end fidelity smoke test for the real inDelphi model.

This test runs the actual upstream `inDelphi.predict()` against a real guide
and asserts STRUCTURAL properties of the result — not pinned per-outcome
values, since pinning to exact upstream numbers would require generating
those numbers from a verified upstream run first.

It is automatically skipped unless ALL of the following are true:

  1. `BIOFORGE_INDELPHI_CONSENT_NONCOMMERCIAL=true` is set in the env. The
     fetcher refuses to download otherwise — this is the user's opt-in to
     the non-commercial license.
  2. The `[indelphi]` optional deps (pandas + scikit-learn) are installed.
  3. Either: the upstream files have already been fetched into the data
     dir, OR the test is allowed to fetch them now (network required).

This is a `@pytest.mark.online` test — deselected by default in CI to keep
the standard run hermetic. To run it locally after opting in:

    BIOFORGE_INDELPHI_CONSENT_NONCOMMERCIAL=true pytest -m online backend/tests/test_indelphi_fidelity.py

What this test catches if it ever fails:

* Upstream commit pin no longer downloads cleanly (404, force-push, etc.).
* sklearn version compatibility broke (modern sklearn can't load 0.20-era pickles).
* Our DataFrame → InDelphiDistribution mapping silently drops outcomes or stats.
* The model loads but produces obviously-wrong distributions (no outcomes,
  frameshift fraction outside [0, 100], probabilities summing way off 100).
"""

from __future__ import annotations

import importlib.util
import os

import pytest

# This is a deliberately heavy, opt-in test — keep it under the `online` marker
# so the default run skips it.
pytestmark = pytest.mark.online


def _should_skip_reason() -> str | None:
    """Return a human-readable reason if we should skip, or None to proceed."""
    if os.environ.get("BIOFORGE_INDELPHI_CONSENT_NONCOMMERCIAL", "").lower() not in ("1", "true", "yes"):
        return (
            "BIOFORGE_INDELPHI_CONSENT_NONCOMMERCIAL is not set to true. "
            "Read backend/src/bioforge/tools/sequence/models/indelphi/LICENSE_NOTICE.md "
            "and set the env var to opt in before running this test."
        )
    for pkg in ("pandas", "sklearn"):
        if importlib.util.find_spec(pkg) is None:
            return (
                f"Optional dependency {pkg!r} is not installed. "
                "Run `pip install -e .[indelphi]` to enable the inDelphi model."
            )
    return None


# Lambda-phage-derived 60-mer with one NGG site at a known position. Same
# construction the deterministic tests use so the cut math is well-understood.
_GUIDE = "ACGTACGTACGTACGTACGT"
_TARGET = (
    "AAAAATTTTAAAAATTTTAA"  # filler [0..20]
    + _GUIDE  # protospacer [20..40]
    + "AGG"  # PAM [40..43]
    + "CCCCCCCCCCCCCCCCC"  # filler [43..60]
)


async def test_indelphi_real_run_produces_sane_distribution() -> None:
    """Fully end-to-end: fetch (if needed), load, predict, check structure.

    NOT a per-outcome fidelity test — there's no committed ground truth
    to compare against. This is the safety net that catches "the whole pipeline
    silently broke" failures while remaining cheap to maintain.
    """
    reason = _should_skip_reason()
    if reason:
        pytest.skip(reason)

    from bioforge.tools.sequence.edit_outcome import EditOutcomeInput, edit_outcome

    out = await edit_outcome(EditOutcomeInput(target=_TARGET, guide=_GUIDE, model="indelphi", cell_type="mESC"))

    # Structural assertions.
    assert out.model_used == "indelphi"
    assert out.indelphi_distribution is not None
    assert out.indelphi_distribution.cell_type == "mESC"
    assert out.indelphi_distribution.cutsite == 37

    # The model must return a non-trivial number of outcomes (deletions cover
    # many sizes/positions, plus 4 single-base insertions). 10 is a defensive lower bound.
    assert len(out.outcomes) >= 10

    # Probabilities are fractions in [0, 1] (we converted from upstream's percentages).
    for o in out.outcomes:
        assert 0.0 <= o.probability <= 1.0
        assert o.outcome_type in {"indelphi_deletion", "indelphi_insertion"}

    # Distribution should be sorted by probability descending.
    probs = [o.probability for o in out.outcomes]
    assert probs == sorted(probs, reverse=True)

    # Both categories should appear (inDelphi predicts both deletions and 1-bp insertions).
    types_seen = {o.outcome_type for o in out.outcomes}
    assert types_seen == {"indelphi_deletion", "indelphi_insertion"}

    # Stats sanity.
    stats = out.indelphi_distribution.stats
    assert 0.0 <= stats.frameshift_frequency <= 100.0
    assert 0.0 <= stats.one_bp_ins_frequency <= 100.0
    assert 0.0 <= stats.mh_del_frequency <= 100.0
    # MH + MHless deletion frequencies + 1bp insertion fraction should roughly
    # account for the bulk of the distribution. Loose bound — model can have
    # other small contributions — but a total under 50% would signal something
    # broken in how we read the stats dict.
    bulk = stats.mh_del_frequency + stats.mhless_del_frequency + stats.one_bp_ins_frequency
    assert bulk > 50.0, f"inDelphi stats look implausible: bulk={bulk:.1f}%"

    # The InDelphiDistribution outcomes should sum to approximately 100% (modulo
    # MH-less long-tail truncation in upstream). 80% is a defensive lower bound.
    total_pct = sum(o.predicted_frequency for o in out.indelphi_distribution.outcomes)
    assert 80.0 <= total_pct <= 105.0, f"Predicted frequencies sum to {total_pct:.1f}% — expected ~100%"
