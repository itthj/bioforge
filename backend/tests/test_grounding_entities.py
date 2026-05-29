"""Deterministic structured-identifier grounding (BioForge v4 §4 — the entity floor).

Covers recognition of the identifier shapes, membership grounding (incl. echo-safety via
the goal), the false-positive guards (background prose / years are not identifiers), and
loop-level enforcement (a fabricated rsID is redacted; an echoed one is preserved).
"""

from __future__ import annotations

import pytest
from bioforge.agent import run_agent
from bioforge.agent.grounding import extract_entity_claims, ground_entities, ground_response
from bioforge.config import settings
from bioforge.constants import DEFAULT_PROJECT_ID


def _kinds(text: str) -> dict[str, str]:
    return {e.kind: e.text for e in extract_entity_claims(text)}


# --- Recognition --------------------------------------------------------------------


def test_recognizes_each_identifier_class() -> None:
    found = _kinds("Variant rs80357065 in NM_007294.3, gene ENSG00000012048, record VCV000017661, structure PDB: 1TUP.")
    assert found["rsid"] == "rs80357065"
    assert found["refseq"] == "NM_007294.3"
    assert found["ensembl"] == "ENSG00000012048"
    assert found["clinvar"] == "VCV000017661"
    assert found["pdb"] == "1TUP"


def test_background_prose_has_no_identifiers() -> None:
    # Gene symbols, years, raw sequence, enzyme names are NOT structured identifiers.
    assert extract_entity_claims("BRCA1 is a tumor suppressor; the 2016 study; sequence ATGCATGC; Cas9.") == []


def test_pdb_requires_keyword() -> None:
    # A bare 4-char token must not be mistaken for a PDB id (would catch years etc.).
    assert extract_entity_claims("The value 1TUP appears without context.") == []


# --- Grounding ----------------------------------------------------------------------


def test_identifier_grounded_against_tool_output() -> None:
    (v,) = ground_entities("The variant rs80357065 is reported.", [{"colocated_variants": [{"id": "rs80357065"}]}])
    assert v.status == "grounded"
    assert v.matched_path == "colocated_variants[0].id"


def test_fabricated_identifier_is_unsupported() -> None:
    (v,) = ground_entities("Linked to rs99999999.", [{"colocated_variants": [{"id": "rs80357065"}]}])
    assert v.status == "unsupported"


def test_identifier_from_user_input_is_grounded() -> None:
    # Echoing an identifier the user supplied is not a fabrication.
    (v,) = ground_entities("rs80357065 was analyzed.", [], extra_sources=["look up rs80357065"])
    assert v.status == "grounded"
    assert v.matched_path == "input[0]"


def test_ground_response_fails_on_fabricated_identifier() -> None:
    report = ground_response(
        "Found rs80357065 and also rs99999999.",
        [{"colocated_variants": [{"id": "rs80357065"}]}],
    )
    assert report.ok is False
    assert [e.text for e in report.unsupported_entities] == ["rs99999999"]


def test_ground_response_ok_when_all_identifiers_grounded() -> None:
    report = ground_response("Found rs80357065.", [{"id": "rs80357065"}])
    assert report.ok is True
    assert report.entity_claims and report.entity_claims[0].status == "grounded"


# --- Loop enforcement ---------------------------------------------------------------


@pytest.fixture
def grounding_enforce(monkeypatch):
    monkeypatch.setattr(settings, "grounding_enabled", True)
    monkeypatch.setattr(settings, "grounding_mode", "enforce")


async def test_enforce_redacts_fabricated_identifier_keeps_echoed_one(
    grounding_enforce,
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    trivial_plan,
) -> None:
    goal = "Look up variant rs80357065 in BRCA1"
    final_text = "The variant rs80357065 was analyzed; it is linked to rs99999999."
    llm = fake_llm_factory(
        [
            make_submit_plan_response(trivial_plan(tool_name="gc_content")),
            make_tool_use_response("gc_content", {"sequence": "ATGCATGC"}),
            make_text_response(final_text),
        ]
    )
    result = await run_agent(goal, project_id=DEFAULT_PROJECT_ID, llm=llm)

    validation = next(s for s in result.steps if s.type == "validation")
    assert validation.verdict["ok"] is False
    body = result.response_text
    before_footer = body.split("---")[0]
    assert "rs80357065" in before_footer  # echoed from the goal -> grounded -> preserved
    assert "rs99999999" not in before_footer  # fabricated -> redacted in the body
    assert "[unverifiable]" in before_footer
    assert "[BioForge grounding]" in body  # audit footer present
