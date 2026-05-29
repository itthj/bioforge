"""v4 §4.2 tool-registry metadata + the §6 uncertainty-honesty helper.

The metadata fields are optional (existing tools keep registering unchanged), and
`uncertainty_note` encodes the honesty rule: report only the uncertainty a model actually
provides, never a fabricated interval or accuracy figure.
"""

from __future__ import annotations

from bioforge.tools.base import ToolInput, ToolOutput, ToolSpec, uncertainty_note
from bioforge.tools.registry import get_tool

# --- Metadata on the registry -------------------------------------------------------


def test_existing_tool_without_metadata_defaults_empty() -> None:
    spec = get_tool("gc_content")
    assert spec.model_versions == {}
    assert spec.emits_instance_uncertainty == {}
    assert spec.published_accuracy == {}
    assert spec.training_distribution == {}
    assert spec.reference_data_keys == []


def test_populated_tool_carries_metadata() -> None:
    spec = get_tool("score_guide_on_target")
    assert spec.emits_instance_uncertainty == {"on_target": False}
    assert "VERIFY" in spec.published_accuracy["on_target"]  # unsourced figures are stubbed, not guessed
    assert spec.training_distribution["guide_length_nt"] == 20


def test_reference_data_keys_populated_on_db_backed_tools() -> None:
    # DB-backed tools declare the external reference they depend on (future §10 pin target).
    assert get_tool("lookup_clinvar").reference_data_keys == ["ncbi_clinvar"]
    assert get_tool("lookup_gnomad").reference_data_keys == ["gnomad"]
    assert get_tool("annotate_variant").reference_data_keys == ["ensembl_vep"]
    assert get_tool("fetch_pdb_structure").reference_data_keys == ["rcsb_pdb"]


def test_pure_transforms_stay_empty() -> None:
    # No model and no external reference → metadata is honestly empty, not invented.
    for name in ("reverse_complement", "translate", "format_hgvs"):
        spec = get_tool(name)
        assert spec.model_versions == {}
        assert spec.published_accuracy == {}
        assert spec.reference_data_keys == []


def test_scoring_tool_metadata_drives_uncertainty_note() -> None:
    # find_offtargets owns the Hsu-2013 MIT score; the §6 note must surface the
    # sourced/VERIFY accuracy, never a fabricated per-prediction interval.
    spec = get_tool("find_offtargets")
    assert spec.emits_instance_uncertainty == {"mit_offtarget": False}
    note = uncertainty_note(spec, "mit_offtarget")
    assert "point estimate only" in note
    assert "VERIFY" in note


def test_edit_outcome_declares_both_models() -> None:
    spec = get_tool("edit_outcome")
    assert "VERIFY" in spec.published_accuracy["rule_of_thumb"]
    assert "VERIFY" in spec.published_accuracy["indelphi"]
    # inDelphi's training distribution records the supported cell types (non-empty).
    assert spec.training_distribution["indelphi"]["cell_types"]


# --- The honesty helper -------------------------------------------------------------


class _In(ToolInput):
    pass


class _Out(ToolOutput):
    pass


async def _handler(_inp: _In) -> _Out:  # pragma: no cover - never invoked
    return _Out()


def _spec(**kwargs) -> ToolSpec:
    return ToolSpec(
        name="t",
        description="d",
        input_model=_In,
        output_model=_Out,
        handler=_handler,
        version="1.0.0",
        **kwargs,
    )


def test_note_reports_emitted_instance_uncertainty() -> None:
    note = uncertainty_note(_spec(emits_instance_uncertainty={"score": True}), "score")
    assert "instance-level uncertainty is provided" in note


def test_note_reports_published_accuracy_when_no_instance_uncertainty() -> None:
    note = uncertainty_note(_spec(published_accuracy={"score": "Spearman 0.85 (Author 2019)"}), "score")
    assert "point estimate only" in note
    assert "Spearman 0.85 (Author 2019)" in note


def test_note_is_explicit_when_nothing_is_recorded() -> None:
    note = uncertainty_note(_spec(), "score")
    assert "no per-prediction interval" in note
    assert "no published accuracy" in note


def test_note_never_invents_a_number() -> None:
    # With nothing declared, the note must contain no fabricated digits.
    note = uncertainty_note(_spec(), "score")
    assert not any(ch.isdigit() for ch in note)
