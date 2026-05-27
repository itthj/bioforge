"""Tests for fetch_interpro_domains."""

from __future__ import annotations

import pydantic
import pytest
from bioforge.tools.structure import fetch_interpro as ip_module
from bioforge.tools.structure.fetch_interpro import (
    FetchInterproInput,
    _parse_entries,
    fetch_interpro_domains,
)


def _entry(
    *,
    accession: str,
    name: str,
    entry_type: str,
    uniprot_id: str = "P38398",
    fragments: list[tuple[int, int]] | None = None,
) -> dict:
    fragments = fragments or [(1, 100)]
    return {
        "metadata": {
            "accession": accession,
            "name": name,
            "type": entry_type,
            "source_database": "interpro",
        },
        "proteins": [
            {
                "accession": uniprot_id,
                "name": f"{accession}_HUMAN",
                "entry_protein_locations": [
                    {"fragments": [{"start": s, "end": e, "dc-status": "CONTINUOUS"} for s, e in fragments]}
                ],
            }
        ],
    }


@pytest.fixture
def patch_ip(monkeypatch):
    holder: dict = {"response": [], "calls": []}

    async def _fake(uniprot_id: str):
        holder["calls"].append(uniprot_id)
        resp = holder["response"]
        if isinstance(resp, Exception):
            raise resp
        return resp

    monkeypatch.setattr(ip_module, "_fetch_interpro", _fake)

    def setter(response):
        holder["response"] = response

    setter.calls = holder["calls"]
    return setter


# --- Parser -------------------------------------------------------------------------


def test_parser_extracts_domain_with_single_region() -> None:
    results = [_entry(accession="IPR001357", name="BRCT", entry_type="domain", fragments=[(1646, 1736)])]
    domains = _parse_entries(results, "P38398")
    assert len(domains) == 1
    assert domains[0].interpro_id == "IPR001357"
    assert domains[0].name == "BRCT"
    assert domains[0].type == "domain"
    assert len(domains[0].regions) == 1
    assert domains[0].regions[0].start == 1646
    assert domains[0].regions[0].end == 1736


def test_parser_handles_multiple_regions_per_entry() -> None:
    """A repeat-type entry can hit the protein in multiple places."""
    results = [
        _entry(
            accession="IPR001478",
            name="PDZ",
            entry_type="repeat",
            fragments=[(100, 200), (300, 400), (500, 600)],
        )
    ]
    domains = _parse_entries(results, "P38398")
    assert len(domains) == 1
    assert len(domains[0].regions) == 3
    assert [r.start for r in domains[0].regions] == [100, 300, 500]


def test_parser_skips_unknown_entry_types() -> None:
    """Only domain / family / homologous_superfamily / repeat / active_site /
    binding_site / conserved_site / ptm are surfaced."""
    results = [
        _entry(accession="IPR001357", name="BRCT", entry_type="domain"),
        _entry(accession="IPR999999", name="garbage", entry_type="random_unknown_type"),
    ]
    domains = _parse_entries(results, "P38398")
    assert len(domains) == 1
    assert domains[0].interpro_id == "IPR001357"


def test_parser_skips_entries_with_no_regions() -> None:
    bad = _entry(accession="IPR000001", name="empty", entry_type="domain")
    bad["proteins"][0]["entry_protein_locations"] = []
    domains = _parse_entries([bad], "P38398")
    assert domains == []


def test_parser_filters_by_uniprot_id() -> None:
    """Defensive: if InterPro returns data for some other protein, skip it."""
    results = [_entry(accession="IPR001357", name="BRCT", entry_type="domain", uniprot_id="OTHERID1")]
    domains = _parse_entries(results, "P38398")
    assert domains == []


def test_parser_handles_malformed_payload() -> None:
    """Garbage in, empty out — no exceptions."""
    results = [
        None,  # type: ignore[list-item]
        {"metadata": "not a dict"},
        {"metadata": {"accession": "IPR1", "type": "domain"}, "proteins": "not a list"},
    ]
    domains = _parse_entries(results, "P38398")  # type: ignore[arg-type]
    assert domains == []


# --- Tool end-to-end ----------------------------------------------------------------


async def test_fetch_returns_domain_list(patch_ip) -> None:
    patch_ip(
        [
            _entry(accession="IPR001357", name="BRCT", entry_type="domain", fragments=[(1646, 1736)]),
            _entry(accession="IPR025202", name="BRCA1 zinc finger", entry_type="domain", fragments=[(24, 64)]),
        ]
    )
    out = await fetch_interpro_domains(FetchInterproInput(uniprot_id="P38398"))
    assert out.uniprot_id == "P38398"
    assert len(out.domains) == 2
    assert out.num_entries == 2
    # Caveat about predictions is always present.
    assert any("predicted" in c.lower() for c in out.caveats)


async def test_fetch_empty_adds_no_results_caveat(patch_ip) -> None:
    patch_ip([])
    out = await fetch_interpro_domains(FetchInterproInput(uniprot_id="Q9NRP7"))
    assert out.domains == []
    assert any("no interpro entries" in c.lower() for c in out.caveats)


async def test_fetch_truncates_to_max_domains(patch_ip) -> None:
    many = [_entry(accession=f"IPR{i:06d}", name=f"e{i}", entry_type="domain") for i in range(60)]
    patch_ip(many)
    out = await fetch_interpro_domains(
        FetchInterproInput(uniprot_id="P38398", max_domains=10),
    )
    assert len(out.domains) == 10
    assert any("Truncated" in c for c in out.caveats)


async def test_fetch_provenance_via_executor(patch_ip) -> None:
    from bioforge.tools.registry import execute_tool

    patch_ip([_entry(accession="IPR001357", name="BRCT", entry_type="domain")])
    out = await execute_tool("fetch_interpro_domains", {"uniprot_id": "P38398"})
    assert out.tool_name == "fetch_interpro_domains"
    assert out.tool_version == "1.0.0"
    assert any("InterPro" in c for c in out.citations)


# --- Input validation ---------------------------------------------------------------


def test_rejects_invalid_uniprot_id() -> None:
    with pytest.raises(pydantic.ValidationError):
        FetchInterproInput(uniprot_id="brca1_human")


def test_rejects_too_many_max_domains() -> None:
    with pytest.raises(pydantic.ValidationError):
        FetchInterproInput(uniprot_id="P38398", max_domains=500)


# --- Registration -------------------------------------------------------------------


def test_tool_registered() -> None:
    from bioforge.tools.registry import get_tool

    spec = get_tool("fetch_interpro_domains")
    assert spec.cost_hint == "cheap"
    assert "interpro" in spec.tags
    assert "annotation" in spec.tags


@pytest.mark.online
async def test_brca1_returns_real_domains() -> None:
    """Live InterPro call — BRCA1 has well-annotated BRCT domains. Run with -m online."""
    out = await fetch_interpro_domains(FetchInterproInput(uniprot_id="P38398"))
    assert len(out.domains) > 0
    # BRCA1 has BRCT domains at the C-terminus.
    domain_names = " ".join(d.name.lower() for d in out.domains)
    assert "brct" in domain_names or "brca" in domain_names
