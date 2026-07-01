"""Tests for amr_detection — local blastx-against-CARD AMR gene detection.

Network and filesystem are never touched by default: `_run_local_amr_blast` is
monkeypatched to return canned outfmt-6 tabular text in most tests. A couple of
tests exercise the *real* `_run_local_amr_blast` against a faked subprocess to
cover the missing-binary and non-zero-exit paths, mirroring test_blast.py's
local-backend test pattern.

The consent gate is tested by injecting an isolated `Settings()` instance and
monkeypatching the module's `settings` reference — never the process env,
mirroring test_indelphi_fetcher.py's pattern.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from bioforge.config import Settings
from bioforge.tools import REGISTRY
from bioforge.tools.base import ToolError
from bioforge.tools.functional.amr_detection import (
    AmrDetectionInput,
    _extract_gene_name,
    _parse_amr_hits,
    _run_local_amr_blast,
    amr_detection,
)
from pydantic import ValidationError


def _make_settings(*, consent: bool = True, db: str = "/data/card/card_protein") -> Settings:
    """Build an isolated Settings instance — never touch the module-level singleton."""
    s = Settings()
    s.card_consent_commercial_license = consent
    s.card_blast_db = db
    s.card_min_identity_pct = 40.0
    s.card_min_coverage_pct = 40.0
    return s


# --- Registry --------------------------------------------------------------------


def test_amr_detection_registered():
    assert "amr_detection" in REGISTRY


def test_amr_detection_metadata():
    spec = REGISTRY["amr_detection"]
    assert spec.name == "amr_detection"
    assert spec.description
    assert spec.version
    assert spec.citations
    assert "functional" in spec.tags
    assert "amr" in spec.tags
    assert spec.reference_data_keys == ["card"]


# --- Input validation ----------------------------------------------------------------


def test_sequence_empty_after_strip_rejected():
    with pytest.raises(ValidationError):
        AmrDetectionInput(sequence="   ")


def test_sequence_invalid_residues_rejected():
    with pytest.raises(ValidationError, match="nucleotide"):
        AmrDetectionInput(sequence="ATGCXYZATGCATGC")


def test_sequence_whitespace_stripped_and_uppercased():
    inp = AmrDetectionInput(sequence="atgc atgc\ngggg")
    assert inp.sequence == "ATGCATGCGGGG"


def test_min_identity_bounds():
    with pytest.raises(ValidationError):
        AmrDetectionInput(sequence="ATGCATGCATGC", min_identity_pct=101.0)
    with pytest.raises(ValidationError):
        AmrDetectionInput(sequence="ATGCATGCATGC", min_identity_pct=-1.0)


def test_max_hits_bounds():
    with pytest.raises(ValidationError):
        AmrDetectionInput(sequence="ATGCATGCATGC", max_hits=0)
    with pytest.raises(ValidationError):
        AmrDetectionInput(sequence="ATGCATGCATGC", max_hits=201)


# --- _extract_gene_name: header parsing robust to field reordering -----------------


def test_gene_name_standard_card_layout():
    assert _extract_gene_name("gb|AAA25406.1|ARO:3002999|CblA-1") == "CblA-1"


def test_gene_name_aro_last_field():
    assert _extract_gene_name("gb|AAA25406.1|CblA-1|ARO:3002999") == "CblA-1"


def test_gene_name_no_aro_field_falls_back_to_raw_id():
    assert _extract_gene_name("gb|AAA25406.1|CblA-1") == "gb|AAA25406.1|CblA-1"


def test_gene_name_aro_only_field():
    assert _extract_gene_name("ARO:3002999") == "ARO:3002999"


# --- _parse_amr_hits: tabular parsing, coverage, confidence bands ------------------


def _tabular_line(
    sseqid="gb|AAA25406.1|ARO:3002999|CblA-1",
    pident="95.0",
    length="270",
    slen="270",
    evalue="1e-50",
    bitscore="500.0",
) -> str:
    return "\t".join(
        ["query", sseqid, pident, length, "5", "0", "1", "810", "1", str(int(length)), evalue, bitscore, "810", slen]
    )


def test_parse_single_high_confidence_hit():
    hits = _parse_amr_hits(_tabular_line(), min_identity=40.0, min_coverage=40.0, max_hits=25)
    assert len(hits) == 1
    hit = hits[0]
    assert hit.aro_accession == "ARO:3002999"
    assert hit.gene_name == "CblA-1"
    assert hit.identity_percent == 95.0
    assert hit.reference_coverage_percent == 100.0
    assert hit.confidence == "high"


def test_parse_moderate_confidence_low_coverage():
    # 50% coverage (length=135 of slen=270), identity high -> moderate, not high
    line = _tabular_line(length="135", slen="270")
    hits = _parse_amr_hits(line, min_identity=40.0, min_coverage=40.0, max_hits=25)
    assert len(hits) == 1
    assert hits[0].reference_coverage_percent == 50.0
    assert hits[0].confidence == "moderate"


def test_parse_moderate_confidence_low_identity():
    line = _tabular_line(pident="75.0")
    hits = _parse_amr_hits(line, min_identity=40.0, min_coverage=40.0, max_hits=25)
    assert hits[0].confidence == "moderate"


def test_parse_filters_below_min_identity():
    line = _tabular_line(pident="30.0")
    hits = _parse_amr_hits(line, min_identity=40.0, min_coverage=40.0, max_hits=25)
    assert hits == []


def test_parse_filters_below_min_coverage():
    line = _tabular_line(length="50", slen="270")  # ~18.5% coverage
    hits = _parse_amr_hits(line, min_identity=40.0, min_coverage=40.0, max_hits=25)
    assert hits == []


def test_parse_coverage_capped_at_100():
    # length > slen can happen with gapped alignments; must clamp to 100, not overshoot
    line = _tabular_line(length="300", slen="270")
    hits = _parse_amr_hits(line, min_identity=40.0, min_coverage=40.0, max_hits=25)
    assert hits[0].reference_coverage_percent == 100.0


def test_parse_zero_slen_gives_zero_coverage_no_crash():
    line = _tabular_line(length="100", slen="0")
    hits = _parse_amr_hits(line, min_identity=0.0, min_coverage=0.0, max_hits=25)
    assert hits[0].reference_coverage_percent == 0.0


def test_parse_skips_malformed_lines():
    good = _tabular_line()
    malformed = "not\tenough\tfields"
    text = f"{good}\n{malformed}\n\n"  # blank line too
    hits = _parse_amr_hits(text, min_identity=40.0, min_coverage=40.0, max_hits=25)
    assert len(hits) == 1


def test_parse_skips_non_numeric_fields():
    bad_line = _tabular_line(pident="not_a_number")
    hits = _parse_amr_hits(bad_line, min_identity=40.0, min_coverage=40.0, max_hits=25)
    assert hits == []


def test_parse_sorts_by_bitscore_descending():
    lines = "\n".join(
        [
            _tabular_line(sseqid="gb|A1|ARO:1000001|geneA", bitscore="100.0"),
            _tabular_line(sseqid="gb|A2|ARO:1000002|geneB", bitscore="500.0"),
            _tabular_line(sseqid="gb|A3|ARO:1000003|geneC", bitscore="250.0"),
        ]
    )
    hits = _parse_amr_hits(lines, min_identity=0.0, min_coverage=0.0, max_hits=25)
    assert [h.gene_name for h in hits] == ["geneB", "geneC", "geneA"]


def test_parse_respects_max_hits():
    lines = "\n".join(
        _tabular_line(sseqid=f"gb|A{i}|ARO:100000{i}|gene{i}", bitscore=str(100 + i)) for i in range(10)
    )
    hits = _parse_amr_hits(lines, min_identity=0.0, min_coverage=0.0, max_hits=3)
    assert len(hits) == 3


def test_parse_no_aro_field_still_returns_hit_with_none_accession():
    line = _tabular_line(sseqid="gb|AAA25406.1|CblA-1")
    hits = _parse_amr_hits(line, min_identity=40.0, min_coverage=40.0, max_hits=25)
    assert hits[0].aro_accession is None


# --- Consent gate ------------------------------------------------------------------


async def test_consent_gate_blocks_without_flag():
    fake_settings = _make_settings(consent=False)
    with patch("bioforge.tools.functional.amr_detection.settings", fake_settings):
        with patch(
            "bioforge.tools.functional.amr_detection._run_local_amr_blast", AsyncMock()
        ) as mock_blast:
            with pytest.raises(ToolError, match="BIOFORGE_CARD_CONSENT_COMMERCIAL_LICENSE"):
                await amr_detection(AmrDetectionInput(sequence="ATGCATGCATGC" * 5))
    mock_blast.assert_not_called()


async def test_consent_gate_blocks_before_database_check():
    """Even with no database configured, the consent-gate error must fire first."""
    fake_settings = _make_settings(consent=False, db="")
    with patch("bioforge.tools.functional.amr_detection.settings", fake_settings):
        with pytest.raises(ToolError, match="CONSENT"):
            await amr_detection(AmrDetectionInput(sequence="ATGCATGCATGC" * 5))


async def test_missing_database_raises_when_consented():
    fake_settings = _make_settings(consent=True, db="")
    with patch("bioforge.tools.functional.amr_detection.settings", fake_settings):
        with patch(
            "bioforge.tools.functional.amr_detection._run_local_amr_blast", AsyncMock()
        ) as mock_blast:
            with pytest.raises(ToolError, match="No CARD BLAST database configured"):
                await amr_detection(AmrDetectionInput(sequence="ATGCATGCATGC" * 5))
    mock_blast.assert_not_called()


async def test_per_call_card_db_overrides_settings():
    fake_settings = _make_settings(consent=True, db="")
    with patch("bioforge.tools.functional.amr_detection.settings", fake_settings):
        with patch(
            "bioforge.tools.functional.amr_detection._run_local_amr_blast",
            AsyncMock(return_value=""),
        ) as mock_blast:
            out = await amr_detection(
                AmrDetectionInput(sequence="ATGCATGCATGC" * 5, card_db="/custom/path/db")
            )
    assert out.database == "/custom/path/db"
    mock_blast.assert_called_once()
    assert mock_blast.call_args.kwargs["database"] == "/custom/path/db"


# --- Happy path (mocked blastx call) ------------------------------------------------


async def test_happy_path_returns_hits():
    fake_settings = _make_settings()
    with patch("bioforge.tools.functional.amr_detection.settings", fake_settings):
        with patch(
            "bioforge.tools.functional.amr_detection._run_local_amr_blast",
            AsyncMock(return_value=_tabular_line()),
        ):
            out = await amr_detection(AmrDetectionInput(sequence="ATGCATGCATGC" * 5))
    assert out.n_hits == 1
    assert out.hits[0].aro_accession == "ARO:3002999"
    assert out.database == "/data/card/card_protein"
    assert out.caveats


async def test_no_hits_adds_explanatory_caveat():
    fake_settings = _make_settings()
    with patch("bioforge.tools.functional.amr_detection.settings", fake_settings):
        with patch(
            "bioforge.tools.functional.amr_detection._run_local_amr_blast",
            AsyncMock(return_value=""),
        ):
            out = await amr_detection(AmrDetectionInput(sequence="ATGCATGCATGC" * 5))
    assert out.n_hits == 0
    assert any("No AMR genes were detected" in c for c in out.caveats)


async def test_per_call_thresholds_override_settings_defaults():
    fake_settings = _make_settings()  # defaults: identity=40, coverage=40
    with patch("bioforge.tools.functional.amr_detection.settings", fake_settings):
        with patch(
            "bioforge.tools.functional.amr_detection._run_local_amr_blast",
            AsyncMock(return_value=_tabular_line(pident="50.0")),
        ):
            # Raise the per-call threshold above the hit's identity -> filtered out
            out = await amr_detection(AmrDetectionInput(sequence="ATGCATGCATGC" * 5, min_identity_pct=90.0))
    assert out.n_hits == 0


# --- Real _run_local_amr_blast: missing binary / non-zero exit ---------------------


async def test_run_local_amr_blast_missing_binary(monkeypatch):
    import bioforge.tools.functional.amr_detection as mod

    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    with pytest.raises(ToolError, match="not found in PATH"):
        await _run_local_amr_blast(database="/data/card/db", sequence="ATGCATGC", evalue=1e-5, max_target_seqs=25)


async def test_run_local_amr_blast_nonzero_exit(monkeypatch):
    import bioforge.tools.functional.amr_detection as mod

    monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/bin/blastx")

    class _FakeProc:
        returncode = 2

        async def communicate(self, data):
            return b"", b"BLAST Database error: No alias or index file found"

    async def _fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    with pytest.raises(ToolError, match="failed with exit 2"):
        await _run_local_amr_blast(database="/data/card/db", sequence="ATGCATGC", evalue=1e-5, max_target_seqs=25)


async def test_run_local_amr_blast_happy_path_real_function(monkeypatch):
    import bioforge.tools.functional.amr_detection as mod

    monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/bin/blastx")

    class _FakeProc:
        returncode = 0

        async def communicate(self, data):
            return _tabular_line().encode("utf-8"), b""

    async def _fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    result = await _run_local_amr_blast(database="/data/card/db", sequence="ATGCATGC", evalue=1e-5, max_target_seqs=25)
    assert "ARO:3002999" in result


# --- Tool-level metadata stamping ------------------------------------------------


async def test_tool_stamped_via_registry_execute():
    from bioforge.tools.registry import execute_tool

    fake_settings = _make_settings()
    with patch("bioforge.tools.functional.amr_detection.settings", fake_settings):
        with patch(
            "bioforge.tools.functional.amr_detection._run_local_amr_blast",
            AsyncMock(return_value=_tabular_line()),
        ):
            result = await execute_tool("amr_detection", {"sequence": "ATGCATGCATGC" * 5})
    assert result.tool_name == "amr_detection"
    assert result.tool_version == "1.0.0"
