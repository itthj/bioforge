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
