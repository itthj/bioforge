"""Tests for find_best_structure — Phase 4 composite tool.

The composite tool calls SIFTS first, then dispatches to fetch_pdb_structure
or fetch_alphafold_structure. We patch all three:
  - `_fetch_sifts_best_structures` (in find_best module) for the mapping call
  - `_fetch_pdb` (in fetch_pdb module) for the experimental fetch downstream
  - `_fetch_alphafold` (in fetch_alphafold module) for the prediction fallback

This lets us assert which branch the tool takes without any network and with
fixed metadata for the embedded child results.
"""

from __future__ import annotations

import pydantic
import pytest
from bioforge.tools.base import ToolError
from bioforge.tools.structure import fetch_alphafold as af_module
from bioforge.tools.structure import fetch_pdb as pdb_module
from bioforge.tools.structure import find_best as fb_module
from bioforge.tools.structure.find_best import (
    FindBestStructureInput,
    find_best_structure,
)

# Reuse the existing fake-PDB and fake-metadata builders from the sibling
# test modules — they already implement valid fixed-width PDB lines.
from .test_fetch_alphafold import (
    _build_fake_pdb as _build_fake_alphafold_pdb,
)
from .test_fetch_alphafold import (
    _fake_metadata as _fake_alphafold_metadata,
)
from .test_fetch_pdb import _build_fake_pdb as _build_fake_rcsb_pdb
from .test_fetch_pdb import _fake_metadata as _fake_rcsb_metadata


def _sifts_record(
    *,
    pdb_id: str,
    chain_id: str = "A",
    coverage: float = 0.9,
    resolution: float | None = 1.85,
    method: str = "X-ray diffraction",
    unp_start: int = 1,
    unp_end: int = 200,
) -> dict:
    return {
        "pdb_id": pdb_id.lower(),
        "chain_id": chain_id,
        "coverage": coverage,
        "resolution": resolution,
        "experimental_method": method,
        "unp_start": unp_start,
        "unp_end": unp_end,
        "tax_id": 9606,
    }


@pytest.fixture
def patch_all(monkeypatch):
    """Patch SIFTS, RCSB, and AlphaFold transport layers in one place.

    Returns an object with three setters:
      - sifts(records) — list[dict] returned by _fetch_sifts_best_structures
      - rcsb((meta, pdb_text)) — return tuple for _fetch_pdb
      - alphafold((meta, pdb_text)) — return tuple for _fetch_alphafold
    Any of them can be set to an Exception to make that call raise.
    """
    holder: dict = {"sifts": [], "rcsb": None, "alphafold": None, "calls": []}

    async def _fake_sifts(uniprot_id: str):
        holder["calls"].append(("sifts", uniprot_id))
        v = holder["sifts"]
        if isinstance(v, Exception):
            raise v
        return v

    async def _fake_pdb(pdb_id: str):
        holder["calls"].append(("pdb", pdb_id))
        v = holder["rcsb"]
        if isinstance(v, Exception):
            raise v
        return v

    async def _fake_af(uniprot_id: str):
        holder["calls"].append(("alphafold", uniprot_id))
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

        @property
        def calls(self):
            return holder["calls"]

    return Patches()


# --- Experimental path ----------------------------------------------------------


async def test_returns_experimental_when_sifts_has_a_hit(patch_all) -> None:
    patch_all.sifts([_sifts_record(pdb_id="1mx6", coverage=0.116, resolution=1.85)])
    patch_all.rcsb((_fake_rcsb_metadata("1MX6"), _build_fake_rcsb_pdb(chains={"A": 100}, ligands=["HEM"])))

    out = await find_best_structure(FindBestStructureInput(uniprot_id="P38398"))

    assert out.source == "experimental"
    assert out.pdb_result is not None
    assert out.alphafold_result is None
    assert out.pdb_result.pdb_id == "1MX6"
    assert "1MX6" in out.reason
    # The candidate list is populated even when the top one was used.
    assert len(out.experimental_candidates) == 1
    assert out.experimental_candidates[0].pdb_id == "1MX6"
    # The AlphaFold path was NOT touched.
    assert not any(c[0] == "alphafold" for c in patch_all.calls)


async def test_low_coverage_adds_caveat(patch_all) -> None:
    """Top SIFTS hit covers only 15% of the protein — caveat should flag this."""
    patch_all.sifts([_sifts_record(pdb_id="1mx6", coverage=0.15, resolution=1.85)])
    patch_all.rcsb((_fake_rcsb_metadata("1MX6"), _build_fake_rcsb_pdb(chains={"A": 50})))
    out = await find_best_structure(FindBestStructureInput(uniprot_id="P38398"))
    assert any("15%" in c for c in out.caveats)
    assert any("only" in c.lower() for c in out.caveats)


async def test_takes_first_sifts_record_when_multiple(patch_all) -> None:
    """SIFTS pre-sorts by coverage desc / resolution asc — we trust it and take [0]."""
    patch_all.sifts(
        [
            _sifts_record(pdb_id="1abc", coverage=0.95, resolution=1.5),
            _sifts_record(pdb_id="2def", coverage=0.50, resolution=1.0),
            _sifts_record(pdb_id="3ghi", coverage=0.30, resolution=0.8),
        ]
    )
    patch_all.rcsb((_fake_rcsb_metadata("1ABC"), _build_fake_rcsb_pdb(chains={"A": 100})))
    out = await find_best_structure(FindBestStructureInput(uniprot_id="P38398"))
    assert out.pdb_result is not None
    assert out.pdb_result.pdb_id == "1ABC"
    # All 3 considered candidates surface for transparency.
    assert len(out.experimental_candidates) == 3
    # Alternatives caveat fires.
    assert any("2 alternative" in c for c in out.caveats)


# --- Predicted (AlphaFold) fallback ---------------------------------------------


async def test_no_sifts_hit_falls_back_to_alphafold(patch_all) -> None:
    patch_all.sifts([])
    patch_all.alphafold((_fake_alphafold_metadata("Q9NRP7"), _build_fake_alphafold_pdb([85.0, 90.0, 95.0])))
    out = await find_best_structure(FindBestStructureInput(uniprot_id="Q9NRP7"))

    assert out.source == "predicted"
    assert out.alphafold_result is not None
    assert out.pdb_result is None
    assert "no experimental" in out.reason.lower() or "alphafold" in out.reason.lower()
    # Caveat acknowledges the gap.
    assert any("no experimental coverage" in c.lower() for c in out.caveats)
    # RCSB was NOT called.
    assert not any(c[0] == "pdb" for c in patch_all.calls)


async def test_prefer_predicted_skips_sifts_entirely(patch_all) -> None:
    """User explicitly asked for AlphaFold — don't even look at SIFTS."""
    patch_all.alphafold((_fake_alphafold_metadata("P38398"), _build_fake_alphafold_pdb([80.0] * 5)))
    out = await find_best_structure(
        FindBestStructureInput(uniprot_id="P38398", prefer="predicted"),
    )
    assert out.source == "predicted"
    assert "explicitly" in out.reason
    # SIFTS was not consulted.
    assert not any(c[0] == "sifts" for c in patch_all.calls)


async def test_prefer_experimental_with_no_hit_raises(patch_all) -> None:
    """User asked for experimental + nothing in SIFTS → ToolError (not silent
    fallback to AlphaFold). This is part of the "no silent truncation" rule."""
    patch_all.sifts([])
    with pytest.raises(ToolError, match="No experimental structure"):
        await find_best_structure(
            FindBestStructureInput(uniprot_id="Q9NRP7", prefer="experimental"),
        )
    # AlphaFold was NOT silently called.
    assert not any(c[0] == "alphafold" for c in patch_all.calls)


async def test_rcsb_fetch_failure_falls_back_to_alphafold_in_auto(patch_all) -> None:
    """SIFTS suggests a PDB, but the actual PDB fetch errors out. In auto mode,
    we still want to give the agent something, so fall back to AlphaFold + record
    the failure in the reason."""
    patch_all.sifts([_sifts_record(pdb_id="9zzz", coverage=0.9, resolution=2.0)])
    patch_all.rcsb(ToolError("PDB download failed"))
    patch_all.alphafold((_fake_alphafold_metadata("P38398"), _build_fake_alphafold_pdb([85.0] * 5)))

    out = await find_best_structure(FindBestStructureInput(uniprot_id="P38398"))
    assert out.source == "predicted"
    assert "9ZZZ" in out.reason or "9zzz" in out.reason.lower()
    assert "could not be fetched" in " ".join(out.caveats).lower()


async def test_rcsb_fetch_failure_does_not_swallow_in_strict_mode(patch_all) -> None:
    """In prefer='experimental', a PDB fetch failure should surface — not
    silently swap to AlphaFold."""
    patch_all.sifts([_sifts_record(pdb_id="9zzz", coverage=0.9, resolution=2.0)])
    patch_all.rcsb(ToolError("PDB download failed"))
    with pytest.raises(ToolError, match="PDB download failed"):
        await find_best_structure(
            FindBestStructureInput(uniprot_id="P38398", prefer="experimental"),
        )


# --- Forwarding ----------------------------------------------------------------


async def test_max_pdb_kb_forwarded_to_downstream(patch_all) -> None:
    """The composite's max_pdb_kb is forwarded — verified indirectly by checking
    the resulting pdb_result.pdb_text is None when the cap is undersized."""
    patch_all.sifts([_sifts_record(pdb_id="1mx6", coverage=0.9)])
    # Synthesize a "big" PDB (~150 KB) and request a 20 KB cap.
    big_pdb = _build_fake_rcsb_pdb(chains={"A": 800})
    patch_all.rcsb((_fake_rcsb_metadata("1MX6"), big_pdb))

    out = await find_best_structure(
        FindBestStructureInput(uniprot_id="P38398", max_pdb_kb=20),
    )
    assert out.pdb_result is not None
    assert out.pdb_result.pdb_text is None
    # The downstream tool's own size-cap caveat shows up on the embedded result.
    assert any("exceeding the 20 KB" in c for c in out.pdb_result.caveats)


# --- Input validation ----------------------------------------------------------


def test_rejects_invalid_uniprot_id() -> None:
    with pytest.raises(pydantic.ValidationError):
        FindBestStructureInput(uniprot_id="p38398")  # lowercase


def test_rejects_unknown_prefer_option() -> None:
    with pytest.raises(pydantic.ValidationError):
        FindBestStructureInput(uniprot_id="P38398", prefer="random")  # type: ignore[arg-type]


# --- Registration --------------------------------------------------------------


def test_tool_is_registered_as_cheap_composite() -> None:
    from bioforge.tools.registry import get_tool

    spec = get_tool("find_best_structure")
    assert spec.cost_hint == "cheap"
    assert spec.destructive is False
    assert "structure" in spec.tags
    assert "composite" in spec.tags


# --- Live test (opt-in) --------------------------------------------------------


@pytest.mark.online
async def test_brca1_picks_experimental_brct_domain() -> None:
    """BRCA1 has well-characterized BRCT-domain structures in PDB. Run with
    `pytest -m online`."""
    out = await find_best_structure(FindBestStructureInput(uniprot_id="P38398", max_pdb_kb=5000))
    assert out.source == "experimental"
    assert out.pdb_result is not None
    assert len(out.experimental_candidates) >= 1
