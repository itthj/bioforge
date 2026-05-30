"""PolyPhen-2 model-variant naming (rule 16 / Phase 3).

The blueprint explicitly forbids reporting a PolyPhen score without naming the classifier
variant (HumDiv vs HumVar). Ensembl VEP defaults to HumVar (verified against the Ensembl
protein-function docs), so annotate_variant surfaces `polyphen_model="HumVar"` whenever a
PolyPhen score is present — never leaving the model ambiguous.
"""

from __future__ import annotations

from bioforge.tools.variants.annotate_variant import _map_transcript_consequence


def test_polyphen_model_named_humvar_when_score_present() -> None:
    row = _map_transcript_consequence(
        {"transcript_id": "ENST1", "polyphen_score": 0.95, "polyphen_prediction": "probably_damaging"},
        "A/G",
    )
    assert row.polyphen_model == "HumVar"
    assert row.polyphen_score == 0.95


def test_polyphen_model_absent_when_no_score() -> None:
    row = _map_transcript_consequence({"transcript_id": "ENST1"}, None)
    assert row.polyphen_model is None
