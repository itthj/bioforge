"""Tests for fetch_alphafold_structure — Phase 4 slice 1.

Network is never hit. `_fetch_alphafold` is monkeypatched to return a fabricated
metadata dict + a tiny synthetic PDB string with known pLDDT values, so the
parser, the summarizer, the size cap, and the caveat list are all exercised
without an HTTPS call. There IS an opt-in `online` test at the bottom that hits
the real AlphaFold API for BRCA1 — runs only with `pytest -m online`.
"""

from __future__ import annotations

import pydantic
import pytest
from bioforge.tools.base import ToolError
from bioforge.tools.structure import fetch_alphafold as af_module
from bioforge.tools.structure.fetch_alphafold import (
    FetchAlphaFoldInput,
    _parse_plddt_from_pdb,
    _summarize_plddt,
    fetch_alphafold_structure,
)


def _pdb_atom_line(
    *,
    serial: int,
    atom_name: str,
    res_name: str,
    chain: str,
    res_num: int,
    x: float,
    y: float,
    z: float,
    occupancy: float,
    b_factor: float,
    element: str,
) -> str:
    """Build a PDB ATOM record obeying fixed-width column layout.

    Columns (1-indexed):
      1-6   'ATOM  '
      7-11  serial (right-justified)
      13-16 atom name
      18-20 res name
      22    chain
      23-26 res num
      31-38 x  (8.3f)
      39-46 y
      47-54 z
      55-60 occupancy (6.2f)
      61-66 temperatureFactor (pLDDT, 6.2f)
      77-78 element
    """
    return (
        "ATOM  "
        f"{serial:>5d}"
        " "
        f"{atom_name:<4s}"
        " "
        f"{res_name:<3s}"
        " "
        f"{chain}"
        f"{res_num:>4d}"
        "    "
        f"{x:8.3f}"
        f"{y:8.3f}"
        f"{z:8.3f}"
        f"{occupancy:6.2f}"
        f"{b_factor:6.2f}"
        "          "
        f"{element:>2s}"
    )


def _build_fake_pdb(plddt_per_residue: list[float]) -> str:
    """Synthesize a minimal PDB string with one CA atom per residue.

    Each residue also gets a side-chain atom with a different b-factor — the
    parser should ignore those so per-residue pLDDT is unambiguous.
    """
    lines = ["HEADER    PREDICTED MODEL", "REMARK   1 ALPHAFOLD"]
    for i, plddt in enumerate(plddt_per_residue, start=1):
        lines.append(
            _pdb_atom_line(
                serial=2 * i - 1,
                atom_name="CA",
                res_name="ALA",
                chain="A",
                res_num=i,
                x=float(i),
                y=0.0,
                z=0.0,
                occupancy=1.00,
                b_factor=plddt,
                element="C",
            )
        )
        # Side-chain atom with a wildly different b-factor — must be ignored.
        lines.append(
            _pdb_atom_line(
                serial=2 * i,
                atom_name="CB",
                res_name="ALA",
                chain="A",
                res_num=i,
                x=float(i),
                y=1.0,
                z=0.0,
                occupancy=1.00,
                b_factor=999.0,
                element="C",
            )
        )
    lines.append("END")
    return "\n".join(lines) + "\n"


def _fake_metadata(uniprot_id: str = "P38398", **overrides) -> dict:
    base = {
        "entryId": f"AF-{uniprot_id}-F1",
        "gene": "BRCA1",
        "uniprotAccession": uniprot_id,
        "uniprotDescription": "Breast cancer type 1 susceptibility protein",
        "organismScientificName": "Homo sapiens",
        "taxId": 9606,
        "uniprotStart": 1,
        "uniprotEnd": 1863,
        "uniprotSequence": "M" * 1863,
        "pdbUrl": f"https://alphafold.ebi.ac.uk/files/AF-{uniprot_id}-F1-model_v4.pdb",
        "cifUrl": f"https://alphafold.ebi.ac.uk/files/AF-{uniprot_id}-F1-model_v4.cif",
        "bcifUrl": f"https://alphafold.ebi.ac.uk/files/AF-{uniprot_id}-F1-model_v4.bcif",
        "paeImageUrl": f"https://alphafold.ebi.ac.uk/files/AF-{uniprot_id}-F1-predicted_aligned_error_v4.json",
        "paeDocUrl": None,
        "latestVersion": 4,
        "modelCreatedDate": "2022-11-01",
    }
    base.update(overrides)
    return base


@pytest.fixture
def patch_af(monkeypatch):
    """Yields a setter that configures the next `_fetch_alphafold` return value."""
    holder: dict = {"response": None, "calls": []}

    async def _fake(uniprot_id: str):
        holder["calls"].append(uniprot_id)
        resp = holder["response"]
        if isinstance(resp, Exception):
            raise resp
        return resp

    monkeypatch.setattr(af_module, "_fetch_alphafold", _fake)

    def setter(response):
        holder["response"] = response

    setter.calls = holder["calls"]
    return setter


# --- Parser / summarizer -----------------------------------------------------------


def test_parse_plddt_picks_ca_atoms_only() -> None:
    pdb = _build_fake_pdb([85.0, 92.5, 60.0])
    plddt, n = _parse_plddt_from_pdb(pdb)
    assert n == 3
    assert plddt == [85.0, 92.5, 60.0]


def test_parse_plddt_ignores_non_atom_records() -> None:
    pdb_with_junk = (
        "HEADER    JUNK\n"
        "REMARK   1 NOISE\n"
        + _pdb_atom_line(
            serial=1,
            atom_name="CA",
            res_name="GLY",
            chain="A",
            res_num=1,
            x=0.0,
            y=0.0,
            z=0.0,
            occupancy=1.0,
            b_factor=77.7,
            element="C",
        )
        + "\nTER\nEND\n"
    )
    plddt, n = _parse_plddt_from_pdb(pdb_with_junk)
    assert n == 1
    assert plddt == [77.7]


def test_parse_plddt_handles_empty_pdb() -> None:
    plddt, n = _parse_plddt_from_pdb("HEADER ONLY\nEND\n")
    assert (plddt, n) == ([], 0)


def test_summarize_plddt_bins_match_alphafold_definition() -> None:
    # Designed to land exactly one residue in each bin + one boundary case at 90.
    avg, bins = _summarize_plddt([95.0, 90.0, 80.0, 70.0, 60.0, 49.9])
    assert bins == {"very_high": 2, "confident": 2, "low": 1, "very_low": 1}
    # 70 is the lower boundary of "confident"; 49.9 is "very_low" (strict <50).
    assert avg == pytest.approx((95 + 90 + 80 + 70 + 60 + 49.9) / 6, abs=0.01)


def test_summarize_plddt_empty_returns_zeros() -> None:
    avg, bins = _summarize_plddt([])
    assert avg == 0.0
    assert sum(bins.values()) == 0


# --- Tool end-to-end ---------------------------------------------------------------


async def test_fetch_returns_metadata_and_plddt_stats(patch_af) -> None:
    plddt_values = [95.0, 92.0, 88.0, 75.0, 65.0, 45.0, 30.0]
    pdb = _build_fake_pdb(plddt_values)
    patch_af((_fake_metadata("P38398"), pdb))

    out = await fetch_alphafold_structure(FetchAlphaFoldInput(uniprot_id="P38398"))

    assert out.uniprot_id == "P38398"
    assert out.entry_id == "AF-P38398-F1"
    assert out.gene == "BRCA1"
    assert out.organism == "Homo sapiens"
    assert out.length_residues == len(plddt_values)
    assert out.average_plddt == pytest.approx(sum(plddt_values) / len(plddt_values), abs=0.01)
    # Bins: 95,92 in very_high; 88,75 in confident; 65 in low; 45,30 in very_low.
    assert out.plddt_distribution == {
        "very_high": 2,
        "confident": 2,
        "low": 1,
        "very_low": 2,
    }
    # Distribution must sum to length_residues.
    assert sum(out.plddt_distribution.values()) == out.length_residues
    assert out.pdb_text is not None
    assert "ATOM  " in out.pdb_text


async def test_provenance_stamped_via_executor(patch_af) -> None:
    """Provenance fields (tool_name, tool_version, citations) are stamped by
    execute_tool, not the handler itself — so they only show up on the full
    pipeline path."""
    from bioforge.tools.registry import execute_tool

    patch_af((_fake_metadata("P38398"), _build_fake_pdb([85.0] * 4)))
    out = await execute_tool("fetch_alphafold_structure", {"uniprot_id": "P38398"})
    assert out.tool_name == "fetch_alphafold_structure"
    assert out.tool_version == "1.0.0"
    assert any("Jumper" in c for c in out.citations)


async def test_fetch_includes_mandatory_caveats(patch_af) -> None:
    patch_af((_fake_metadata("P04637"), _build_fake_pdb([80.0] * 5)))
    out = await fetch_alphafold_structure(FetchAlphaFoldInput(uniprot_id="P04637"))
    text = " ".join(out.caveats).lower()
    assert "computational" in text
    assert "conformational" in text or "conformational state" in text
    assert "multimer" in text
    # And there's a caveat that says not every entry has a prediction.
    assert "not every uniprot" in text or "alphafold prediction" in text


async def test_fetch_caps_large_pdb_text(patch_af) -> None:
    # 600 residues × ~160 bytes/line × 2 atoms ≈ 192 KB. Cap at 50 KB to force trip.
    big_pdb = _build_fake_pdb([80.0] * 600)
    patch_af((_fake_metadata("PBIGGY"), big_pdb))
    out = await fetch_alphafold_structure(FetchAlphaFoldInput(uniprot_id="PBIGGY", max_pdb_kb=50))
    assert out.pdb_text is None
    assert any("exceeding the 50 KB" in c for c in out.caveats)
    # But length / plddt stats are still returned.
    assert out.length_residues == 600


async def test_fetch_omits_pdb_when_not_requested(patch_af) -> None:
    patch_af((_fake_metadata("P38398"), _build_fake_pdb([85.0] * 10)))
    out = await fetch_alphafold_structure(FetchAlphaFoldInput(uniprot_id="P38398", include_pdb_text=False))
    assert out.pdb_text is None
    # Stats still present.
    assert out.length_residues == 10
    # And no "exceeded the size cap" caveat — the cap wasn't the reason.
    assert not any("KB" in c for c in out.caveats)


async def test_fetch_raises_when_api_returns_404(patch_af) -> None:
    patch_af(ToolError("No AlphaFold prediction available for UniProt 'X' ..."))
    with pytest.raises(ToolError, match="No AlphaFold prediction"):
        await fetch_alphafold_structure(FetchAlphaFoldInput(uniprot_id="XXXXXX"))


async def test_fetch_raises_when_pdb_url_missing(patch_af) -> None:
    """Metadata exists but no pdbUrl — should refuse cleanly."""
    meta_no_pdb = _fake_metadata("P38398", pdbUrl=None)
    patch_af((meta_no_pdb, None))
    with pytest.raises(ToolError, match="no PDB URL"):
        await fetch_alphafold_structure(FetchAlphaFoldInput(uniprot_id="P38398"))


async def test_fetch_raises_on_malformed_pdb(patch_af) -> None:
    """No CA atoms at all → ToolError, not a silent zero-residue result."""
    patch_af((_fake_metadata("P38398"), "HEADER ONLY\nEND\n"))
    with pytest.raises(ToolError, match="Could not parse any CA atoms"):
        await fetch_alphafold_structure(FetchAlphaFoldInput(uniprot_id="P38398"))


# --- Input validation --------------------------------------------------------------


def test_rejects_lowercase_uniprot_id() -> None:
    with pytest.raises(pydantic.ValidationError):
        FetchAlphaFoldInput(uniprot_id="p38398")


def test_rejects_entry_name() -> None:
    # "BRCA1_HUMAN" contains underscore — pattern requires [A-Z0-9]+ only.
    with pytest.raises(pydantic.ValidationError):
        FetchAlphaFoldInput(uniprot_id="BRCA1_HUMAN")


def test_rejects_too_short_uniprot_id() -> None:
    with pytest.raises(pydantic.ValidationError):
        FetchAlphaFoldInput(uniprot_id="P38")


def test_rejects_too_large_max_pdb_kb() -> None:
    with pytest.raises(pydantic.ValidationError):
        FetchAlphaFoldInput(uniprot_id="P38398", max_pdb_kb=999_999)


# --- Registration ------------------------------------------------------------------


def test_tool_is_registered_with_correct_metadata() -> None:
    from bioforge.tools.registry import get_tool

    spec = get_tool("fetch_alphafold_structure")
    assert spec.cost_hint == "cheap"
    assert spec.destructive is False
    assert "structure" in spec.tags
    assert "alphafold" in spec.tags
    assert "protein" in spec.tags
    assert any("AlphaFold" in c for c in spec.citations)


# --- Live test (opt-in) ------------------------------------------------------------


@pytest.mark.online
async def test_fetch_brca1_from_real_api() -> None:
    """Live AlphaFold API. Marked online — run with `pytest -m online`.

    BRCA1 (P38398) is the canonical Phase 4 test target: long protein, known
    structure, well-curated prediction. We assert metadata shape, not specific
    pLDDT values (those are model-version-dependent).
    """
    out = await fetch_alphafold_structure(FetchAlphaFoldInput(uniprot_id="P38398", max_pdb_kb=5000))
    assert out.entry_id.startswith("AF-P38398")
    assert out.organism == "Homo sapiens"
    assert out.length_residues > 1500  # BRCA1 is 1863 residues
    assert 0.0 <= out.average_plddt <= 100.0
    assert sum(out.plddt_distribution.values()) == out.length_residues
    assert out.pdb_text is not None
    assert out.pdb_text.startswith("HEADER") or "ATOM" in out.pdb_text[:500]
