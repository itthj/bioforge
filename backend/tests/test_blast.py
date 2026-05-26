"""BLAST tool tests.

Network is never hit. `_run_ncbi_blast` is monkeypatched to return a fabricated record
shaped like what `Bio.Blast.NCBIXML.read` produces. We test the parser, the alphabet
validator, and the error-mapping layer — not Biopython or NCBI's correctness.
"""

from __future__ import annotations

from types import SimpleNamespace

import pydantic
import pytest
from bioforge.tools.base import ToolError
from bioforge.tools.sequence import blast as blast_module
from bioforge.tools.sequence.blast import (
    BlastInput,
    BlastProgram,
    blast,
)


def _fake_hsp(
    *,
    expect: float = 1e-50,
    bits: float = 300.0,
    identities: int = 95,
    align_length: int = 100,
    query_start: int = 1,
    query_end: int = 100,
    subject_start: int = 1001,
    subject_end: int = 1100,
) -> SimpleNamespace:
    return SimpleNamespace(
        expect=expect,
        bits=bits,
        identities=identities,
        align_length=align_length,
        query_start=query_start,
        query_end=query_end,
        sbjct_start=subject_start,
        sbjct_end=subject_end,
    )


def _fake_alignment(
    *,
    accession: str,
    hit_def: str,
    hsps: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        accession=accession,
        hit_def=hit_def,
        hsps=hsps if hsps is not None else [_fake_hsp()],
    )


def _fake_blast_record(alignments: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(alignments=alignments)


@pytest.fixture
def patch_ncbi(monkeypatch):
    """Yields a setter: `set_response((record, rid))` configures the next call's return."""

    holder: dict = {"response": None, "calls": []}

    async def _fake_run(*, program, database, sequence, expect, hitlist_size, task=None):
        holder["calls"].append(
            dict(
                program=program,
                database=database,
                sequence=sequence,
                expect=expect,
                hitlist_size=hitlist_size,
                task=task,
            )
        )
        if isinstance(holder["response"], Exception):
            raise holder["response"]
        return holder["response"]

    monkeypatch.setattr(blast_module, "_run_ncbi_blast", _fake_run)

    def setter(response):
        holder["response"] = response

    setter.calls = holder["calls"]
    return setter


async def test_blast_parses_hits(patch_ncbi) -> None:
    record = _fake_blast_record(
        [
            _fake_alignment(
                accession="NM_007294.4",
                hit_def="Homo sapiens BRCA1 mRNA [Homo sapiens]",
                hsps=[_fake_hsp(expect=1e-100, bits=500, identities=98, align_length=100)],
            ),
            _fake_alignment(
                accession="XM_005246379.1",
                hit_def="PREDICTED: Pan troglodytes BRCA1 [Pan troglodytes]",
            ),
        ]
    )
    patch_ncbi((record, "RID-ABC123"))

    out = await blast(
        BlastInput(
            sequence="ATGCATGCATGCATGC",
            program=BlastProgram.blastn,
            database="nt",
            max_hits=10,
        )
    )

    assert out.program == "blastn"
    assert out.database == "nt"
    assert out.request_id == "RID-ABC123"
    assert out.query_length == 16
    assert out.num_hits_returned == 2

    top = out.hits[0]
    assert top.accession == "NM_007294.4"
    assert top.organism == "Homo sapiens"
    assert top.e_value == pytest.approx(1e-100)
    assert top.identity_percent == 98.0
    assert top.alignment_length == 100


async def test_blast_respects_max_hits(patch_ncbi) -> None:
    record = _fake_blast_record([_fake_alignment(accession=f"ACC{i}", hit_def=f"hit {i}") for i in range(20)])
    patch_ncbi((record, "RID-X"))

    out = await blast(BlastInput(sequence="ATGCATGCATGCATGC", max_hits=5))
    assert out.num_hits_returned == 5


async def test_blast_validates_dna_alphabet_for_blastn(patch_ncbi) -> None:
    """blastn with a protein-like sequence is caught BEFORE the network call."""
    patch_ncbi((_fake_blast_record([]), ""))
    with pytest.raises(ToolError, match="blastn requires a DNA query"):
        await blast(
            BlastInput(
                sequence="MEEPQSDPSVEPPLSQETFSDLWKLLPENNVL",  # p53 N-terminus
                program=BlastProgram.blastn,
            )
        )
    # The network was not called.
    assert patch_ncbi.calls == []


async def test_blast_rejects_too_short_sequence() -> None:
    with pytest.raises(pydantic.ValidationError):
        BlastInput(sequence="ATGC")  # min_length=12


async def test_blast_rejects_too_many_max_hits() -> None:
    with pytest.raises(pydantic.ValidationError):
        BlastInput(sequence="ATGCATGCATGCATGC", max_hits=100)  # le=50


async def test_blast_maps_network_error_to_tool_error(patch_ncbi) -> None:
    patch_ncbi(RuntimeError("Connection refused"))
    with pytest.raises(ToolError, match="NCBI BLAST call failed"):
        await blast(BlastInput(sequence="ATGCATGCATGCATGC"))


async def test_blast_empty_result_is_not_an_error(patch_ncbi) -> None:
    """No hits is a valid biological answer, not an error."""
    patch_ncbi((_fake_blast_record([]), "RID-EMPTY"))
    out = await blast(BlastInput(sequence="ATGCATGCATGCATGC"))
    assert out.num_hits_returned == 0
    assert out.hits == []
    assert out.request_id == "RID-EMPTY"


async def test_blast_is_registered_as_expensive() -> None:
    from bioforge.tools.registry import get_tool

    spec = get_tool("blast")
    assert spec.cost_hint == "expensive"
    assert spec.destructive is False
    assert "alignment" in spec.tags


# --- Local backend ---------------------------------------------------------------------
#
# Local BLAST+ shells out to a binary. We patch BOTH the local runner (no real subprocess)
# AND shutil.which (so the binary-found check passes without BLAST+ installed). The path
# coverage is the same shape as the remote one: success, missing-binary, nonzero exit.


@pytest.fixture
def patch_local(monkeypatch):
    """Monkeypatch _run_local_blast + shutil.which so the local backend path is testable
    without an actual BLAST+ install."""
    holder: dict = {"response": None, "calls": []}

    async def _fake_local(*, program, database, sequence, expect, hitlist_size, task=None):
        holder["calls"].append(
            dict(
                program=program,
                database=database,
                sequence=sequence,
                expect=expect,
                hitlist_size=hitlist_size,
                task=task,
            )
        )
        if isinstance(holder["response"], Exception):
            raise holder["response"]
        return holder["response"]

    monkeypatch.setattr(blast_module, "_run_local_blast", _fake_local)
    # shutil.which is called inside the REAL _run_local_blast, but our fake replaces
    # the whole function — no need to patch shutil. Kept here as a comment so future
    # tests of the real subprocess path know where to look.

    def setter(response):
        holder["response"] = response

    setter.calls = holder["calls"]
    return setter


async def test_local_backend_routes_to_local_runner(patch_local) -> None:
    """database_type=local sends the call through _run_local_blast, NOT NCBIWWW.qblast."""
    record = _fake_blast_record([_fake_alignment(accession="LOCAL", hit_def="local hit [Homo sapiens]")])
    patch_local((record, ""))

    out = await blast(
        BlastInput(
            sequence="ATGCATGCATGCATGC",
            database="my_local_db",
            database_type="local",
        )
    )
    assert out.backend == "local"
    # Local searches have no NCBI RID
    assert out.request_id == ""
    assert out.num_hits_returned == 1
    assert out.hits[0].accession == "LOCAL"
    # The local runner was called with the user's local db name verbatim
    assert patch_local.calls[0]["database"] == "my_local_db"


async def test_remote_backend_is_default_and_unchanged(patch_ncbi) -> None:
    """Default database_type=remote still hits the NCBI runner (regression check
    for the refactor)."""
    record = _fake_blast_record([_fake_alignment(accession="REMOTE", hit_def="remote hit")])
    patch_ncbi((record, "RID-DEFAULT"))
    out = await blast(BlastInput(sequence="ATGCATGCATGCATGC"))
    assert out.backend == "remote"
    assert out.request_id == "RID-DEFAULT"


async def test_local_backend_missing_binary_raises_with_install_hint(monkeypatch) -> None:
    """Real _run_local_blast: shutil.which returns None → ToolError with install hint."""
    import shutil as _shutil

    monkeypatch.setattr(_shutil, "which", lambda name: None)
    with pytest.raises(ToolError, match="not found in PATH"):
        await blast(
            BlastInput(
                sequence="ATGCATGCATGCATGC",
                database="some_local_db",
                database_type="local",
            )
        )


async def test_local_backend_subprocess_failure_maps_to_tool_error(patch_local) -> None:
    """The local runner raising a generic error is wrapped in ToolError by the handler."""
    patch_local(RuntimeError("makeblastdb produced corrupted index"))
    with pytest.raises(ToolError, match="Local BLAST"):
        await blast(
            BlastInput(
                sequence="ATGCATGCATGCATGC",
                database="bad_db",
                database_type="local",
            )
        )
