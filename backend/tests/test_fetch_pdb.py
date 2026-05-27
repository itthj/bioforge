"""Tests for fetch_pdb_structure — Phase 4 slice 3.

Network is never hit. `_fetch_pdb` is monkeypatched to return a synthesized
RCSB metadata dict + a fabricated PDB string with known chains, ligands, and
B-factors, so every parse branch is exercised offline. An opt-in online test
hits the real RCSB API for 4HHB (hemoglobin — classic, small, stable entry).
"""

from __future__ import annotations

import pydantic
import pytest
from bioforge.tools.base import ToolError
from bioforge.tools.structure import fetch_pdb as pdb_module
from bioforge.tools.structure.fetch_pdb import (
    FetchPdbInput,
    _build_caveats,
    _extract_metadata_fields,
    _parse_pdb_structure_stats,
    fetch_pdb_structure,
)


def _pdb_atom_line(
    *,
    record_name: str = "ATOM  ",
    serial: int,
    atom_name: str,
    res_name: str,
    chain: str,
    res_num: int,
    b_factor: float,
    element: str = "C",
) -> str:
    """Build one fixed-width PDB ATOM or HETATM line. We don't care about XYZ
    here — only chain, residue name, atom name, B-factor."""
    return (
        f"{record_name:<6s}"
        f"{serial:>5d}"
        " "
        f"{atom_name:<4s}"
        " "
        f"{res_name:<3s}"
        " "
        f"{chain}"
        f"{res_num:>4d}"
        "    "
        f"{0.0:8.3f}"
        f"{0.0:8.3f}"
        f"{0.0:8.3f}"
        f"{1.00:6.2f}"
        f"{b_factor:6.2f}"
        "          "
        f"{element:>2s}"
    )


def _build_fake_pdb(
    *,
    chains: dict[str, int],
    b_factors: list[float] | None = None,
    ligands: list[str] | None = None,
    waters: int = 0,
) -> str:
    """Construct a minimal PDB with the requested chains, residue counts,
    ligand HETATMs, and water HETATMs.

    `b_factors` (if given) must have length sum(chains.values()) — one per CA.
    If not given, every CA gets 25.0.
    """
    total_residues = sum(chains.values())
    if b_factors is None:
        b_factors = [25.0] * total_residues
    if len(b_factors) != total_residues:
        raise ValueError("b_factors length must match total residues")

    lines = ["HEADER    TEST STRUCTURE", "TITLE     SYNTHETIC FIXTURE"]
    serial = 1
    b_idx = 0
    for chain, n_res in chains.items():
        for res_num in range(1, n_res + 1):
            lines.append(
                _pdb_atom_line(
                    serial=serial,
                    atom_name="N",
                    res_name="ALA",
                    chain=chain,
                    res_num=res_num,
                    b_factor=999.0,  # non-CA, must be ignored
                )
            )
            serial += 1
            lines.append(
                _pdb_atom_line(
                    serial=serial,
                    atom_name="CA",
                    res_name="ALA",
                    chain=chain,
                    res_num=res_num,
                    b_factor=b_factors[b_idx],
                )
            )
            serial += 1
            b_idx += 1
        lines.append("TER")

    for lig in ligands or []:
        lines.append(
            _pdb_atom_line(
                record_name="HETATM",
                serial=serial,
                atom_name="FE",
                res_name=lig,
                chain="A",
                res_num=999,
                b_factor=30.0,
                element="FE",
            )
        )
        serial += 1
    for w in range(waters):
        lines.append(
            _pdb_atom_line(
                record_name="HETATM",
                serial=serial,
                atom_name="O",
                res_name="HOH",
                chain="A",
                res_num=1000 + w,
                b_factor=40.0,
                element="O",
            )
        )
        serial += 1
    lines.append("END")
    return "\n".join(lines) + "\n"


def _fake_metadata(
    pdb_id: str = "4HHB",
    *,
    title: str = "STRUCTURE OF HUMAN DEOXYHAEMOGLOBIN",
    method: str = "X-RAY DIFFRACTION",
    resolution: float | None = 1.74,
    deposit_date: str = "1984-03-07",
    release_date: str = "1984-07-17",
    revision_date: str = "2024-10-30",
    keywords: str = "OXYGEN TRANSPORT, HEMOGLOBIN",
) -> dict:
    return {
        "rcsb_id": pdb_id,
        "struct": {"title": title},
        "rcsb_entry_info": {
            "resolution_combined": [resolution] if resolution is not None else None,
            "experimental_method": method,
        },
        "exptl": [{"method": method}],
        "rcsb_accession_info": {
            "deposit_date": deposit_date,
            "initial_release_date": release_date,
            "revision_date": revision_date,
        },
        "struct_keywords": {"text": keywords, "pdbx_keywords": "OXYGEN TRANSPORT"},
    }


@pytest.fixture
def patch_pdb(monkeypatch):
    """Patches _fetch_pdb. Accepts both legacy 2-tuples (meta, text) — which
    are auto-padded to (meta, text, "pdb") — and explicit 3-tuples for the
    CIF-fallback path."""
    holder: dict = {"response": None, "calls": []}

    async def _fake(pdb_id: str):
        holder["calls"].append(pdb_id)
        resp = holder["response"]
        if isinstance(resp, Exception):
            raise resp
        if isinstance(resp, tuple) and len(resp) == 2:
            # Legacy shape — pad with default format.
            meta, text = resp
            return meta, text, "pdb"
        return resp

    monkeypatch.setattr(pdb_module, "_fetch_pdb", _fake)

    def setter(response):
        holder["response"] = response

    setter.calls = holder["calls"]
    return setter


# --- Parser ----------------------------------------------------------------------


def test_parser_counts_chains_and_residues() -> None:
    pdb = _build_fake_pdb(chains={"A": 5, "B": 3, "C": 2})
    stats = _parse_pdb_structure_stats(pdb)
    assert stats["chain_ids"] == ["A", "B", "C"]
    assert stats["num_chains"] == 3
    assert stats["num_residues"] == 10
    assert stats["residues_per_chain"] == {"A": 5, "B": 3, "C": 2}


def test_parser_skips_water_in_ligand_list() -> None:
    pdb = _build_fake_pdb(chains={"A": 2}, ligands=["HEM", "ATP"], waters=5)
    stats = _parse_pdb_structure_stats(pdb)
    assert "HEM" in stats["ligand_ids"]
    assert "ATP" in stats["ligand_ids"]
    assert "HOH" not in stats["ligand_ids"]
    assert len(stats["ligand_ids"]) == 2


def test_parser_computes_mean_b_factor_from_ca_only() -> None:
    # 3 CA atoms with B = 10, 20, 30. Non-CA atoms have B=999 and MUST be excluded.
    pdb = _build_fake_pdb(chains={"A": 3}, b_factors=[10.0, 20.0, 30.0])
    stats = _parse_pdb_structure_stats(pdb)
    assert stats["mean_b_factor"] == pytest.approx(20.0, abs=0.01)


def test_parser_handles_no_atoms() -> None:
    stats = _parse_pdb_structure_stats("HEADER ONLY\nEND\n")
    assert stats["num_chains"] == 0
    assert stats["num_residues"] == 0
    assert stats["mean_b_factor"] is None
    assert stats["ligand_ids"] == []


# --- Metadata extraction --------------------------------------------------------


def test_metadata_extraction_pulls_core_fields() -> None:
    meta = _fake_metadata(resolution=2.10)
    fields = _extract_metadata_fields(meta)
    assert fields["title"] == "STRUCTURE OF HUMAN DEOXYHAEMOGLOBIN"
    assert fields["experimental_method"] == "X-RAY DIFFRACTION"
    assert fields["resolution_angstrom"] == 2.10
    assert fields["deposit_date"] == "1984-03-07"
    assert "OXYGEN TRANSPORT" in fields["keywords"]


def test_metadata_extraction_handles_missing_resolution() -> None:
    meta = _fake_metadata(method="SOLUTION NMR", resolution=None)
    fields = _extract_metadata_fields(meta)
    assert fields["resolution_angstrom"] is None
    assert fields["experimental_method"] == "SOLUTION NMR"


def test_metadata_extraction_tolerates_empty_dict() -> None:
    fields = _extract_metadata_fields({})
    assert fields["title"] is None
    assert fields["experimental_method"] is None
    assert fields["resolution_angstrom"] is None


# --- Caveat generation ----------------------------------------------------------


def test_caveats_for_xray_low_resolution_warn_side_chains() -> None:
    fields = _extract_metadata_fields(_fake_metadata(method="X-RAY DIFFRACTION", resolution=3.4))
    stats = _parse_pdb_structure_stats(_build_fake_pdb(chains={"A": 5}))
    caveats = _build_caveats(fields, stats)
    joined = " ".join(caveats).lower()
    assert "3.40" in " ".join(caveats) or "side-chain" in joined
    assert "crystal contacts" in joined


def test_caveats_for_cryo_em_mention_local_resolution() -> None:
    fields = _extract_metadata_fields(_fake_metadata(method="ELECTRON MICROSCOPY", resolution=3.8))
    stats = _parse_pdb_structure_stats(_build_fake_pdb(chains={"A": 5}))
    caveats = _build_caveats(fields, stats)
    joined = " ".join(caveats).lower()
    assert "cryo-em" in joined or "local resolution" in joined


def test_caveats_for_nmr_mention_ensemble() -> None:
    fields = _extract_metadata_fields(_fake_metadata(method="SOLUTION NMR", resolution=None))
    stats = _parse_pdb_structure_stats(_build_fake_pdb(chains={"A": 5}))
    caveats = _build_caveats(fields, stats)
    joined = " ".join(caveats).lower()
    assert "ensemble" in joined or "solution" in joined


def test_caveats_mention_multiple_chains() -> None:
    fields = _extract_metadata_fields(_fake_metadata())
    stats = _parse_pdb_structure_stats(_build_fake_pdb(chains={"A": 5, "B": 5}))
    caveats = _build_caveats(fields, stats)
    assert any("2 chains" in c for c in caveats)


# --- Tool end-to-end ------------------------------------------------------------


async def test_fetch_pdb_returns_metadata_and_stats(patch_pdb) -> None:
    pdb_text = _build_fake_pdb(
        chains={"A": 141, "B": 146, "C": 141, "D": 146},
        ligands=["HEM"],
        waters=10,
    )
    patch_pdb((_fake_metadata("4HHB"), pdb_text))

    out = await fetch_pdb_structure(FetchPdbInput(pdb_id="4hhb"))

    # Input is case-insensitive, output is normalized to upper.
    assert out.pdb_id == "4HHB"
    assert out.title == "STRUCTURE OF HUMAN DEOXYHAEMOGLOBIN"
    assert out.experimental_method == "X-RAY DIFFRACTION"
    assert out.resolution_angstrom == 1.74
    assert out.num_chains == 4
    assert out.chain_ids == ["A", "B", "C", "D"]
    assert out.num_residues == 141 + 146 + 141 + 146
    assert out.residues_per_chain == {"A": 141, "B": 146, "C": 141, "D": 146}
    assert "HEM" in out.ligand_ids
    assert "HOH" not in out.ligand_ids
    assert out.pdb_text is not None
    assert "ATOM  " in out.pdb_text
    assert out.pdb_url.endswith("/4HHB.pdb")
    assert out.cif_url.endswith("/4HHB.cif")
    # Multi-chain caveat fires.
    assert any("4 chains" in c for c in out.caveats)


async def test_provenance_stamped_via_executor(patch_pdb) -> None:
    from bioforge.tools.registry import execute_tool

    patch_pdb((_fake_metadata("4HHB"), _build_fake_pdb(chains={"A": 5})))
    out = await execute_tool("fetch_pdb_structure", {"pdb_id": "4HHB"})
    assert out.tool_name == "fetch_pdb_structure"
    assert out.tool_version == "1.0.0"
    assert any("Berman" in c or "RCSB" in c for c in out.citations)


async def test_fetch_pdb_caps_large_text(patch_pdb) -> None:
    big = _build_fake_pdb(chains={"A": 800})  # ~150 KB
    patch_pdb((_fake_metadata("BIGX"), big))
    out = await fetch_pdb_structure(FetchPdbInput(pdb_id="BIGX", max_pdb_kb=20))
    assert out.pdb_text is None
    assert any("exceeding the 20 KB" in c for c in out.caveats)
    assert out.num_residues == 800  # stats still computed before cap is applied


async def test_fetch_pdb_omits_text_when_not_requested(patch_pdb) -> None:
    patch_pdb((_fake_metadata("4HHB"), _build_fake_pdb(chains={"A": 5})))
    out = await fetch_pdb_structure(
        FetchPdbInput(pdb_id="4HHB", include_pdb_text=False),
    )
    assert out.pdb_text is None
    assert out.num_residues == 5
    assert not any("KB" in c for c in out.caveats)


async def test_fetch_pdb_404_raises_with_actionable_message(patch_pdb) -> None:
    patch_pdb(ToolError("No PDB entry found for ID 'XXXX' ..."))
    with pytest.raises(ToolError, match="No PDB entry"):
        await fetch_pdb_structure(FetchPdbInput(pdb_id="XXXX"))


async def test_fetch_pdb_text_unavailable_raises(patch_pdb) -> None:
    """Metadata exists, but BOTH the PDB and the CIF file URLs 404'd.
    This is rare — most entries have at least mmCIF — but should produce a
    clean error rather than crashing or returning a structureless result."""
    patch_pdb((_fake_metadata("HUGE"), None))
    with pytest.raises(ToolError, match="neither the PDB nor the mmCIF"):
        await fetch_pdb_structure(FetchPdbInput(pdb_id="HUGE"))


async def test_fetch_pdb_falls_back_to_cif_when_pdb_404(patch_pdb) -> None:
    """When the .pdb endpoint 404s and the .cif endpoint serves, we return
    the CIF text + format='cif' + a caveat that fine-grained stats aren't
    computed for CIF in this version."""
    fake_cif = "data_4HHB\n_entity.id 1\n# minimal CIF body for test\n"
    patch_pdb((_fake_metadata("RIBO"), fake_cif, "cif"))
    out = await fetch_pdb_structure(FetchPdbInput(pdb_id="RIBO"))
    assert out.structure_format == "cif"
    assert out.cif_text == fake_cif
    assert out.pdb_text is None
    # Stats fields are None / 0 for CIF-only results.
    assert out.num_residues == 0
    assert out.chain_ids == []
    assert out.mean_b_factor is None
    # The agent gets explicit context about the format swap.
    assert any("no legacy PDB-format file" in c for c in out.caveats)


async def test_cif_fallback_respects_size_cap(patch_pdb) -> None:
    """Cap applies to CIF text too. 100 KB of fake CIF with a 20 KB cap → text omitted."""
    fake_cif = "data_BIGX\n" + ("x" * 110_000)
    patch_pdb((_fake_metadata("BIGX"), fake_cif, "cif"))
    out = await fetch_pdb_structure(FetchPdbInput(pdb_id="BIGX", max_pdb_kb=20))
    assert out.cif_text is None
    assert out.structure_format == "cif"
    assert any("CIF file is" in c for c in out.caveats)


async def test_fetch_pdb_no_ca_atoms_raises(patch_pdb) -> None:
    """Nucleic acid-only or ligand-only entries have zero CA atoms."""
    patch_pdb((_fake_metadata("DNA1"), "HEADER NUCLEIC ACID\nEND\n"))
    with pytest.raises(ToolError, match="Could not parse any CA atoms"):
        await fetch_pdb_structure(FetchPdbInput(pdb_id="DNA1"))


# --- Input validation -----------------------------------------------------------


def test_rejects_non_four_char_id() -> None:
    with pytest.raises(pydantic.ValidationError):
        FetchPdbInput(pdb_id="4HHBX")  # too long


def test_rejects_empty_id() -> None:
    with pytest.raises(pydantic.ValidationError):
        FetchPdbInput(pdb_id="")


def test_rejects_id_with_punctuation() -> None:
    with pytest.raises(pydantic.ValidationError):
        FetchPdbInput(pdb_id="4-HB")


# --- Registration ---------------------------------------------------------------


def test_tool_is_registered_with_correct_metadata() -> None:
    from bioforge.tools.registry import get_tool

    spec = get_tool("fetch_pdb_structure")
    assert spec.cost_hint == "cheap"
    assert spec.destructive is False
    assert "structure" in spec.tags
    assert "pdb" in spec.tags
    assert "rcsb" in spec.tags
    assert any("Berman" in c for c in spec.citations)


# --- Live test (opt-in) ---------------------------------------------------------


@pytest.mark.online
async def test_fetch_4hhb_from_real_api() -> None:
    """Live RCSB API: hemoglobin. Run with `pytest -m online`."""
    out = await fetch_pdb_structure(FetchPdbInput(pdb_id="4HHB", max_pdb_kb=5000))
    assert out.pdb_id == "4HHB"
    assert out.experimental_method and "X-RAY" in out.experimental_method.upper()
    assert isinstance(out.resolution_angstrom, float)
    # Hemoglobin is a tetramer with HEM cofactors.
    assert out.num_chains == 4
    assert "HEM" in out.ligand_ids
    assert out.pdb_text is not None
