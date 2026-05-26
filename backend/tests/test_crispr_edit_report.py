from __future__ import annotations

from types import SimpleNamespace

import pytest
from bioforge.tools.registry import REGISTRY
from bioforge.tools.sequence import blast as blast_module
from bioforge.tools.sequence.crispr_edit_report import (
    CrisprEditReportInput,
    crispr_edit_report,
)

_GUIDE = "ACGTACGTACGTACGTACGT"
_TARGET = "C" * 30 + _GUIDE + "AGG" + "T" * 30


def _fake_hsp(*, identities=20, align_length=20) -> SimpleNamespace:
    return SimpleNamespace(
        expect=1e-10,
        bits=40.0,
        identities=identities,
        align_length=align_length,
        query_start=1,
        query_end=align_length,
        sbjct_start=1001,
        sbjct_end=1001 + align_length - 1,
    )


def _fake_alignment(*, accession: str, identities: int) -> SimpleNamespace:
    return SimpleNamespace(
        accession=accession,
        hit_def=f"{accession} candidate [Homo sapiens]",
        hsps=[_fake_hsp(identities=identities)],
    )


def _fake_record(alignments: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(alignments=alignments)


@pytest.fixture
def patch_ncbi(monkeypatch):
    holder: dict = {"response": None, "calls": []}

    async def _fake_run(*, program, database, sequence, expect, hitlist_size, task=None):
        holder["calls"].append(
            {
                "program": program,
                "database": database,
                "sequence": sequence,
                "expect": expect,
                "hitlist_size": hitlist_size,
                "task": task,
            }
        )
        return holder["response"]

    monkeypatch.setattr(blast_module, "_run_ncbi_blast", _fake_run)

    def setter(response):
        holder["response"] = response

    setter.calls = holder["calls"]
    return setter


async def test_report_designs_guides_and_simulates_edit_without_offtargets() -> None:
    out = await crispr_edit_report(CrisprEditReportInput(target=_TARGET))

    assert out.recommended_guide is not None
    assert out.recommended_guide.protospacer
    assert out.recommended_guide.on_target_score is not None
    assert out.recommended_guide.edit_outcome_summary is not None
    assert out.recommended_guide.edit_outcome_summary.frameshift_probability > 0
    assert out.recommended_guide.off_target_summary.searched is False
    assert "find_offtargets" not in out.tool_chain
    assert any("Off-target search was not run" in c for c in out.caveats)


async def test_report_runs_offtarget_search_when_requested(patch_ncbi) -> None:
    patch_ncbi(
        (
            _fake_record(
                [
                    _fake_alignment(accession="HIGH", identities=20),
                    _fake_alignment(accession="MED", identities=17),
                ]
            ),
            "RID-REPORT",
        )
    )

    out = await crispr_edit_report(
        CrisprEditReportInput(
            target=_TARGET,
            run_offtarget_search=True,
            offtarget_database="refseq_genomic",
            max_offtarget_hits=5,
        )
    )

    assert out.recommended_guide is not None
    assert "find_offtargets" in out.tool_chain
    off = out.recommended_guide.off_target_summary
    assert off.searched is True
    assert off.database == "refseq_genomic"
    assert off.high_risk_count == 1
    assert off.medium_risk_count == 1
    assert patch_ncbi.calls[0]["task"] == "blastn-short"
    assert patch_ncbi.calls[0]["database"] == "refseq_genomic"


async def test_high_risk_offtargets_push_label_to_caution_or_avoid(patch_ncbi) -> None:
    patch_ncbi(
        (
            _fake_record(
                [
                    _fake_alignment(accession="HIGH1", identities=20),
                    _fake_alignment(accession="HIGH2", identities=19),
                ]
            ),
            "RID-HIGH",
        )
    )

    out = await crispr_edit_report(CrisprEditReportInput(target=_TARGET, run_offtarget_search=True))

    assert out.recommended_guide is not None
    assert out.recommended_guide.recommendation_label == "avoid"
    assert out.recommended_guide.recommendation_score < 0.8


async def test_no_pam_returns_empty_report_with_design_notes() -> None:
    out = await crispr_edit_report(CrisprEditReportInput(target="A" * 80))

    assert out.recommended_guide is None
    assert out.guides == []
    assert out.num_guides_considered == 0
    assert out.tool_chain == ["design_guides"]
    assert any("No NGG PAM" in caveat for caveat in out.caveats)


async def test_simulate_top_n_zero_skips_edit_outcome_summary() -> None:
    out = await crispr_edit_report(CrisprEditReportInput(target=_TARGET, simulate_top_n=0))

    assert out.recommended_guide is not None
    assert out.recommended_guide.edit_outcome_summary is None
    assert "edit_outcome" in out.tool_chain


def test_crispr_edit_report_is_registered_as_expensive() -> None:
    spec = REGISTRY["crispr_edit_report"]
    assert spec.cost_hint == "expensive"
    assert {"crispr", "workflow", "report"}.issubset(set(spec.tags))
