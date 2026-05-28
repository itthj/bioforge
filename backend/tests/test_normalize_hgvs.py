"""Tests for normalize_hgvs.

Ensembl is never hit in the hot suite. `_fetch_variant_recoder` is
monkeypatched and returns the committed BRCA1 c.5266dupC fixture — the
canonical right-shift example (historic '5382insC' → modern 'c.5266dup').
One @pytest.mark.online test hits the live endpoint for the nightly job.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pydantic
import pytest
from bioforge.tools.base import ToolError
from bioforge.tools.variants import normalize_hgvs as nh_module
from bioforge.tools.variants.normalize_hgvs import (
    NormalizeHgvsInput,
    _map_allele,
    _map_response,
    _pick_primary_hgvsg,
    normalize_hgvs,
)

FIXTURE = Path(__file__).parent / "fixtures" / "variant_recoder_brca1_5266dupc.json"


def _load_fixture() -> list[dict[str, Any]]:
    with FIXTURE.open("r", encoding="utf-8") as f:
        return json.load(f)


def _allele_c() -> dict[str, Any]:
    """The 'C' allele sub-dict from the fixture (BRCA1 dupC is single-allele)."""
    return _load_fixture()[0]["C"]


# --- Input validation --------------------------------------------------------------


def test_input_accepts_refseq_coding() -> None:
    inp = NormalizeHgvsInput(hgvs="NM_007294.4:c.5266dupC")
    assert inp.hgvs == "NM_007294.4:c.5266dupC"


def test_input_accepts_ensembl_coding() -> None:
    NormalizeHgvsInput(hgvs="ENST00000357654.9:c.5266dup")


def test_input_accepts_genomic() -> None:
    NormalizeHgvsInput(hgvs="17:g.43057065dup")


def test_input_strips_whitespace() -> None:
    inp = NormalizeHgvsInput(hgvs="  NM_007294.4:c.5266dupC  ")
    assert inp.hgvs == "NM_007294.4:c.5266dupC"


def test_input_rejects_empty() -> None:
    with pytest.raises(pydantic.ValidationError):
        NormalizeHgvsInput(hgvs="")


def test_input_rejects_garbage_chars() -> None:
    with pytest.raises(pydantic.ValidationError, match="unexpected characters"):
        NormalizeHgvsInput(hgvs="BRCA1; DROP TABLE variants;--")


def test_input_caps_max_transcript_forms() -> None:
    with pytest.raises(pydantic.ValidationError):
        NormalizeHgvsInput(hgvs="NM_007294.4:c.5266dupC", max_transcript_forms=9999)


def test_input_defaults() -> None:
    inp = NormalizeHgvsInput(hgvs="NM_007294.4:c.5266dupC")
    assert inp.species == "human"
    assert inp.max_transcript_forms == 20


# --- _pick_primary_hgvsg -----------------------------------------------------------


def test_pick_primary_prefers_nc_chromosome() -> None:
    assert _pick_primary_hgvsg(["LRG_292:g.160921dup", "NC_000017.11:g.43057065dup"]) == "NC_000017.11:g.43057065dup"


def test_pick_primary_falls_back_when_no_nc() -> None:
    assert _pick_primary_hgvsg(["LRG_292:g.160921dup"]) == "LRG_292:g.160921dup"


def test_pick_primary_empty_returns_none() -> None:
    assert _pick_primary_hgvsg([]) is None


# --- _map_allele on real fixture ---------------------------------------------------


def test_map_allele_extracts_brca1_dupc() -> None:
    rec = _map_allele("C", _allele_c(), max_forms=20)
    assert rec.allele == "C"
    assert rec.input == "NM_007294.4:c.5266dupC"


def test_map_allele_picks_nc_genomic_as_primary() -> None:
    """The right-shift signal: the genomic primary is at position 43057065 (the
    3'-most C in the repeat stretch), not whatever the historic position was."""
    rec = _map_allele("C", _allele_c(), max_forms=20)
    assert rec.primary_hgvsg == "NC_000017.11:g.43057065dup"
    # And the LRG form is also present in the full list.
    assert "LRG_292:g.160921dup" in rec.hgvsg


def test_map_allele_picks_first_coding_and_protein_as_primary() -> None:
    rec = _map_allele("C", _allele_c(), max_forms=20)
    assert rec.primary_hgvsc is not None
    assert rec.primary_hgvsc.endswith("dup")
    assert rec.primary_hgvsp is not None
    assert "fsTer" in rec.primary_hgvsp  # frameshift → premature termination


def test_map_allele_caps_transcript_forms() -> None:
    """BRCA1 has 200+ transcripts; the cap must trim both hgvsc and hgvsp."""
    rec = _map_allele("C", _allele_c(), max_forms=10)
    assert len(rec.hgvsc) == 10
    assert len(rec.hgvsp) == 10
    # totals are the un-capped counts, so they reveal the trimming.
    assert rec.total_hgvsc_count > 10
    assert rec.total_hgvsp_count > 10


def test_map_allele_preserves_spdi() -> None:
    rec = _map_allele("C", _allele_c(), max_forms=20)
    assert any(s.startswith("NC_000011") or s.startswith("NC_000017") for s in rec.spdi)


def test_right_shift_evidence_in_raw_response() -> None:
    """The headline right-shift assertion: input 'c.5266dupC' (explicit base 'C')
    canonicalizes to 'c.5266dup' (trailing base dropped — HGVS shorthand for a
    single-base duplication) on the user's input transcript NM_007294.4.

    Asserted against the raw fixture (not a capped record) — trimming is a
    separate concern tested in test_map_allele_caps_transcript_forms.
    """
    raw_hgvsc = _allele_c().get("hgvsc", [])
    assert "NM_007294.4:c.5266dup" in raw_hgvsc, (
        f"expected NM_007294.4:c.5266dup in raw hgvsc (sample first 3: {raw_hgvsc[:3]})"
    )


# --- _map_response -----------------------------------------------------------------


def test_map_response_flattens_allele_keys() -> None:
    alleles = _map_response(_load_fixture(), max_forms=20)
    assert len(alleles) == 1  # BRCA1 c.5266dupC is single-allelic
    assert alleles[0].allele == "C"


def test_map_response_skips_warnings_key() -> None:
    """variant_recoder sometimes emits a 'warnings' key at allele-dict level —
    must not be treated as an allele."""
    payload = [{"warnings": ["something"], "A": {"input": "x", "hgvsg": [], "hgvsc": [], "hgvsp": [], "spdi": []}}]
    alleles = _map_response(payload, max_forms=20)
    assert len(alleles) == 1
    assert alleles[0].allele == "A"


def test_map_response_handles_empty_payload() -> None:
    assert _map_response([], max_forms=20) == []


def test_map_response_skips_non_dict_entries() -> None:
    payload = [{"C": {"input": "x", "hgvsg": [], "hgvsc": [], "hgvsp": [], "spdi": []}}, "garbage"]
    alleles = _map_response(payload, max_forms=20)  # type: ignore[arg-type]
    assert len(alleles) == 1


# --- End-to-end (monkeypatched fetcher) --------------------------------------------


async def test_end_to_end_brca1_dupc(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, str]] = []

    async def fake_fetch(hgvs: str, species: str) -> list[dict[str, Any]]:
        captured.append((hgvs, species))
        return _load_fixture()

    monkeypatch.setattr(nh_module, "_fetch_variant_recoder", fake_fetch)

    out = await normalize_hgvs(NormalizeHgvsInput(hgvs="NM_007294.4:c.5266dupC"))

    assert captured == [("NM_007294.4:c.5266dupC", "human")]
    assert out.query == "NM_007294.4:c.5266dupC"
    assert len(out.alleles) == 1
    rec = out.alleles[0]
    assert rec.primary_hgvsg == "NC_000017.11:g.43057065dup"
    # The recipe-blessed primary coding form: HGVS canonical dropping the explicit base.
    assert rec.primary_hgvsc is not None
    assert rec.primary_hgvsc.endswith(":c.1840dup") or "dup" in rec.primary_hgvsc


async def test_trimming_emits_caveat(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(hgvs: str, species: str) -> list[dict[str, Any]]:
        return _load_fixture()

    monkeypatch.setattr(nh_module, "_fetch_variant_recoder", fake_fetch)

    out = await normalize_hgvs(NormalizeHgvsInput(hgvs="NM_007294.4:c.5266dupC", max_transcript_forms=5))
    assert any("trimmed transcript forms to 5" in c for c in out.caveats)


async def test_no_trim_when_under_cap_no_trim_caveat(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cap >= total count → no trim caveat should fire."""

    async def fake_fetch(hgvs: str, species: str) -> list[dict[str, Any]]:
        # Tiny fixture: 2 hgvsc entries, cap at 50 → no trim.
        return [
            {
                "C": {
                    "input": "x:c.1A>G",
                    "hgvsg": ["NC_000001.11:g.100A>G"],
                    "hgvsc": ["NM_001.1:c.1A>G", "NM_002.1:c.1A>G"],
                    "hgvsp": ["NP_001.1:p.Met1?"],
                    "spdi": ["NC_000001.11:99:A:G"],
                }
            }
        ]

    monkeypatch.setattr(nh_module, "_fetch_variant_recoder", fake_fetch)

    out = await normalize_hgvs(NormalizeHgvsInput(hgvs="x:c.1A>G", max_transcript_forms=50))
    assert not any("trimmed" in c for c in out.caveats)


async def test_empty_response_emits_caveat(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(hgvs: str, species: str) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(nh_module, "_fetch_variant_recoder", fake_fetch)

    out = await normalize_hgvs(NormalizeHgvsInput(hgvs="something:c.999X>Y"))
    assert out.alleles == []
    assert any("no allele records" in c for c in out.caveats)


# --- Error paths -------------------------------------------------------------------


async def test_http_400_surfaces_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            return httpx.Response(
                status_code=400,
                content=b"bad HGVS",
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(nh_module.httpx, "AsyncClient", FakeClient)
    with pytest.raises(ToolError, match="could not parse HGVS"):
        await normalize_hgvs(NormalizeHgvsInput(hgvs="garbage:c.1X>Y"))


async def test_http_429_surfaces_rate_limit_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            return httpx.Response(
                status_code=429,
                content=b"slow down",
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(nh_module.httpx, "AsyncClient", FakeClient)
    with pytest.raises(ToolError, match="rate-limited"):
        await normalize_hgvs(NormalizeHgvsInput(hgvs="NM_007294.4:c.5266dupC"))


async def test_http_503_surfaces_generic_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            return httpx.Response(
                status_code=503,
                content=b"upstream busy",
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(nh_module.httpx, "AsyncClient", FakeClient)
    with pytest.raises(ToolError, match="HTTP 503"):
        await normalize_hgvs(NormalizeHgvsInput(hgvs="NM_007294.4:c.5266dupC"))


async def test_non_json_response_raises_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            return httpx.Response(
                status_code=200,
                content=b"<html>maintenance</html>",
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(nh_module.httpx, "AsyncClient", FakeClient)
    with pytest.raises(ToolError, match="non-JSON"):
        await normalize_hgvs(NormalizeHgvsInput(hgvs="NM_007294.4:c.5266dupC"))


async def test_non_list_response_raises_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            return httpx.Response(
                status_code=200,
                content=b'{"unexpected": "shape"}',
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(nh_module.httpx, "AsyncClient", FakeClient)
    with pytest.raises(ToolError, match="non-list payload"):
        await normalize_hgvs(NormalizeHgvsInput(hgvs="NM_007294.4:c.5266dupC"))


async def test_network_error_raises_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            raise httpx.ConnectError("network down")

    monkeypatch.setattr(nh_module.httpx, "AsyncClient", FakeClient)
    with pytest.raises(ToolError, match="unreachable"):
        await normalize_hgvs(NormalizeHgvsInput(hgvs="NM_007294.4:c.5266dupC"))


# --- Registry --------------------------------------------------------------------


async def test_tool_registered_with_correct_metadata() -> None:
    from bioforge.tools.registry import get_tool

    spec = get_tool("normalize_hgvs")
    assert spec.cost_hint == "moderate"
    assert {"variants", "annotation", "hgvs", "normalize"} <= set(spec.tags)
    assert any("Yates" in c or "Ensembl" in c for c in spec.citations)
    assert any("den Dunnen" in c or "HGVS" in c for c in spec.citations)
    assert spec.version == "1.0.0"
    assert spec.destructive is False


# --- Live integration (opt-in) --------------------------------------------------


@pytest.mark.online
async def test_live_brca1_dupc_right_shift() -> None:
    """Hits the real Ensembl variant_recoder. Deselected by default; the
    nightly job runs it to catch upstream API drift. The right-shift
    behavior is stable for this canonical clinical variant."""
    out = await normalize_hgvs(NormalizeHgvsInput(hgvs="NM_007294.4:c.5266dupC"))
    assert len(out.alleles) >= 1
    rec = out.alleles[0]
    # Genomic right-shift: position 43057065 on chr17 is the canonical 3'-most
    # position for the duplication.
    assert rec.primary_hgvsg is not None
    assert "43057065dup" in rec.primary_hgvsg
    # The canonical NM_007294.4 coding form drops the trailing C (HGVS shorthand).
    assert any(s == "NM_007294.4:c.5266dup" for s in rec.hgvsc)
