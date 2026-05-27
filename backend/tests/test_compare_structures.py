"""Tests for compare_structures composite.

The composite fans out to find_best_structure (which itself calls SIFTS +
fetch_pdb_structure) and fetch_alphafold_structure. We patch the three
transport layers — same approach as test_find_best_structure.
"""

from __future__ import annotations

import pytest
from bioforge.tools.base import ToolError
from bioforge.tools.structure import fetch_alphafold as af_module
from bioforge.tools.structure import fetch_pdb as pdb_module
from bioforge.tools.structure import find_best as fb_module
from bioforge.tools.structure.compare_structures import (
    CompareStructuresInput,
    _compute_overlap,
    compare_structures,
)

from .test_fetch_alphafold import _build_fake_pdb as _build_fake_af_pdb
from .test_fetch_alphafold import _fake_metadata as _fake_af_metadata
from .test_fetch_pdb import _build_fake_pdb as _build_fake_rcsb_pdb
from .test_fetch_pdb import _fake_metadata as _fake_rcsb_metadata


def _sifts_record(
    *,
    pdb_id: str,
    coverage: float = 0.9,
    resolution: float = 1.85,
    unp_start: int = 1,
    unp_end: int = 200,
    chain_id: str = "A",
) -> dict:
    return {
        "pdb_id": pdb_id.lower(),
        "chain_id": chain_id,
        "coverage": coverage,
        "resolution": resolution,
        "experimental_method": "X-ray diffraction",
        "unp_start": unp_start,
        "unp_end": unp_end,
        "tax_id": 9606,
    }


@pytest.fixture
def patch_all(monkeypatch):
    holder: dict = {"sifts": [], "rcsb": None, "alphafold": None}

    async def _fake_sifts(uniprot_id: str):
        v = holder["sifts"]
        if isinstance(v, Exception):
            raise v
        return v

    async def _fake_pdb(pdb_id: str):
        v = holder["rcsb"]
        if isinstance(v, Exception):
            raise v
        if isinstance(v, tuple) and len(v) == 2:
            meta, text = v
            return meta, text, "pdb"
        return v

    async def _fake_af(uniprot_id: str):
        v = holder["alphafold"]
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr(fb_module, "_fetch_sifts_best_structures", _fake_sifts)
    monkeypatch.setattr(pdb_module, "_fetch_pdb", _fake_pdb)
    monkeypatch.setattr(af_module, "_fetch_alphafold", _fake_af)

    class Patches:
        def sifts(self, v):
            holder["sifts"] = v

        def rcsb(self, v):
            holder["rcsb"] = v

        def alphafold(self, v):
            holder["alphafold"] = v

    return Patches()


# --- Overlap math ---------------------------------------------------------------


def test_overlap_full_coverage() -> None:
    """Experimental covers residues 1-200 of a 200-aa AlphaFold model."""
    overlap = _compute_overlap(exp_start=1, exp_end=200, af_length=200)
    assert overlap.overlap_start == 1
    assert overlap.overlap_end == 200
    assert overlap.overlap_residues == 200
    assert overlap.predicted_only_residues == 0
    assert overlap.experimental_only_residues == 0


def test_overlap_partial_within_alphafold() -> None:
    """Experimental covers a C-terminal fragment (e.g. BRCA1 BRCT)."""
    overlap = _compute_overlap(exp_start=1646, exp_end=1863, af_length=1863)
    assert overlap.overlap_start == 1646
    assert overlap.overlap_end == 1863
    assert overlap.overlap_residues == 1863 - 1646 + 1
    assert overlap.predicted_only_residues == 1863 - overlap.overlap_residues
    assert overlap.experimental_only_residues == 0


def test_overlap_experimental_outside_alphafold_length() -> None:
    """SIFTS coordinates extend past the AlphaFold length — isoform mismatch."""
    overlap = _compute_overlap(exp_start=500, exp_end=700, af_length=400)
    assert overlap.overlap_start is None
    assert overlap.overlap_residues == 0
    assert overlap.experimental_only_residues == 201  # 500-700 inclusive
    assert overlap.predicted_only_residues == 400


def test_overlap_missing_sifts_coordinates() -> None:
    overlap = _compute_overlap(exp_start=None, exp_end=None, af_length=100)
    assert overlap.overlap_residues == 0
    assert overlap.predicted_only_residues == 100


# --- End-to-end -----------------------------------------------------------------


async def test_compare_returns_both_results(patch_all) -> None:
    patch_all.sifts([_sifts_record(pdb_id="1mx6", unp_start=1646, unp_end=1863)])
    patch_all.rcsb((_fake_rcsb_metadata("1MX6"), _build_fake_rcsb_pdb(chains={"A": 218})))
    patch_all.alphafold((_fake_af_metadata("P38398"), _build_fake_af_pdb([85.0] * 1863)))

    out = await compare_structures(CompareStructuresInput(uniprot_id="P38398"))

    assert out.uniprot_id == "P38398"
    assert out.experimental.pdb_id == "1MX6"
    assert out.predicted.entry_id == "AF-P38398-F1"
    assert out.predicted.length_residues == 1863
    # The SIFTS coordinates are propagated into the overlap.
    assert out.overlap.experimental_start == 1646
    assert out.overlap.experimental_end == 1863
    assert out.overlap.overlap_residues == 1863 - 1646 + 1
    # Summary mentions both structures.
    assert "1MX6" in out.summary
    assert "AF-P38398-F1" in out.summary
    # The "use Mol* superpose" caveat is always present.
    assert any("superpose" in c.lower() for c in out.caveats)


async def test_compare_raises_when_no_experimental_exists(patch_all) -> None:
    """Without a SIFTS hit + prefer='experimental', find_best_structure raises
    — and compare_structures should propagate cleanly (not silently sub in
    the prediction)."""
    patch_all.sifts([])
    # AlphaFold fetch shouldn't even run, but set it anyway so a regression
    # surfaces if the composite mistakenly continues.
    patch_all.alphafold((_fake_af_metadata("P38398"), _build_fake_af_pdb([85.0] * 100)))
    with pytest.raises(ToolError, match="No experimental"):
        await compare_structures(CompareStructuresInput(uniprot_id="P38398"))


async def test_compare_propagates_alphafold_failure(patch_all) -> None:
    patch_all.sifts([_sifts_record(pdb_id="1mx6", unp_start=1, unp_end=200)])
    patch_all.rcsb((_fake_rcsb_metadata("1MX6"), _build_fake_rcsb_pdb(chains={"A": 50})))
    patch_all.alphafold(ToolError("AlphaFold prediction not available"))
    with pytest.raises(ToolError, match="AlphaFold prediction not available"):
        await compare_structures(CompareStructuresInput(uniprot_id="P38398"))


async def test_compare_handles_missing_sifts_coordinates(patch_all) -> None:
    """SIFTS record without unp_start/unp_end — overlap should be zero with a caveat."""
    patch_all.sifts([_sifts_record(pdb_id="1mx6", unp_start=None, unp_end=None)])  # type: ignore[arg-type]
    patch_all.rcsb((_fake_rcsb_metadata("1MX6"), _build_fake_rcsb_pdb(chains={"A": 50})))
    patch_all.alphafold((_fake_af_metadata("P38398"), _build_fake_af_pdb([85.0] * 100)))
    out = await compare_structures(CompareStructuresInput(uniprot_id="P38398"))
    assert out.overlap.overlap_residues == 0
    assert any("did not report" in c.lower() for c in out.caveats)


# --- Registration ---------------------------------------------------------------


def test_tool_registered() -> None:
    from bioforge.tools.registry import get_tool

    spec = get_tool("compare_structures")
    assert spec.cost_hint == "cheap"
    assert "comparison" in spec.tags
    assert "composite" in spec.tags
