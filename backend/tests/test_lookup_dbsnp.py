"""Tests for lookup_dbsnp.

NCBI is never hit in the hot suite. `_fetch_dbsnp` is monkeypatched and
returns the committed rs334 (HBB sickle) fixture. One @pytest.mark.online
test exercises the live NCBI endpoint — runs on the nightly job to catch
upstream API drift.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pydantic
import pytest
from bioforge.tools.base import ToolError
from bioforge.tools.variants import lookup_dbsnp as ld_module
from bioforge.tools.variants.lookup_dbsnp import (
    LookupDbsnpInput,
    _map_record,
    _normalize_snp_id,
    _parse_chrpos,
    _parse_freq_token,
    _pick_minor_allele,
    _split_comma,
    lookup_dbsnp,
)

FIXTURE = Path(__file__).parent / "fixtures" / "dbsnp_rs334.json"


def _load_fixture() -> dict[str, Any]:
    with FIXTURE.open("r", encoding="utf-8") as f:
        return json.load(f)


def _rs334_entry() -> dict[str, Any]:
    return _load_fixture()["result"]["334"]


# --- Input validation --------------------------------------------------------------


def test_input_accepts_rs_prefix() -> None:
    inp = LookupDbsnpInput(query="rs334")
    assert inp.query == "rs334"


def test_input_accepts_bare_digits() -> None:
    inp = LookupDbsnpInput(query="334")
    assert inp.query == "334"


def test_input_accepts_case_insensitive_prefix() -> None:
    LookupDbsnpInput(query="RS334")
    LookupDbsnpInput(query="Rs334")


def test_input_strips_whitespace() -> None:
    inp = LookupDbsnpInput(query="  rs334  ")
    assert inp.query == "rs334"


def test_input_rejects_empty() -> None:
    with pytest.raises(pydantic.ValidationError):
        LookupDbsnpInput(query="")


def test_input_rejects_non_rsid_form() -> None:
    with pytest.raises(pydantic.ValidationError, match="dbSNP rsid"):
        LookupDbsnpInput(query="chr11:5227002:T:A")


def test_input_rejects_letters_only() -> None:
    with pytest.raises(pydantic.ValidationError, match="dbSNP rsid"):
        LookupDbsnpInput(query="HBB")


def test_input_rejects_sql_injection_attempt() -> None:
    with pytest.raises(pydantic.ValidationError):
        LookupDbsnpInput(query="334; DROP TABLE variants;--")


# --- _normalize_snp_id -------------------------------------------------------------


def test_normalize_strips_rs_prefix() -> None:
    assert _normalize_snp_id("rs334") == "334"
    assert _normalize_snp_id("RS334") == "334"
    assert _normalize_snp_id("334") == "334"


def test_normalize_preserves_whitespace_stripping() -> None:
    assert _normalize_snp_id("  rs334  ") == "334"


# --- _parse_freq_token -------------------------------------------------------------


def test_parse_freq_token_canonical() -> None:
    assert _parse_freq_token("A=0.027356/137") == ("A", 0.027356, 137)


def test_parse_freq_token_zero_count_sentinel() -> None:
    """NCBI uses 'A=0./0' for zero-count entries — keep them as valid 0-frequency."""
    parsed = _parse_freq_token("A=0./0")
    assert parsed is not None
    assert parsed[0] == "A"
    assert parsed[1] == 0.0
    assert parsed[2] == 0


def test_parse_freq_token_without_count() -> None:
    parsed = _parse_freq_token("T=0.5")
    assert parsed == ("T", 0.5, None)


def test_parse_freq_token_multi_char_allele() -> None:
    """Indels can carry multi-char alleles in dbSNP."""
    assert _parse_freq_token("AT=0.01/5") == ("AT", 0.01, 5)


def test_parse_freq_token_malformed_returns_none() -> None:
    assert _parse_freq_token("garbage") is None
    assert _parse_freq_token("A=") is None
    assert _parse_freq_token("=0.5") is None


# --- _parse_chrpos / _split_comma --------------------------------------------------


def test_parse_chrpos_canonical() -> None:
    assert _parse_chrpos("11:5227002") == ("11", 5227002)


def test_parse_chrpos_x_chromosome() -> None:
    assert _parse_chrpos("X:12345") == ("X", 12345)


def test_parse_chrpos_empty() -> None:
    assert _parse_chrpos(None) == (None, None)
    assert _parse_chrpos("") == (None, None)


def test_parse_chrpos_malformed() -> None:
    assert _parse_chrpos("no colon here") == (None, None)


def test_split_comma_normal() -> None:
    assert _split_comma("a,b,c") == ["a", "b", "c"]


def test_split_comma_drops_empties_and_whitespace() -> None:
    assert _split_comma("a, ,b, c ,") == ["a", "b", "c"]


def test_split_comma_none() -> None:
    assert _split_comma(None) == []
    assert _split_comma("") == []


# --- _pick_minor_allele ------------------------------------------------------------


def test_pick_minor_allele_prefers_1000genomes() -> None:
    from bioforge.tools.variants.lookup_dbsnp import PopulationFrequency

    freqs = [
        PopulationFrequency(study="ALFA", allele="A", frequency=0.001, sample_size=10),
        PopulationFrequency(study="1000Genomes", allele="T", frequency=0.05, sample_size=100),
        PopulationFrequency(study="GnomAD_genomes", allele="A", frequency=0.01, sample_size=50),
    ]
    allele, freq, source = _pick_minor_allele(freqs)
    assert allele == "T"
    assert freq == 0.05
    assert source == "1000Genomes"


def test_pick_minor_allele_skips_zero_frequencies() -> None:
    from bioforge.tools.variants.lookup_dbsnp import PopulationFrequency

    freqs = [
        PopulationFrequency(study="1000Genomes", allele="A", frequency=0.0, sample_size=0),
        PopulationFrequency(study="GnomAD_genomes", allele="C", frequency=0.02, sample_size=200),
    ]
    allele, freq, source = _pick_minor_allele(freqs)
    assert allele == "C"
    assert source == "GnomAD_genomes"


def test_pick_minor_allele_empty_returns_nones() -> None:
    assert _pick_minor_allele([]) == (None, None, None)


def test_pick_minor_allele_falls_back_to_first_non_zero() -> None:
    from bioforge.tools.variants.lookup_dbsnp import PopulationFrequency

    freqs = [
        PopulationFrequency(study="ExoticStudy", allele="G", frequency=0.3, sample_size=10),
    ]
    allele, freq, source = _pick_minor_allele(freqs)
    assert allele == "G"
    assert source == "ExoticStudy"


# --- _map_record full conversion on real rs334 fixture ----------------------------


def test_map_record_rs334_canonical_shape() -> None:
    rec = _map_record("334", _rs334_entry())
    assert rec.rsid == "rs334"
    assert rec.snp_id == "334"
    assert rec.variant_class == "snv"
    assert rec.chromosome == "11"
    assert rec.position_grch38 == 5227002
    assert rec.position_grch37 == 5248232
    assert rec.dbsnp_url == "https://www.ncbi.nlm.nih.gov/snp/rs334"


def test_map_record_rs334_genes() -> None:
    rec = _map_record("334", _rs334_entry())
    assert len(rec.genes) == 1
    assert rec.genes[0].symbol == "HBB"
    assert rec.genes[0].gene_id == "3043"


def test_map_record_rs334_functional_class() -> None:
    rec = _map_record("334", _rs334_entry())
    assert "missense_variant" in rec.functional_class
    assert "coding_sequence_variant" in rec.functional_class


def test_map_record_rs334_clinical_significance_tags() -> None:
    rec = _map_record("334", _rs334_entry())
    # rs334 is the sickle variant — pathogenic AND protective (malaria resistance).
    assert "pathogenic" in rec.clinical_significance
    assert "protective" in rec.clinical_significance
    assert "likely-benign" in rec.clinical_significance


def test_map_record_rs334_spdi_multi_allelic() -> None:
    rec = _map_record("334", _rs334_entry())
    # rs334 is a multi-allelic site at 11:5227002 — T>A, T>C, T>G.
    assert len(rec.spdi) == 3
    assert "NC_000011.10:5227001:T:A" in rec.spdi


def test_map_record_rs334_population_frequencies_parsed() -> None:
    rec = _map_record("334", _rs334_entry())
    studies = {pf.study for pf in rec.population_frequencies}
    assert "1000Genomes" in studies
    assert "GnomAD_genomes" in studies
    assert "ALFA" in studies
    # The zero-count 'PRJEB36033' entry should still be parsed (not dropped).
    assert "PRJEB36033" in studies


def test_map_record_rs334_thousand_genomes_freq() -> None:
    """1000Genomes reports A at 137/5008 chromosomes ≈ 2.74% — sickle is rare globally."""
    rec = _map_record("334", _rs334_entry())
    tg = next(pf for pf in rec.population_frequencies if pf.study == "1000Genomes")
    assert tg.allele == "A"
    assert abs(tg.frequency - 0.027356) < 1e-9
    assert tg.sample_size == 137


def test_map_record_rs334_minor_allele_pick() -> None:
    rec = _map_record("334", _rs334_entry())
    # 1000Genomes is in _PREFERRED_STUDIES and has non-zero A freq.
    assert rec.minor_allele == "A"
    assert rec.minor_allele_source_study == "1000Genomes"
    assert rec.minor_allele_frequency is not None
    assert abs(rec.minor_allele_frequency - 0.027356) < 1e-9


def test_map_record_rs334_preserves_raw_docsum() -> None:
    rec = _map_record("334", _rs334_entry())
    assert rec.raw_docsum is not None
    # docsum contains the HBB protein change.
    assert "p.Glu7Val" in rec.raw_docsum


# --- End-to-end (monkeypatched fetcher) ---------------------------------------------


async def test_end_to_end_rs334_with_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_ids: list[str] = []

    async def fake_fetch(snp_id: str) -> dict[str, Any]:
        captured_ids.append(snp_id)
        return _rs334_entry()

    monkeypatch.setattr(ld_module, "_fetch_dbsnp", fake_fetch)

    out = await lookup_dbsnp(LookupDbsnpInput(query="rs334"))

    assert captured_ids == ["334"]  # 'rs' prefix stripped before fetch
    assert out.query == "rs334"  # preserves user input form
    assert out.record.rsid == "rs334"
    assert out.record.genes[0].symbol == "HBB"
    assert out.record.minor_allele == "A"
    # Caveats include the four base entries.
    assert len(out.caveats) >= 4


async def test_end_to_end_bare_digits(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(snp_id: str) -> dict[str, Any]:
        return _rs334_entry()

    monkeypatch.setattr(ld_module, "_fetch_dbsnp", fake_fetch)

    out = await lookup_dbsnp(LookupDbsnpInput(query="334"))
    assert out.query == "334"
    assert out.record.rsid == "rs334"  # canonical form in record


# --- Error paths --------------------------------------------------------------------


async def test_fetch_unknown_rsid_raises_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """NCBI returns no entry under result.{snp_id} for unknown IDs — surface as ToolError."""
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
                content=b'{"header":{"type":"esummary","version":"0.3"},"result":{"uids":["99999999999"]}}',
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(ld_module.httpx, "AsyncClient", FakeClient)
    with pytest.raises(ToolError, match="no record"):
        await lookup_dbsnp(LookupDbsnpInput(query="rs99999999999"))


async def test_fetch_http_503_raises_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
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
                status_code=503,
                content=b"upstream busy",
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(ld_module.httpx, "AsyncClient", FakeClient)
    with pytest.raises(ToolError, match="HTTP 503"):
        await lookup_dbsnp(LookupDbsnpInput(query="rs334"))


async def test_fetch_non_json_raises_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
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
                content=b"<html>NCBI maintenance</html>",
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(ld_module.httpx, "AsyncClient", FakeClient)
    with pytest.raises(ToolError, match="non-JSON"):
        await lookup_dbsnp(LookupDbsnpInput(query="rs334"))


async def test_fetch_network_error_raises_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            raise httpx.ConnectError("network unreachable")

    monkeypatch.setattr(ld_module.httpx, "AsyncClient", FakeClient)
    with pytest.raises(ToolError, match="unreachable"):
        await lookup_dbsnp(LookupDbsnpInput(query="rs334"))


async def test_ncbi_error_field_raises_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """NCBI puts per-record errors inside result.{snp_id}.error — propagate to ToolError."""
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
                content=b'{"result":{"uids":["334"],"334":{"uid":"334","error":"Invalid ID"}}}',
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(ld_module.httpx, "AsyncClient", FakeClient)
    with pytest.raises(ToolError, match="Invalid ID"):
        await lookup_dbsnp(LookupDbsnpInput(query="rs334"))


# --- BIOFORGE_ENTREZ_EMAIL behavior -------------------------------------------------


async def test_entrez_email_unset_adds_caveat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ld_module.settings, "entrez_email", "", raising=False)

    async def fake_fetch(snp_id: str) -> dict[str, Any]:
        return _rs334_entry()

    monkeypatch.setattr(ld_module, "_fetch_dbsnp", fake_fetch)

    out = await lookup_dbsnp(LookupDbsnpInput(query="rs334"))
    assert any("BIOFORGE_ENTREZ_EMAIL" in c for c in out.caveats)


async def test_entrez_email_set_omits_caveat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ld_module.settings, "entrez_email", "user@example.com", raising=False)

    async def fake_fetch(snp_id: str) -> dict[str, Any]:
        return _rs334_entry()

    monkeypatch.setattr(ld_module, "_fetch_dbsnp", fake_fetch)

    out = await lookup_dbsnp(LookupDbsnpInput(query="rs334"))
    assert not any("BIOFORGE_ENTREZ_EMAIL" in c for c in out.caveats)


# --- Registry ---------------------------------------------------------------------


def test_tool_registered_with_correct_metadata() -> None:
    from bioforge.tools.registry import get_tool

    spec = get_tool("lookup_dbsnp")
    assert spec.cost_hint == "moderate"
    assert {"variants", "annotation", "dbsnp"} <= set(spec.tags)
    assert any("Sherry" in c or "dbSNP" in c for c in spec.citations)
    assert any("E-utilities" in c or "Sayers" in c for c in spec.citations)
    assert spec.version == "1.0.0"
    assert spec.destructive is False


# --- Live integration (opt-in) ----------------------------------------------------


@pytest.mark.online
async def test_live_rs334_lookup_returns_hbb_sickle() -> None:
    """Hits the real NCBI dbSNP for rs334. Deselected by default; nightly online job runs it."""
    out = await lookup_dbsnp(LookupDbsnpInput(query="rs334"))
    rec = out.record
    assert rec.rsid == "rs334"
    assert any(g.symbol == "HBB" for g in rec.genes)
    assert rec.chromosome == "11"
    assert "missense_variant" in rec.functional_class
    # 1000Genomes A frequency is stable around 2.7%.
    tg_freqs = [pf for pf in rec.population_frequencies if pf.study == "1000Genomes"]
    assert tg_freqs, "1000Genomes population frequency should be present"
