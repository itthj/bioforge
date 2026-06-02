"""Tests for align_msa (MAFFT multiple-sequence alignment).

Hot tests mock the MAFFT subprocess (monkeypatching the runner's _default_run_fn) so they
never spawn Docker. The biological soundness checks -- the honesty rails -- get the bulk of
the coverage: a real MAFFT could in principle return a corrupted alignment, and the tool must
refuse rather than report it. The real-image run is a `-m docker` e2e that skips when absent.
"""

from __future__ import annotations

import pydantic
import pytest
from bioforge.config import settings
from bioforge.tools.base import ToolError
from bioforge.tools.registry import REGISTRY
from bioforge.tools.sequence.align_msa import AlignMsaInput, align_msa
from bioforge.tools.sequence.models.mafft import runner as mafft_runner


@pytest.fixture
def mafft_local(monkeypatch):
    """Enable MAFFT via the 'local' backend so build_command succeeds without a real image."""
    monkeypatch.setattr(settings, "mafft_enabled", True)
    monkeypatch.setattr(settings, "mafft_runner", "local")
    monkeypatch.setattr(settings, "mafft_binary", "mafft")


def _parse_in(stdin_text: str) -> list[tuple[str, str]]:
    recs: list[tuple[str, str]] = []
    cur, chunks = None, []
    for line in stdin_text.splitlines():
        if line.startswith(">"):
            if cur is not None:
                recs.append((cur, "".join(chunks)))
            cur, chunks = line[1:].strip(), []
        elif cur is not None:
            chunks.append(line.strip())
    if cur is not None:
        recs.append((cur, "".join(chunks)))
    return recs


def _pad_align_run_fn(argv, stdin_text, timeout):
    """A stand-in MAFFT that returns a VALID alignment: each input padded with trailing
    gaps to the max length. De-gapping recovers the input exactly, so it passes soundness."""
    recs = _parse_in(stdin_text)
    width = max(len(s) for _i, s in recs)
    return "".join(f">{i}\n{s + '-' * (width - len(s))}\n" for i, s in recs)


def _set_run_fn(monkeypatch, fn) -> None:
    monkeypatch.setattr(mafft_runner, "_default_run_fn", fn)


async def test_aligns_and_preserves_order_and_residues(mafft_local, monkeypatch) -> None:
    _set_run_fn(monkeypatch, _pad_align_run_fn)
    out = await align_msa(
        AlignMsaInput(
            sequences=[
                {"id": "a", "sequence": "ACGTACGT"},
                {"id": "b", "sequence": "ACGTAC"},
                {"id": "c", "sequence": "ACGTACGTAA"},
            ]
        )
    )
    assert out.num_sequences == 3
    assert out.alignment_length == 10  # widest input, others gap-padded
    assert [r.id for r in out.aligned] == ["a", "b", "c"]  # input order preserved
    for r in out.aligned:
        assert len(r.aligned_sequence) == out.alignment_length
    # De-gapping each row recovers the submitted sequence.
    degapped = {r.id: r.aligned_sequence.replace("-", "") for r in out.aligned}
    assert degapped == {"a": "ACGTACGT", "b": "ACGTAC", "c": "ACGTACGTAA"}
    assert "MAFFT" in out.method


async def test_refuses_when_mafft_disabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "mafft_enabled", False)
    with pytest.raises(ToolError, match="not enabled"):
        await align_msa(AlignMsaInput(sequences=[{"id": "a", "sequence": "ACGT"}, {"id": "b", "sequence": "ACGT"}]))


async def test_refuses_when_docker_image_unset(monkeypatch) -> None:
    monkeypatch.setattr(settings, "mafft_enabled", True)
    monkeypatch.setattr(settings, "mafft_runner", "docker")
    monkeypatch.setattr(settings, "mafft_docker_image", "")
    with pytest.raises(ToolError, match="BIOFORGE_MAFFT_DOCKER_IMAGE"):
        await align_msa(AlignMsaInput(sequences=[{"id": "a", "sequence": "ACGT"}, {"id": "b", "sequence": "ACGT"}]))


async def test_refuses_ragged_alignment(mafft_local, monkeypatch) -> None:
    def ragged(argv, stdin_text, timeout):
        return ">a\nACGT\n>b\nACG\n"  # different column counts

    _set_run_fn(monkeypatch, ragged)
    with pytest.raises(ToolError, match="ragged"):
        await align_msa(AlignMsaInput(sequences=[{"id": "a", "sequence": "ACGT"}, {"id": "b", "sequence": "ACG"}]))


async def test_refuses_when_aligner_alters_residues(mafft_local, monkeypatch) -> None:
    def altered(argv, stdin_text, timeout):
        # 'b' comes back as TTTT -- de-gapping won't match the submitted ACGT.
        return ">a\nACGT\n>b\nTTTT\n"

    _set_run_fn(monkeypatch, altered)
    with pytest.raises(ToolError, match="altered residues"):
        await align_msa(AlignMsaInput(sequences=[{"id": "a", "sequence": "ACGT"}, {"id": "b", "sequence": "ACGT"}]))


async def test_refuses_when_ids_change(mafft_local, monkeypatch) -> None:
    def wrong_ids(argv, stdin_text, timeout):
        return ">a\nACGT\n>zzz\nACGT\n"  # 'b' missing, 'zzz' appeared

    _set_run_fn(monkeypatch, wrong_ids)
    with pytest.raises(ToolError, match="different set of sequences"):
        await align_msa(AlignMsaInput(sequences=[{"id": "a", "sequence": "ACGT"}, {"id": "b", "sequence": "ACGT"}]))


def test_input_validation_rejects_bad_inputs() -> None:
    with pytest.raises(pydantic.ValidationError):  # < 2 sequences
        AlignMsaInput(sequences=[{"id": "a", "sequence": "ACGT"}])
    with pytest.raises(pydantic.ValidationError):  # duplicate ids
        AlignMsaInput(sequences=[{"id": "a", "sequence": "ACGT"}, {"id": "a", "sequence": "ACGT"}])
    with pytest.raises(pydantic.ValidationError):  # non-residue characters
        AlignMsaInput(sequences=[{"id": "a", "sequence": "ACGT1234"}, {"id": "b", "sequence": "ACGT"}])


def test_align_msa_is_registered() -> None:
    spec = REGISTRY["align_msa"]
    assert {"alignment", "msa"}.issubset(set(spec.tags))
    assert spec.emits_instance_uncertainty == {"aligner": False}


@pytest.mark.docker
async def test_align_msa_real_mafft_end_to_end() -> None:
    """Real MAFFT over real sequences. Skips unless a MAFFT runtime is configured."""
    if not settings.mafft_enabled or not (settings.mafft_docker_image or settings.mafft_runner == "local"):
        pytest.skip("MAFFT not configured (set BIOFORGE_MAFFT_ENABLED + image/binary).")
    # Three short real homologous fragments (conserved 5' of beta-globin-like CDS, illustrative).
    out = await align_msa(
        AlignMsaInput(
            sequences=[
                {"id": "seq1", "sequence": "ATGGTGCACCTGACTCCTGAGGAGAAGTCT"},
                {"id": "seq2", "sequence": "ATGGTGCATCTGACTCCTGAGGAGAAGTCT"},
                {"id": "seq3", "sequence": "ATGGTGCACCTGACTCCTGTGGAGAAGTCT"},
            ]
        )
    )
    assert out.num_sequences == 3
    assert all(len(r.aligned_sequence) == out.alignment_length for r in out.aligned)
