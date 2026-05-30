"""FORECasT edit-outcome integration — scaffolding tests (no Docker, no FORECasT install).

Exercises the modern-side glue with injected fakes: a fake `run_fn` for the subprocess and
a monkeypatched `predict_forecast` for the edit_outcome wiring. Real numeric inference is
validated separately, inside the FORECasT env (see models/forecast/legacy/README.md).
"""

from __future__ import annotations

import asyncio
import json

import bioforge.tools  # noqa: F401 — ensure tools are registered
import pytest
from bioforge.config import settings
from bioforge.tools.sequence.edit_outcome import EditOutcomeInput, edit_outcome
from bioforge.tools.sequence.models.forecast import (
    ForecastDistribution,
    ForecastInferenceError,
    ForecastUnavailable,
    predict_forecast,
)
from bioforge.tools.sequence.models.forecast.runner import build_command, run_inference

_GUIDE = "GAGTCCGAGCAGAAGAAGAA"
_TARGET = "ACGT" * 8 + _GUIDE + "AGG" + "ACGT" * 8  # 87 bp, PAM ("AGG") at index 52
_PAM_INDEX = 52


# --- runner: command construction ---------------------------------------------------


def test_build_command_docker() -> None:
    # The thin image bakes the wrapper in, so no bind-mount is constructed.
    s = settings.model_copy(update={"forecast_runner": "docker", "forecast_docker_image": "img@sha256:dead"})
    argv = build_command(s)
    assert argv[0] == "docker"
    assert "img@sha256:dead" in argv
    assert argv[-1].endswith("forecast_infer.py")
    assert "-v" not in argv  # wrapper baked into the thin image, no bind-mount


def test_build_command_docker_requires_image() -> None:
    s = settings.model_copy(update={"forecast_runner": "docker", "forecast_docker_image": ""})
    with pytest.raises(ForecastUnavailable):
        build_command(s)


def test_build_command_local() -> None:
    s = settings.model_copy(update={"forecast_runner": "local", "forecast_python": "/envs/forecast/bin/python"})
    argv = build_command(s)
    assert argv[0] == "/envs/forecast/bin/python"
    assert argv[1].endswith("forecast_infer.py")


def test_build_command_unknown_runner() -> None:
    with pytest.raises(ForecastUnavailable):
        build_command(settings.model_copy(update={"forecast_runner": "weird"}))


# --- runner: protocol ---------------------------------------------------------------


def test_run_inference_happy_path() -> None:
    s = settings.model_copy(
        update={"forecast_runner": "local", "forecast_python": "py", "forecast_timeout_seconds": 8.0}
    )
    captured: dict[str, object] = {}

    def fake_run(argv: list[str], stdin_text: str, timeout: float) -> str:
        captured["stdin"] = stdin_text
        captured["timeout"] = timeout
        return json.dumps({"results": [{"predictions": {"D3_L-4": 0.6, "I1_A": 0.4}}]})

    payload = run_inference([{"sequence": _TARGET, "pam_index": _PAM_INDEX}], s, run_fn=fake_run)
    assert payload["results"][0]["predictions"]["D3_L-4"] == 0.6
    assert json.loads(captured["stdin"])["requests"][0]["pam_index"] == _PAM_INDEX  # type: ignore[index]
    assert captured["timeout"] == 8.0


def test_run_inference_length_mismatch() -> None:
    s = settings.model_copy(update={"forecast_runner": "local", "forecast_python": "py"})
    with pytest.raises(ForecastInferenceError):
        run_inference(
            [{"sequence": _TARGET, "pam_index": _PAM_INDEX}], s, run_fn=lambda *_a: json.dumps({"results": []})
        )


def test_run_inference_error_payload() -> None:
    s = settings.model_copy(update={"forecast_runner": "local", "forecast_python": "py"})
    with pytest.raises(ForecastInferenceError):
        run_inference(
            [{"sequence": _TARGET, "pam_index": _PAM_INDEX}], s, run_fn=lambda *_a: json.dumps({"error": "boom"})
        )


# --- inference orchestration --------------------------------------------------------


def test_predict_forecast_disabled_raises() -> None:
    s = settings.model_copy(update={"forecast_enabled": False})
    with pytest.raises(ForecastUnavailable):
        predict_forecast(_TARGET, _PAM_INDEX, settings=s)


def test_predict_forecast_rejects_short_or_nonacgt() -> None:
    s = settings.model_copy(update={"forecast_enabled": True, "forecast_runner": "local", "forecast_python": "py"})
    with pytest.raises(ForecastInferenceError):
        predict_forecast("ACGT", 0, settings=s)  # too short
    with pytest.raises(ForecastInferenceError):
        predict_forecast("N" * 30, 10, settings=s)  # non-ACGT


def test_predict_forecast_rejects_bad_pam_index() -> None:
    s = settings.model_copy(update={"forecast_enabled": True, "forecast_runner": "local", "forecast_python": "py"})
    with pytest.raises(ForecastInferenceError):
        predict_forecast(_TARGET, len(_TARGET), settings=s)  # PAM would run off the end


def test_predict_forecast_happy() -> None:
    s = settings.model_copy(update={"forecast_enabled": True, "forecast_runner": "local", "forecast_python": "py"})
    dist = predict_forecast(
        _TARGET,
        _PAM_INDEX,
        settings=s,
        run_fn=lambda *_a: json.dumps({"results": [{"predictions": {"D2_L-2": 0.7, "I1_T": 0.3}}]}),
    )
    assert isinstance(dist, ForecastDistribution)
    assert dist.sequence_length == len(_TARGET)
    assert dist.predictions == {"D2_L-2": 0.7, "I1_T": 0.3}


# --- edit_outcome wiring ------------------------------------------------------------


def test_edit_outcome_default_has_no_forecast_distribution() -> None:
    out = asyncio.run(edit_outcome(EditOutcomeInput(target=_TARGET, guide=_GUIDE, pam="NGG")))
    assert out.model_used == "rule_of_thumb"
    assert out.forecast_distribution is None


def test_edit_outcome_forecast_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    import bioforge.tools.sequence.models.forecast as forecast_pkg

    monkeypatch.setattr(
        forecast_pkg,
        "predict_forecast",
        lambda _seq, _pam, **_kw: ForecastDistribution(sequence_length=len(_TARGET), predictions={"D3_L-4": 0.8}),
    )
    out = asyncio.run(edit_outcome(EditOutcomeInput(target=_TARGET, guide=_GUIDE, pam="NGG", model="forecast")))
    assert out.model_used == "forecast"
    assert out.outcomes == []  # faithful: labels live in forecast_distribution, not remapped
    assert out.forecast_distribution is not None
    assert out.forecast_distribution.predictions["D3_L-4"] == 0.8
