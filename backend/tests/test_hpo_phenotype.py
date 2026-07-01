"""Tests for hpo_phenotype — Monarch v3 gene-to-HPO-phenotype lookup.

Network is never hit. `_search_gene` and `_fetch_associations` are monkeypatched.
Special focus on the defensive object-field parsing (nested dict vs flat string)
and the empty-list-vs-unrecognized-envelope distinction called out in the module
docstring as a previously-fixed bug.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from bioforge.tools import REGISTRY
from bioforge.tools.base import ToolError
from bioforge.tools.knowledge.hpo_phenotype import (
    HpoPhenotypeInput,
    _extract_list,
    _object_id_and_name,
    _parse_terms,
    _publications_list,
    hpo_phenotype,
)
from pydantic import ValidationError


# --- Registry --------------------------------------------------------------------


def test_hpo_phenotype_registered():
    assert "hpo_phenotype" in REGISTRY


def test_hpo_phenotype_metadata():
    spec = REGISTRY["hpo_phenotype"]
    assert spec.name == "hpo_phenotype"
    assert spec.description
    assert spec.version
    assert spec.citations
    assert "knowledge" in spec.tags
    assert "hpo" in spec.tags


# --- Input validation ----------------------------------------------------------------


def test_empty_gene_rejected():
    with pytest.raises(ValidationError):
        HpoPhenotypeInput(gene="   ")


def test_gene_whitespace_stripped():
    inp = HpoPhenotypeInput(gene="  BRCA1  ")
    assert inp.gene == "BRCA1"


def test_max_terms_bounds():
    with pytest.raises(ValidationError):
        HpoPhenotypeInput(gene="BRCA1", max_terms=0)
    with pytest.raises(ValidationError):
        HpoPhenotypeInput(gene="BRCA1", max_terms=501)


# --- _extract_list: the empty-vs-unrecognized envelope distinction -----------------


def test_extract_list_recognized_key_empty_is_not_an_error():
    result = _extract_list({"associations": []}, ["associations", "items"], "test")
    assert result == []


def test_extract_list_recognized_key_with_data():
    result = _extract_list({"associations": [{"a": 1}]}, ["associations", "items"], "test")
    assert result == [{"a": 1}]


def test_extract_list_falls_back_to_second_candidate_key():
    result = _extract_list({"items": [{"a": 1}]}, ["associations", "items"], "test")
    assert result == [{"a": 1}]


def test_extract_list_no_recognized_key_raises():
    """This is the exact bug described in the module docstring: a missing/unrecognized
    envelope key must raise, not silently look like zero results."""
    with pytest.raises(ToolError, match="did not contain any"):
        _extract_list({"totally_different_key": []}, ["associations", "items"], "test")


def test_extract_list_wrong_type_raises():
    with pytest.raises(ToolError, match="not a list"):
        _extract_list({"associations": {"not": "a list"}}, ["associations", "items"], "test")


# --- Object-field shape handling (nested dict vs flat string) ----------------------


def test_object_field_as_flat_string():
    assoc = {"object": "HP:0001250", "object_label": "Seizure"}
    obj_id, obj_name = _object_id_and_name(assoc)
    assert obj_id == "HP:0001250"
    assert obj_name == "Seizure"


def test_object_field_as_nested_dict():
    assoc = {"object": {"id": "HP:0001250", "name": "Seizure"}}
    obj_id, obj_name = _object_id_and_name(assoc)
    assert obj_id == "HP:0001250"
    assert obj_name == "Seizure"


def test_object_field_nested_dict_uses_label_fallback():
    assoc = {"object": {"id": "HP:0001250", "label": "Seizure"}}
    obj_id, obj_name = _object_id_and_name(assoc)
    assert obj_name == "Seizure"


def test_object_field_flat_string_no_label_falls_back_to_id():
    assoc = {"object": "HP:0001250"}
    obj_id, obj_name = _object_id_and_name(assoc)
    assert obj_id == "HP:0001250"
    assert obj_name == "HP:0001250"


def test_publications_list_handles_strings_and_dicts():
    assert _publications_list({"publications": ["PMID:123", "PMID:456"]}) == ["PMID:123", "PMID:456"]
    assert _publications_list({"publications": [{"id": "PMID:789"}]}) == ["PMID:789"]
    assert _publications_list({"publications": None}) == []
    assert _publications_list({}) == []


def test_parse_terms_mixed_shapes():
    records = [
        {"object": "HP:0001250", "object_label": "Seizure"},
        {"object": {"id": "HP:0000252", "name": "Microcephaly"}},
        {"object": None},  # malformed, should be skipped
    ]
    terms = _parse_terms(records)
    assert len(terms) == 2
    assert {t.hpo_id for t in terms} == {"HP:0001250", "HP:0000252"}


# --- Happy path: CURIE input (skips search) -----------------------------------------


def _assoc(hpo_id="HP:0001250", name="Seizure", **extra) -> dict:
    return {"object": {"id": hpo_id, "name": name}, **extra}


async def test_curie_input_skips_search():
    with patch("bioforge.tools.knowledge.hpo_phenotype._search_gene", AsyncMock()) as mock_search:
        with patch(
            "bioforge.tools.knowledge.hpo_phenotype._fetch_associations",
            AsyncMock(return_value=[_assoc()]),
        ):
            out = await hpo_phenotype(HpoPhenotypeInput(gene="HGNC:1100"))
    mock_search.assert_not_called()
    assert out.hgnc_id == "HGNC:1100"
    assert out.n_terms == 1
    assert out.terms[0].hpo_id == "HP:0001250"


async def test_curie_input_case_insensitive():
    with patch("bioforge.tools.knowledge.hpo_phenotype._search_gene", AsyncMock()) as mock_search:
        with patch(
            "bioforge.tools.knowledge.hpo_phenotype._fetch_associations",
            AsyncMock(return_value=[]),
        ):
            out = await hpo_phenotype(HpoPhenotypeInput(gene="hgnc:1100"))
    mock_search.assert_not_called()
    assert out.hgnc_id == "HGNC:1100"


# --- Happy path: symbol input (goes through search) ---------------------------------


async def test_symbol_input_calls_search_then_fetch():
    with patch(
        "bioforge.tools.knowledge.hpo_phenotype._search_gene",
        AsyncMock(return_value=("HGNC:1100", "BRCA1")),
    ) as mock_search:
        with patch(
            "bioforge.tools.knowledge.hpo_phenotype._fetch_associations",
            AsyncMock(return_value=[_assoc(), _assoc(hpo_id="HP:0000252", name="Microcephaly")]),
        ) as mock_fetch:
            out = await hpo_phenotype(HpoPhenotypeInput(gene="BRCA1"))
    mock_search.assert_called_once_with("BRCA1")
    mock_fetch.assert_called_once()
    assert out.gene_symbol == "BRCA1"
    assert out.hgnc_id == "HGNC:1100"
    assert out.n_terms == 2


async def test_frequency_onset_evidence_populated_when_present():
    assoc = _assoc(frequency_qualifier="HP:0040283", onset_qualifier="Congenital onset", has_evidence="ECO:0000501")
    with patch(
        "bioforge.tools.knowledge.hpo_phenotype._search_gene",
        AsyncMock(return_value=("HGNC:1100", "BRCA1")),
    ):
        with patch("bioforge.tools.knowledge.hpo_phenotype._fetch_associations", AsyncMock(return_value=[assoc])):
            out = await hpo_phenotype(HpoPhenotypeInput(gene="BRCA1"))
    term = out.terms[0]
    assert term.frequency == "HP:0040283"
    assert term.onset == "Congenital onset"
    assert term.evidence == "ECO:0000501"


async def test_no_terms_found_adds_explanatory_caveat_not_error():
    with patch(
        "bioforge.tools.knowledge.hpo_phenotype._search_gene",
        AsyncMock(return_value=("HGNC:99999", "OBSCUREGENE")),
    ):
        with patch("bioforge.tools.knowledge.hpo_phenotype._fetch_associations", AsyncMock(return_value=[])):
            out = await hpo_phenotype(HpoPhenotypeInput(gene="OBSCUREGENE"))
    assert out.n_terms == 0
    assert out.terms == []
    assert any("No HPO phenotype associations" in c for c in out.caveats)


async def test_max_terms_truncates():
    records = [_assoc(hpo_id=f"HP:{i:07d}", name=f"term{i}") for i in range(10)]
    with patch(
        "bioforge.tools.knowledge.hpo_phenotype._search_gene",
        AsyncMock(return_value=("HGNC:1100", "BRCA1")),
    ):
        with patch("bioforge.tools.knowledge.hpo_phenotype._fetch_associations", AsyncMock(return_value=records)):
            out = await hpo_phenotype(HpoPhenotypeInput(gene="BRCA1", max_terms=3))
    assert out.n_terms == 3


# --- Error paths: search step -----------------------------------------------------


async def test_search_no_hits_raises():
    with patch(
        "bioforge.tools.knowledge.hpo_phenotype._search_gene",
        AsyncMock(side_effect=ToolError("No Monarch gene entity found for 'NOTAGENE'.")),
    ):
        with pytest.raises(ToolError, match="No Monarch gene entity found"):
            await hpo_phenotype(HpoPhenotypeInput(gene="NOTAGENE"))


async def test_search_gene_real_function_picks_hgnc_hit(monkeypatch):
    """Exercise the real _search_gene against a fake httpx client."""
    import bioforge.tools.knowledge.hpo_phenotype as mod

    class _FakeResponse:
        status_code = 200

        def json(self):
            return {
                "items": [
                    {"id": "MGI:1234", "name": "Brca1"},  # mouse ortholog, should be skipped
                    {"id": "HGNC:1100", "name": "BRCA1"},
                ]
            }

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            return _FakeResponse()

    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda timeout=None: _FakeClient())
    hgnc_id, symbol = await mod._search_gene("BRCA1")
    assert hgnc_id == "HGNC:1100"
    assert symbol == "BRCA1"


async def test_search_gene_empty_items_raises(monkeypatch):
    import bioforge.tools.knowledge.hpo_phenotype as mod

    class _FakeResponse:
        status_code = 200

        def json(self):
            return {"items": []}

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            return _FakeResponse()

    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda timeout=None: _FakeClient())
    with pytest.raises(ToolError, match="No Monarch gene entity found"):
        await mod._search_gene("NOTAGENE")


async def test_fetch_associations_http_error_status(monkeypatch):
    import bioforge.tools.knowledge.hpo_phenotype as mod

    class _FakeResponse:
        status_code = 503

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            return _FakeResponse()

    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda timeout=None: _FakeClient())
    with pytest.raises(ToolError, match="HTTP 503"):
        await mod._fetch_associations("HGNC:1100", 50)


# --- Tool-level metadata stamping ------------------------------------------------


async def test_tool_stamped_via_registry_execute():
    from bioforge.tools.registry import execute_tool

    with patch(
        "bioforge.tools.knowledge.hpo_phenotype._search_gene",
        AsyncMock(return_value=("HGNC:1100", "BRCA1")),
    ):
        with patch(
            "bioforge.tools.knowledge.hpo_phenotype._fetch_associations",
            AsyncMock(return_value=[_assoc()]),
        ):
            result = await execute_tool("hpo_phenotype", {"gene": "BRCA1"})
    assert result.tool_name == "hpo_phenotype"
    assert result.tool_version == "1.0.0"
