"""Tests for cadd_score — CADD PHRED/raw score lookup.

Network is never hit. `_query_cadd` is monkeypatched to return records shaped
like the real CADD API's JSON list response.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from bioforge.tools import REGISTRY
from bioforge.tools.base import ToolError
from bioforge.tools.variants.cadd_score import (
    CaddScoreInput,
    _compose_version_string,
    _interpret_phred,
    cadd_score,
)
from pydantic import ValidationError


# --- Registry --------------------------------------------------------------------


def test_cadd_score_registered():
    assert "cadd_score" in REGISTRY


def test_cadd_score_metadata():
    spec = REGISTRY["cadd_score"]
    assert spec.name == "cadd_score"
    assert spec.description
    assert spec.version
    assert spec.citations
    assert "variants" in spec.tags
    assert "cadd" in spec.tags
    assert "phred_score" in spec.published_accuracy


# --- Version-string composition (the core API-correctness logic) -------------------


def test_legacy_version_bare_string_grch37():
    assert _compose_version_string("v1.3", "GRCh37") == "v1.3"


def test_legacy_version_rejects_grch38():
    with pytest.raises(ToolError, match="GRCh37 only"):
        _compose_version_string("v1.3", "GRCh38")


def test_v1_7_composes_build_prefix():
    assert _compose_version_string("v1.7", "GRCh38") == "GRCh38-v1.7"
    assert _compose_version_string("v1.7", "GRCh37") == "GRCh37-v1.7"


def test_v1_5_grch38_only():
    assert _compose_version_string("v1.5", "GRCh38") == "GRCh38-v1.5"
    with pytest.raises(ToolError, match="not released"):
        _compose_version_string("v1.5", "GRCh37")


def test_unknown_version_raises():
    with pytest.raises(ToolError, match="Unknown cadd_version"):
        _compose_version_string("v99.0", "GRCh38")


# --- PHRED interpretation buckets ---------------------------------------------------


@pytest.mark.parametrize(
    "phred,expected_substring",
    [
        (35.0, "0.1%"),
        (30.0, "0.1%"),
        (25.0, "top 1%"),
        (20.0, "top 1%"),
        (15.0, "top 10%"),
        (10.0, "top 10%"),
        (5.0, "not among"),
        (0.0, "not among"),
    ],
)
def test_interpret_phred_buckets(phred, expected_substring):
    assert expected_substring in _interpret_phred(phred)


# --- Input validation ----------------------------------------------------------------


def test_chrom_strips_chr_prefix():
    inp = CaddScoreInput(chrom="chr17", pos=43106487, ref="T", alt="G")
    assert inp.chrom == "17"


def test_chrom_accepts_x_y_mt():
    for c in ("X", "Y", "MT"):
        inp = CaddScoreInput(chrom=c, pos=100, ref="A", alt="C")
        assert inp.chrom == c


def test_invalid_chrom_rejected():
    with pytest.raises(ValidationError):
        CaddScoreInput(chrom="chr99z", pos=100, ref="A", alt="C")


def test_invalid_base_rejected():
    with pytest.raises(ValidationError):
        CaddScoreInput(chrom="1", pos=100, ref="Z", alt="C")


def test_alleles_uppercased():
    inp = CaddScoreInput(chrom="1", pos=100, ref="a", alt="g")
    assert inp.ref == "A"
    assert inp.alt == "G"


def test_invalid_genome_build_rejected():
    with pytest.raises(ValidationError):
        CaddScoreInput(chrom="1", pos=100, ref="A", alt="G", genome_build="hg19")


def test_negative_position_rejected():
    with pytest.raises(ValidationError):
        CaddScoreInput(chrom="1", pos=-5, ref="A", alt="G")


def test_defaults_are_grch38_v1_7():
    inp = CaddScoreInput(chrom="1", pos=100, ref="A", alt="G")
    assert inp.genome_build == "GRCh38"
    assert inp.cadd_version == "v1.7"


# --- Happy path ------------------------------------------------------------------


def _fake_record(ref="T", alt="G", raw="4.5", phred="25.3") -> dict:
    return {"Chrom": "17", "Pos": "43106487", "Ref": ref, "Alt": alt, "RawScore": raw, "PHRED": phred}


async def test_happy_path_returns_scores():
    with patch(
        "bioforge.tools.variants.cadd_score._query_cadd",
        AsyncMock(return_value=[_fake_record()]),
    ):
        out = await cadd_score(CaddScoreInput(chrom="17", pos=43106487, ref="T", alt="G"))
    assert out.raw_score == pytest.approx(4.5)
    assert out.phred_score == pytest.approx(25.3)
    assert "top 1%" in out.interpretation
    assert out.genome_build == "GRCh38"
    assert out.cadd_version == "v1.7"
    assert out.caveats


async def test_multiple_records_picks_matching_ref_alt():
    """If the endpoint ever returns multiple SNVs at a position, pick the one matching ref/alt."""
    records = [
        _fake_record(ref="T", alt="A", raw="1.0", phred="5.0"),
        _fake_record(ref="T", alt="G", raw="9.9", phred="31.0"),
    ]
    with patch("bioforge.tools.variants.cadd_score._query_cadd", AsyncMock(return_value=records)):
        out = await cadd_score(CaddScoreInput(chrom="17", pos=43106487, ref="T", alt="G"))
    assert out.raw_score == pytest.approx(9.9)
    assert out.phred_score == pytest.approx(31.0)
    assert "0.1%" in out.interpretation


async def test_legacy_version_happy_path():
    with patch(
        "bioforge.tools.variants.cadd_score._query_cadd",
        AsyncMock(return_value=[_fake_record(raw="1.2", phred="12.0")]),
    ) as mock_query:
        out = await cadd_score(
            CaddScoreInput(chrom="5", pos=2003402, ref="C", alt="A", cadd_version="v1.3", genome_build="GRCh37")
        )
    assert out.phred_score == pytest.approx(12.0)
    # Verify the composed version string sent to the query function was bare (no build prefix).
    call_args = mock_query.call_args
    assert call_args.args[0] == "v1.3" or call_args.kwargs.get("version_string") == "v1.3"
    assert any("legacy" in c.lower() for c in out.caveats)


# --- Error paths -----------------------------------------------------------------


async def test_empty_result_raises_tool_error():
    with patch("bioforge.tools.variants.cadd_score._query_cadd", AsyncMock(return_value=[])):
        with pytest.raises(ToolError, match="no score"):
            await cadd_score(CaddScoreInput(chrom="1", pos=999999999, ref="A", alt="G"))


async def test_malformed_score_fields_raises_tool_error():
    bad_record = {"Chrom": "1", "Pos": "100", "Ref": "A", "Alt": "G"}  # missing RawScore/PHRED
    with patch("bioforge.tools.variants.cadd_score._query_cadd", AsyncMock(return_value=[bad_record])):
        with pytest.raises(ToolError, match="malformed"):
            await cadd_score(CaddScoreInput(chrom="1", pos=100, ref="A", alt="G"))


async def test_grch38_with_legacy_version_raises_before_network_call():
    """Version/build validation happens before any network call — _query_cadd must not be invoked."""
    with patch("bioforge.tools.variants.cadd_score._query_cadd", AsyncMock()) as mock_query:
        with pytest.raises(ToolError, match="GRCh37 only"):
            await cadd_score(CaddScoreInput(chrom="1", pos=100, ref="A", alt="G", cadd_version="v1.2", genome_build="GRCh38"))
    mock_query.assert_not_called()


async def test_http_error_wrapped_as_tool_error():
    with patch(
        "bioforge.tools.variants.cadd_score._query_cadd",
        AsyncMock(side_effect=ToolError("CADD API unreachable: timed out. retry in a moment.")),
    ):
        with pytest.raises(ToolError, match="unreachable"):
            await cadd_score(CaddScoreInput(chrom="1", pos=100, ref="A", alt="G"))


async def test_non_200_status_raises(monkeypatch):
    """Exercise the real _query_cadd against a fake httpx client to hit the status-code branch."""
    import bioforge.tools.variants.cadd_score as mod

    class _FakeResponse:
        status_code = 500
        text = "server error"

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return _FakeResponse()

    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda timeout=None: _FakeClient())
    with pytest.raises(ToolError, match="HTTP 500"):
        await mod._query_cadd("GRCh38-v1.7", "1", 100, "A", "G")


async def test_non_list_response_raises(monkeypatch):
    import bioforge.tools.variants.cadd_score as mod

    class _FakeResponse:
        status_code = 200

        def json(self):
            return {"unexpected": "shape"}

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return _FakeResponse()

    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda timeout=None: _FakeClient())
    with pytest.raises(ToolError, match="unexpected response shape"):
        await mod._query_cadd("GRCh38-v1.7", "1", 100, "A", "G")


# --- Tool-level metadata stamping ------------------------------------------------


async def test_tool_stamped_via_registry_execute():
    from bioforge.tools.registry import execute_tool

    with patch(
        "bioforge.tools.variants.cadd_score._query_cadd",
        AsyncMock(return_value=[_fake_record()]),
    ):
        result = await execute_tool(
            "cadd_score",
            {"chrom": "17", "pos": 43106487, "ref": "T", "alt": "G"},
        )
    assert result.tool_name == "cadd_score"
    assert result.tool_version == "1.0.0"
    assert result.citations
