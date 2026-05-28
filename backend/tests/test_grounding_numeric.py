"""Tests for Layer 3 deterministic numeric grounding (BioForge v4 §4).

Three concerns are exercised:
  1. Extraction: the conservative claim tokenizer must catch real quantities while
     refusing to treat identifier-embedded digits (Cas9, BRCA1, 5', SARS-CoV-2) as claims.
  2. Inventory: the generous walker must flatten every numeric value from structured
     outputs (including numbers inside strings) and never count booleans.
  3. Grounding: the two canonical contracts — pass 78%/0.78/"roughly 0.78" against a 0.78
     field; hard-block a fabricated 0.92 — plus percent/fraction duality, rounding,
     coordinates, e-values, and counts.

The real-biology integration test runs the actual `design_guides` tool on the committed
lambda phage fixture and grounds the agent's legitimate quote vs. a fabricated score.
"""

from __future__ import annotations

from bioforge.agent.grounding import (
    build_inventory,
    extract_numeric_claims,
    ground_response,
)
from bioforge.agent.grounding.report import ValidationReport


def _vals(text: str) -> list[float]:
    return [c.value for c in extract_numeric_claims(text)]


# --- Extraction: real quantities ----------------------------------------------------


def test_extracts_decimal_score() -> None:
    (claim,) = extract_numeric_claims("The on-target score is 0.78 for this guide.")
    assert claim.value == 0.78
    assert claim.decimals == 2
    assert claim.is_percent is False
    assert claim.is_sci is False
    assert claim.text == "0.78"


def test_extracts_percent() -> None:
    (claim,) = extract_numeric_claims("GC content is 78% across the protospacer.")
    assert claim.value == 78.0
    assert claim.is_percent is True
    assert claim.text == "78%"


def test_extracts_comma_grouped_coordinate() -> None:
    # 'chr17' must NOT yield 17 (identifier); the coordinate 43,000,000 must.
    assert _vals("Variant at chr17:43,000,000 in the genome.") == [43_000_000.0]


def test_extracts_scientific_notation_evalue() -> None:
    (claim,) = extract_numeric_claims("Top BLAST hit with an e-value of 2e-40.")
    assert claim.is_sci is True
    assert claim.value == 2e-40


def test_extracts_range_endpoints() -> None:
    # A digit-hyphen-digit range keeps both endpoints (these are positions).
    assert _vals("Strong G/C preference at positions 16-20 of the protospacer.") == [16.0, 20.0]


def test_extracts_unit_suffixed_quantity() -> None:
    # Number+unit (digits first) is a real quantity and is kept.
    assert _vals("Designed a 20-nt guide.") == [20.0]


# --- Extraction: identifier guards (precision-critical) -----------------------------


def test_does_not_extract_protein_or_enzyme_identifiers() -> None:
    assert _vals("The Cas9 guide targets p53 and BRCA1.") == []


def test_does_not_extract_genome_build_identifiers() -> None:
    assert _vals("Aligned against hg38 / GRCh38.") == []


def test_does_not_extract_hyphenated_identifiers() -> None:
    assert _vals("SARS-CoV-2 spike; relevant to COVID-19.") == []


def test_does_not_extract_sequence_ends() -> None:
    assert _vals("Protospacer shown 5'->3' on the target strand.") == []


def test_does_not_extract_rsid_or_hgvs() -> None:
    assert _vals("Variant rs80357065 is c.5266dupC in BRCA1.") == []


def test_does_not_extract_ordinals_or_versions() -> None:
    assert _vals("The 1st of 9th attempts used tool v1.0.0.") == []


def test_extracts_real_number_amid_identifiers() -> None:
    # The 9 in 'Cas9' is skipped; the genuine score 0.82 is kept.
    assert _vals("The Cas9 guide scored 0.82.") == [0.82]


# --- Inventory ----------------------------------------------------------------------


def test_inventory_flattens_nested_values() -> None:
    entries = build_inventory([{"a": 1, "b": [{"c": 2.5}], "score": 0.78}])
    vals = [e.value for e in entries]
    assert 1.0 in vals
    assert 2.5 in vals
    assert 0.78 in vals


def test_inventory_excludes_booleans() -> None:
    # bool is a subclass of int — a True/False flag must never become a groundable "1"/"0".
    entries = build_inventory([{"count": 1, "destructive": True, "ok": False}])
    vals = [e.value for e in entries]
    assert vals.count(1.0) == 1  # only from `count`, not from `destructive=True`
    assert 0.0 not in vals  # `ok=False` must not contribute a 0


def test_inventory_extracts_numbers_inside_strings() -> None:
    # Coordinates / citations live inside string fields — they must be groundable.
    entries = build_inventory([{"vcf_string": "17-43000000-A-G", "cite": "Doench 2016"}])
    vals = [e.value for e in entries]
    assert 43_000_000.0 in vals
    assert 2016.0 in vals


def test_inventory_records_paths() -> None:
    entries = build_inventory([{"guides": [{"gc_percent": 55.0}]}])
    paths = {e.path for e in entries}
    assert "guides[0].gc_percent" in paths


# --- Grounding: the two canonical contracts -----------------------------------------

_OUTPUTS = [
    {
        "on_target_score": 0.78,
        "guides": [{"gc_percent": 78.0, "protospacer_start": 10}],
        "num_returned": 3,
    }
]


def test_grounds_exact_decimal() -> None:
    report = ground_response("The on-target score is 0.78.", _OUTPUTS)
    assert report.ok
    (v,) = report.numeric_claims
    assert v.status == "grounded"
    assert v.matched_value == 0.78


def test_grounds_percent_form_of_fraction_field() -> None:
    # A 0.78 score quoted as "78%" must ground against the 0.78 field.
    report = ground_response("The on-target score is 78%.", _OUTPUTS)
    assert report.ok


def test_grounds_approximate_phrasing() -> None:
    report = ground_response("The guide scored roughly 0.78 on-target.", _OUTPUTS)
    assert report.ok


def test_grounds_percent_against_percent_field() -> None:
    report = ground_response("GC content is 78%.", _OUTPUTS)
    assert report.ok


def test_hard_blocks_fabricated_score() -> None:
    report = ground_response("The on-target score is 0.92.", _OUTPUTS)
    assert not report.ok
    assert [v.value for v in report.unsupported] == [0.92]


def test_grounds_count_and_coordinate() -> None:
    report = ground_response("Found 3 guides; the top one starts at position 10.", _OUTPUTS)
    assert report.ok


# --- Grounding: rounding, duality false-positive guard, e-values --------------------


def test_grounds_rounded_quote_of_precise_field() -> None:
    report = ground_response("The score is 0.78.", [{"score": 0.7843}])
    assert report.ok
    report2 = ground_response("The score is 0.92.", [{"score": 0.7843}])
    assert not report2.ok


def test_percent_does_not_falsely_match_unrelated_value() -> None:
    # Regression: "78%" must NOT match a 0.9 field via rounding-to-integer.
    report = ground_response("Confidence is 78%.", [{"plddt": 0.9}])
    assert not report.ok


def test_grounds_evalue() -> None:
    report = ground_response("Best hit e-value of 2e-40.", [{"e_value": 2e-40}])
    assert report.ok


def test_evalue_mismatch_is_blocked() -> None:
    report = ground_response("Best hit e-value of 2e-40.", [{"e_value": 5e-12}])
    assert not report.ok


# --- Grounding: empty / report shape ------------------------------------------------


def test_no_tool_outputs_blocks_any_number() -> None:
    report = ground_response("The score is 0.5.", [])
    assert not report.ok
    assert report.inventory_size == 0
    assert isinstance(report, ValidationReport)


def test_response_without_numbers_is_ok() -> None:
    report = ground_response("The Cas9 guide was designed against BRCA1 in GRCh38.", _OUTPUTS)
    assert report.ok
    assert report.numeric_claims == []


def test_report_summary_and_layer() -> None:
    report = ground_response("Scores 0.78 and 0.92.", _OUTPUTS)
    assert report.layer == "L3_numeric"
    assert len(report.numeric_claims) == 2
    assert len(report.unsupported) == 1
    assert "grounded" in report.summary


# --- Real biology: actual tool, real lambda phage DNA -------------------------------


async def test_grounds_real_design_guides_output(lambda_phage_fixture) -> None:
    from bioforge.tools.sequence.design_guides import DesignGuidesInput, design_guides

    out = await design_guides(DesignGuidesInput(sequence=lambda_phage_fixture["sequence"], max_guides=5))
    assert out.num_returned >= 1
    dump = out.model_dump()

    # The agent legitimately quotes a real value the tool produced -> grounded.
    true_gc = out.guides[0].gc_percent
    grounded = ground_response(f"The top guide has a GC content of {true_gc}%.", [dump])
    assert grounded.ok, grounded.summary

    # A fabricated score that appears nowhere in the structured output -> hard-blocked.
    fabricated = ground_response("Its off-target CFD score is 0.123456789.", [dump])
    assert not fabricated.ok
    assert any(v.value == 0.123456789 for v in fabricated.unsupported)
