"""Tests for lookup_clinvar.

NCBI is never hit. Both `_esearch_clinvar` and `_esummary_clinvar` are
monkeypatched; the esummary stub returns the committed BRCA1 fixture. One
@pytest.mark.online test exercises the live NCBI endpoint for BRCA1 — runs
on the nightly job to catch upstream API drift.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pydantic
import pytest
from bioforge.tools.base import ToolError
from bioforge.tools.variants import lookup_clinvar as lc_module
from bioforge.tools.variants.lookup_clinvar import (
    LookupClinvarInput,
    _classify_query,
    _map_classification,
    _map_record,
    lookup_clinvar,
)

FIXTURE = Path(__file__).parent / "fixtures" / "clinvar_esummary_brca1_17661.json"


def _load_fixture() -> dict[str, Any]:
    with FIXTURE.open("r", encoding="utf-8") as f:
        return json.load(f)


def _result_dict() -> dict[str, Any]:
    return _load_fixture()["result"]


def _brca1_record() -> dict[str, Any]:
    return _result_dict()["17661"]


# --- Query classification ----------------------------------------------------------


def test_classify_numeric_uid() -> None:
    assert _classify_query("17661") == "uid"


def test_classify_vcv_with_and_without_version() -> None:
    assert _classify_query("VCV000017661") == "vcv"
    assert _classify_query("VCV000017661.157") == "vcv"
    assert _classify_query("vcv000017661") == "vcv"  # case-insensitive


def test_classify_rcv() -> None:
    assert _classify_query("RCV000019229") == "rcv"


def test_classify_free_text() -> None:
    assert _classify_query("BRCA1 c.181T>G") == "free_text"
    assert _classify_query("NM_007294.4:c.181T>G") == "free_text"


# --- Input validation --------------------------------------------------------------


def test_input_rejects_empty_query() -> None:
    with pytest.raises(pydantic.ValidationError):
        LookupClinvarInput(query="")


def test_input_rejects_garbage_characters() -> None:
    with pytest.raises(pydantic.ValidationError, match="unexpected characters"):
        LookupClinvarInput(query="BRCA1; DROP TABLE variants;--")


def test_input_caps_max_records() -> None:
    with pytest.raises(pydantic.ValidationError):
        LookupClinvarInput(query="17661", max_records=999)


# --- _map_classification -----------------------------------------------------------


def test_map_classification_pathogenic_with_traits() -> None:
    raw = _brca1_record()["germline_classification"]
    cs = _map_classification(raw)
    assert cs.description == "Pathogenic"
    assert cs.review_status == "reviewed by expert panel"
    assert cs.last_evaluated == "2015/08/10 00:00"
    assert "Breast-ovarian cancer, familial, susceptibility to, 1" in cs.trait_names


def test_map_classification_empty_squashes_sentinel_date() -> None:
    """NCBI uses '1/01/01 00:00' as a 'never evaluated' sentinel — we squash to None."""
    raw = _brca1_record()["clinical_impact_classification"]
    cs = _map_classification(raw)
    assert cs.description is None
    assert cs.review_status is None
    assert cs.last_evaluated is None  # NOT the sentinel
    assert cs.trait_names == []


def test_map_classification_handles_missing_dict() -> None:
    assert _map_classification({}).description is None
    assert _map_classification(None).description is None  # type: ignore[arg-type]


# --- _map_record full conversion ---------------------------------------------------


def test_map_record_brca1_canonical_shape() -> None:
    rec = _map_record(_brca1_record())
    assert rec.uid == "17661"
    assert rec.accession == "VCV000017661"
    assert rec.accession_version == "VCV000017661.157"
    assert "BRCA1" in rec.title
    assert rec.cdna_change == "c.181T>G"
    assert rec.protein_change == "C61G, C14G"
    assert rec.canonical_spdi == "NC_000017.11:43106486:A:C"
    assert "BRCA1" in rec.genes
    assert "missense variant" in rec.molecular_consequences
    assert rec.germline.description == "Pathogenic"
    assert rec.scv_count == 5  # fixture trimmed to 5
    assert rec.rcv_count == 5
    assert rec.clinvar_url == "https://www.ncbi.nlm.nih.gov/clinvar/variation/17661/"


def test_map_record_locations_includes_grch38_and_grch37() -> None:
    rec = _map_record(_brca1_record())
    assemblies = {loc.assembly_name for loc in rec.locations}
    assert assemblies == {"GRCh38", "GRCh37"}
    current = next(loc for loc in rec.locations if loc.status == "current")
    assert current.assembly_name == "GRCh38"
    assert current.chr == "17"
    assert current.start == 43106487
    assert current.stop == 43106487


def test_map_record_aliases_preserved() -> None:
    rec = _map_record(_brca1_record())
    assert "p.C61G:TGT>GGT" in rec.aliases
    assert "NP_009225.1:p.Cys61Gly" in rec.aliases


# --- End-to-end (numeric UID skips esearch) ----------------------------------------


async def test_numeric_uid_skips_esearch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Numeric input goes straight to esummary — esearch must NOT fire."""
    esearch_called = False

    async def boom_esearch(*a, **kw):
        nonlocal esearch_called
        esearch_called = True
        return []

    async def fake_esummary(uids: list[str]) -> dict[str, Any]:
        assert uids == ["17661"]
        return _result_dict()

    monkeypatch.setattr(lc_module, "_esearch_clinvar", boom_esearch)
    monkeypatch.setattr(lc_module, "_esummary_clinvar", fake_esummary)

    out = await lookup_clinvar(LookupClinvarInput(query="17661"))

    assert esearch_called is False
    assert out.query_kind == "uid"
    assert len(out.records) == 1
    assert out.records[0].germline.description == "Pathogenic"


async def test_vcv_routes_via_esearch(monkeypatch: pytest.MonkeyPatch) -> None:
    """VCV input first esearches to resolve to a UID, then esummary."""
    esearch_terms: list[str] = []

    async def fake_esearch(term: str, retmax: int) -> list[str]:
        esearch_terms.append(term)
        return ["17661"]

    async def fake_esummary(uids: list[str]) -> dict[str, Any]:
        return _result_dict()

    monkeypatch.setattr(lc_module, "_esearch_clinvar", fake_esearch)
    monkeypatch.setattr(lc_module, "_esummary_clinvar", fake_esummary)

    out = await lookup_clinvar(LookupClinvarInput(query="VCV000017661.157"))
    assert esearch_terms == ["VCV000017661[VCV]"]  # version stripped
    assert out.query_kind == "vcv"
    assert out.records[0].accession == "VCV000017661"


async def test_rcv_routes_via_esearch_with_rcv_field(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str] = []

    async def fake_esearch(term: str, retmax: int) -> list[str]:
        captured.append(term)
        return ["17661"]

    async def fake_esummary(uids: list[str]) -> dict[str, Any]:
        return _result_dict()

    monkeypatch.setattr(lc_module, "_esearch_clinvar", fake_esearch)
    monkeypatch.setattr(lc_module, "_esummary_clinvar", fake_esummary)

    out = await lookup_clinvar(LookupClinvarInput(query="RCV000019229"))
    assert captured == ["RCV000019229[RCV]"]
    assert out.query_kind == "rcv"


async def test_free_text_routes_to_esearch_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str] = []

    async def fake_esearch(term: str, retmax: int) -> list[str]:
        captured.append(term)
        return ["17661"]

    async def fake_esummary(uids: list[str]) -> dict[str, Any]:
        return _result_dict()

    monkeypatch.setattr(lc_module, "_esearch_clinvar", fake_esearch)
    monkeypatch.setattr(lc_module, "_esummary_clinvar", fake_esummary)

    out = await lookup_clinvar(LookupClinvarInput(query="BRCA1 c.181T>G"))
    assert captured == ["BRCA1 c.181T>G"]
    assert out.query_kind == "free_text"


# --- Empty / cap behavior ----------------------------------------------------------


async def test_no_results_returns_clean_empty_output_with_caveat(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_esearch(*a, **kw):
        return []

    async def boom_esummary(uids: list[str]) -> dict[str, Any]:
        raise AssertionError("esummary should not be called when esearch returned nothing")

    monkeypatch.setattr(lc_module, "_esearch_clinvar", fake_esearch)
    monkeypatch.setattr(lc_module, "_esummary_clinvar", boom_esummary)

    out = await lookup_clinvar(LookupClinvarInput(query="VCV099999999"))
    assert out.records == []
    assert any("no uids" in c.lower() for c in out.caveats)


async def test_max_records_cap_emits_caveat(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_esearch(*a, **kw):
        # 7 UIDs returned, max_records=3.
        return [str(i) for i in range(7)]

    async def fake_esummary(uids: list[str]) -> dict[str, Any]:
        # esummary only sees the truncated list.
        assert len(uids) == 3
        # Return a result dict where each uid maps to a minimal record.
        records: dict[str, Any] = {"uids": uids}
        for u in uids:
            records[u] = {"uid": u, "accession": f"VCV{u}", "title": f"variant {u}"}
        return records

    monkeypatch.setattr(lc_module, "_esearch_clinvar", fake_esearch)
    monkeypatch.setattr(lc_module, "_esummary_clinvar", fake_esummary)

    out = await lookup_clinvar(LookupClinvarInput(query="some gene query", max_records=3))
    assert len(out.records) == 3
    assert any("only the first 3" in c.lower() for c in out.caveats)


# --- Error paths -------------------------------------------------------------------


async def test_esearch_http_error_surfaces_as_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            return httpx.Response(status_code=503, content=b"upstream busy", request=httpx.Request("GET", url))

    monkeypatch.setattr(lc_module.httpx, "AsyncClient", FakeClient)
    with pytest.raises(ToolError, match="HTTP 503"):
        await lookup_clinvar(LookupClinvarInput(query="BRCA1 c.181T>G"))


async def test_esearch_returns_error_field(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            return httpx.Response(
                status_code=200,
                content=b'{"esearchresult":{"ERROR":"Invalid db parameter."}}',
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(lc_module.httpx, "AsyncClient", FakeClient)
    with pytest.raises(ToolError, match="esearch error"):
        await lookup_clinvar(LookupClinvarInput(query="BRCA1 c.181T>G"))


async def test_entrez_email_unset_adds_caveat(monkeypatch: pytest.MonkeyPatch) -> None:
    """If BIOFORGE_ENTREZ_EMAIL is unset, we still work but warn the user."""
    monkeypatch.setattr(lc_module.settings, "entrez_email", "", raising=False)

    async def fake_esummary(uids: list[str]) -> dict[str, Any]:
        return _result_dict()

    monkeypatch.setattr(lc_module, "_esummary_clinvar", fake_esummary)

    out = await lookup_clinvar(LookupClinvarInput(query="17661"))
    assert any("BIOFORGE_ENTREZ_EMAIL" in c for c in out.caveats)


# --- Registry ---------------------------------------------------------------------


async def test_tool_registered_with_correct_tags() -> None:
    from bioforge.tools.registry import get_tool

    spec = get_tool("lookup_clinvar")
    assert spec.cost_hint == "moderate"
    assert {"variants", "annotation", "clinvar"} <= set(spec.tags)
    assert any("Landrum" in c or "ClinVar" in c for c in spec.citations)
    assert any("E-utilities" in c or "Sayers" in c for c in spec.citations)


# --- Live integration (opt-in) ----------------------------------------------------


@pytest.mark.online
async def test_live_brca1_lookup_returns_pathogenic_germline() -> None:
    """Hits the real NCBI E-utilities for BRCA1 Variation 17661. Deselected
    by default; the nightly online job runs it."""
    out = await lookup_clinvar(LookupClinvarInput(query="17661"))
    assert len(out.records) == 1
    rec = out.records[0]
    assert "BRCA1" in rec.genes
    assert rec.germline.description == "Pathogenic"
    # 4★ review status is stable for this variant.
    assert rec.germline.review_status == "reviewed by expert panel"
    assert "missense variant" in rec.molecular_consequences
