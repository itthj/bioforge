"""Azimuth / Doench Rule Set 2 on-target integration -- tests (no Docker, no scikit-learn).

The modern-side glue is exercised with injected fakes: a fake `run_fn` for the subprocess and
a monkeypatched `predict_on_target` for the score tool. Real numeric inference is validated
separately in the legacy environment (SCAFFOLD -- not yet built; see models/azimuth/legacy).
"""

from __future__ import annotations

import asyncio
import json

import bioforge.tools  # noqa: F401 — ensure tools are registered
import pytest
from bioforge.config import settings
from bioforge.tools.base import ToolError
from bioforge.tools.sequence.models.azimuth import (
    AzimuthInferenceError,
    AzimuthUnavailable,
    predict_on_target,
)
from bioforge.tools.sequence.models.azimuth import schema as az_schema
from bioforge.tools.sequence.models.azimuth.runner import build_command, run_inference
from bioforge.tools.sequence.score_guide_on_target import (
    ScoreGuideOnTargetInput,
    _score_with_azimuth_rs2,
    score_guide_on_target,
)

_EMX1 = "GAGTCCGAGCAGAAGAAGAA"  # 20 nt, real EMX1 protospacer (as used in test_deepcrispr)
_THIRTYMER = "GGGG" + _EMX1 + "AGG" + "TGG"  # 4 + 20 + 3 + 3 = 30 nt; protospacer at offset 4
_THIRTYMER2 = "ACGT" + "ACGTACGTACGTACGTACGT" + "CGG" + "ACG"  # 30 nt, distinct protospacer


# --- Runner: command construction (env is self-contained — no weight mount) ----------


def test_build_command_docker() -> None:
    s = settings.model_copy(update={"azimuth_runner": "docker", "azimuth_docker_image": "img@sha256:dead"})
    argv = build_command(s)
    assert argv[0] == "docker"
    assert "img@sha256:dead" in argv
    assert argv[-1].endswith("azimuth_infer.py")
    assert "-v" not in argv  # weights are baked into the image


def test_build_command_docker_requires_image() -> None:
    s = settings.model_copy(update={"azimuth_runner": "docker", "azimuth_docker_image": ""})
    with pytest.raises(AzimuthUnavailable):
        build_command(s)


def test_build_command_local() -> None:
    s = settings.model_copy(update={"azimuth_runner": "local", "azimuth_python": "/envs/az/bin/python"})
    argv = build_command(s)
    assert argv[0] == "/envs/az/bin/python"
    assert argv[1].endswith("azimuth_infer.py")


def test_build_command_unknown_runner() -> None:
    with pytest.raises(AzimuthUnavailable):
        build_command(settings.model_copy(update={"azimuth_runner": "weird"}))


# --- Runner: protocol ---------------------------------------------------------------


def test_run_inference_happy_path() -> None:
    s = settings.model_copy(update={"azimuth_runner": "local", "azimuth_python": "py", "azimuth_timeout_seconds": 9.0})
    captured: dict[str, object] = {}

    def fake_run(argv: list[str], stdin_text: str, timeout: float) -> str:
        captured["stdin"] = stdin_text
        captured["timeout"] = timeout
        return json.dumps({"model": "V3_model_nopos", "scores": [0.42, 0.6]})

    payload = run_inference([_THIRTYMER, _THIRTYMER2], "V3_model_nopos", s, run_fn=fake_run)
    assert payload["scores"] == [0.42, 0.6]
    assert json.loads(captured["stdin"])["thirtymers"] == [_THIRTYMER, _THIRTYMER2]  # type: ignore[arg-type]
    assert captured["timeout"] == 9.0


def test_run_inference_length_mismatch() -> None:
    s = settings.model_copy(update={"azimuth_runner": "local", "azimuth_python": "py"})
    with pytest.raises(AzimuthInferenceError):
        run_inference([_THIRTYMER], "m", s, run_fn=lambda *_a: json.dumps({"scores": [0.1, 0.2]}))


def test_run_inference_error_payload() -> None:
    s = settings.model_copy(update={"azimuth_runner": "local", "azimuth_python": "py"})
    with pytest.raises(AzimuthInferenceError):
        run_inference([_THIRTYMER], "m", s, run_fn=lambda *_a: json.dumps({"error": "boom"}))


def test_run_inference_non_json() -> None:
    s = settings.model_copy(update={"azimuth_runner": "local", "azimuth_python": "py"})
    with pytest.raises(AzimuthInferenceError):
        run_inference([_THIRTYMER], "m", s, run_fn=lambda *_a: "not json")


# --- Inference orchestration --------------------------------------------------------


def test_predict_on_target_disabled_raises() -> None:
    s = settings.model_copy(update={"azimuth_enabled": False})
    with pytest.raises(AzimuthUnavailable):
        predict_on_target([_THIRTYMER], settings=s)


def test_predict_on_target_happy() -> None:
    s = settings.model_copy(
        update={
            "azimuth_enabled": True,
            "azimuth_runner": "local",
            "azimuth_python": "py",
            "azimuth_upstream_commit": "abc123",
        }
    )
    res = predict_on_target(
        [_THIRTYMER, _THIRTYMER2],
        settings=s,
        run_fn=lambda _argv, _stdin, _timeout: json.dumps({"scores": [0.7, 0.3]}),
    )
    assert [sc.score for sc in res.scores] == [0.7, 0.3]
    assert res.scores[0].thirtymer == _THIRTYMER
    assert res.model_version == "V3_model_nopos@abc123"


def test_predict_on_target_validates_length() -> None:
    s = settings.model_copy(update={"azimuth_enabled": True})
    with pytest.raises(AzimuthInferenceError):
        predict_on_target(["TOOSHORT"], settings=s)


# --- score_guide_on_target integration ----------------------------------------------


def test_score_with_azimuth_rs2_missing_thirtymer_raises() -> None:
    # No 30-mer → fixable input error (never fabricate the flanking context).
    with pytest.raises(ToolError):
        _score_with_azimuth_rs2(_EMX1, "")


def test_score_with_azimuth_rs2_bad_length_raises() -> None:
    with pytest.raises(ToolError):
        _score_with_azimuth_rs2(_EMX1, "ACGT")  # not 30 nt


def test_score_with_azimuth_rs2_mismatched_protospacer_raises() -> None:
    # A 30-mer whose embedded protospacer (offset 4) does not match → soundness ToolError.
    with pytest.raises(ToolError):
        _score_with_azimuth_rs2(_EMX1, _THIRTYMER2)


def test_score_with_azimuth_rs2_unavailable_is_graceful() -> None:
    # Default settings have azimuth_enabled=False, so predict raises Unavailable; the helper
    # must degrade to (None, None, [caveat]) rather than propagate.
    score, version, caveats = _score_with_azimuth_rs2(_EMX1, _THIRTYMER)
    assert score is None
    assert version is None
    assert any("unavailable" in c.lower() for c in caveats)


def test_score_with_azimuth_rs2_success(monkeypatch: pytest.MonkeyPatch) -> None:
    import bioforge.tools.sequence.models.azimuth as az

    def fake_predict(thirtymers: list[str], **_kw: object) -> az_schema.AzimuthOnTargetResult:
        return az_schema.AzimuthOnTargetResult(
            model="V3_model_nopos",
            model_version="V3_model_nopos@xyz",
            scores=[az_schema.AzimuthOnTargetScore(thirtymer=thirtymers[0], score=0.55)],
        )

    monkeypatch.setattr(az, "predict_on_target", fake_predict)
    score, version, caveats = _score_with_azimuth_rs2(_EMX1, _THIRTYMER)
    assert score == 0.55
    assert version == "V3_model_nopos@xyz"
    assert any("secondary" in c.lower() for c in caveats)
    assert any("out-of-distribution" in c.lower() for c in caveats)


def test_tool_rule_based_default_leaves_azimuth_none() -> None:
    out = asyncio.run(score_guide_on_target(ScoreGuideOnTargetInput(protospacer=_EMX1, pam="AGG")))
    assert out.azimuth_rs2_on_target_score is None
    assert out.azimuth_rs2_model_version is None
    assert 0.0 <= out.on_target_score <= 1.0


def test_tool_azimuth_rs2_mode_returns_both_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    import bioforge.tools.sequence.models.azimuth as az

    monkeypatch.setattr(
        az,
        "predict_on_target",
        lambda thirtymers, **_kw: az_schema.AzimuthOnTargetResult(
            model="V3_model_nopos",
            model_version="V3_model_nopos@xyz",
            scores=[az_schema.AzimuthOnTargetScore(thirtymer=thirtymers[0], score=0.66)],
        ),
    )
    out = asyncio.run(
        score_guide_on_target(
            ScoreGuideOnTargetInput(protospacer=_EMX1, pam="AGG", model="azimuth_rs2", thirtymer=_THIRTYMER)
        )
    )
    assert out.azimuth_rs2_on_target_score == 0.66
    assert out.azimuth_rs2_model_version == "V3_model_nopos@xyz"
    assert 0.0 <= out.on_target_score <= 1.0
