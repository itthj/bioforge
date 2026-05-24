"""Tests for the gc_content tool.

Phase 0 rule: real biology in tests. The substantive correctness assertion runs against a
real lambda phage fragment fetched by `tests/fixtures/regenerate.py` and the committed
metadata sidecar. The other tests cover input validation and the ambiguous-base path —
those are validator-behavior assertions, not biological claims.
"""

from __future__ import annotations

import pytest
import pydantic

from bioforge.tools.sequence.gc_content import (
    GcContentInput,
    GcContentOutput,
    gc_content,
)


async def test_gc_content_matches_lambda_phage_fixture(lambda_phage_fixture: dict) -> None:
    inp = GcContentInput(sequence=lambda_phage_fixture["sequence"])
    out: GcContentOutput = await gc_content(inp)
    assert out.total_length == lambda_phage_fixture["total_length"]
    assert out.gc_count == lambda_phage_fixture["gc_count"]
    assert out.gc_percent == pytest.approx(lambda_phage_fixture["gc_percent"], abs=1e-4)


async def test_gc_content_rejects_non_dna() -> None:
    with pytest.raises(pydantic.ValidationError, match="non-DNA characters"):
        GcContentInput(sequence="ATGZ!")


async def test_gc_content_rejects_empty() -> None:
    with pytest.raises(pydantic.ValidationError):
        GcContentInput(sequence="")


async def test_gc_content_tolerates_whitespace_within_line() -> None:
    inp = GcContentInput(sequence="ATGC ATGC")
    assert inp.sequence == "ATGCATGC"


async def test_gc_content_excludes_n_bases_from_percentage() -> None:
    # 4 informative bases (2 G + 2 C → 100% GC), 2 N bases excluded.
    out = await gc_content(GcContentInput(sequence="GCGCNN"))
    assert out.total_length == 6
    assert out.gc_count == 4
    assert out.n_count == 2
    assert out.gc_percent == pytest.approx(100.0)


async def test_gc_content_refuses_all_n_sequence() -> None:
    from bioforge.tools.base import ToolError

    with pytest.raises(ToolError, match="entirely N"):
        await gc_content(GcContentInput(sequence="NNNN"))


async def test_gc_content_case_insensitive() -> None:
    out = await gc_content(GcContentInput(sequence="atgcATGC"))
    assert out.total_length == 8
    assert out.gc_count == 4
    assert out.gc_percent == pytest.approx(50.0)
