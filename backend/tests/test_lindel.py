"""Lindel edit-outcome integration — scaffolding tests (no Docker, no Lindel install).

Exercises the modern-side glue with injected fakes: a fake `run_fn` for the subprocess and
a monkeypatched `predict_lindel` for the edit_outcome wiring. Real numeric inference is
validated separately, inside the Lindel env (see models/lindel/legacy/README.md).
"""

from __future__ import annotations

import asyncio
import json

import bioforge.tools  # noqa: F401 — ensure tools are registered
import pytest
from bioforge.config import settings
from bioforge.tools.base import ToolError
from bioforge.tools.sequence.edit_outcome import EditOutcomeInput, _lindel_window, edit_outcome
from bioforge.tools.sequence.models.lindel import (
    LindelDistribution,
    LindelInferenceError,
    LindelUnavailable,
    predict_lindel,
)
from bioforge.tools.sequence.models.lindel.runner import build_command, run_inference

_W60 = "ACGT" * 15  # 60 bp, ACGT only (Lindel itself enforces the PAM/cut framing)


# --- runner: command construction ---------------------------------------------------


def test_build_command_docker() -> None:
    s = settings.model_copy(update={"lindel_runner": "docker", "lindel_docker_image": "img@sha256:dead"})
    argv = build_command(s)
    assert argv[0] == "docker"
    assert "img@sha256:dead" in argv
    assert argv[-1].endswith("lindel_infer.py")


def test_build_command_docker_requires_image() -> None:
    s = settings.model_copy(update={"lindel_runner": "docker", "lindel_docker_image": ""})
    with pytest.raises(LindelUnavailable):
        build_command(s)


def test_build_command_local() -> None:
    s = settings.model_copy(update={"lindel_runner": "local", "lindel_python": "/envs/lindel/bin/python"})
    argv = build_command(s)
    assert argv[0] == "/envs/lindel/bin/python"
    assert argv[1].endswith("lindel_infer.py")


def test_build_command_unknown_runner() -> None:
    with pytest.raises(LindelUnavailable):
        build_command(settings.model_copy(update={"lindel_runner": "weird"}))


# --- runner: protocol ---------------------------------------------------------------


def test_run_inference_happy_path() -> None:
    s = settings.model_copy(update={"lindel_runner": "local", "lindel_python": "py", "lindel_timeout_seconds": 9.0})
    captured: dict[str, object] = {}

    def fake_run(argv: list[str], stdin_text: str, timeout: float) -> str:
        captured["stdin"] = stdin_text
        captured["timeout"] = timeout
        return json.dumps({"results": [{"frameshift_ratio": 0.6, "predictions": {"3+0": 0.4}}]})

    payload = run_inference([_W60], s, run_fn=fake_run)
    assert payload["results"][0]["frameshift_ratio"] == 0.6
    assert json.loads(captured["stdin"])["sequences"] == [_W60]  # type: ignore[arg-type]
    assert captured["timeout"] == 9.0


def test_run_inference_length_mismatch() -> None:
    s = settings.model_copy(update={"lindel_runner": "local", "lindel_python": "py"})
    with pytest.raises(LindelInferenceError):
        run_inference([_W60], s, run_fn=lambda *_a: json.dumps({"results": []}))


def test_run_inference_error_payload() -> None:
    s = settings.model_copy(update={"lindel_runner": "local", "lindel_python": "py"})
    with pytest.raises(LindelInferenceError):
        run_inference([_W60], s, run_fn=lambda *_a: json.dumps({"error": "boom"}))


# --- inference orchestration --------------------------------------------------------


def test_predict_lindel_disabled_raises() -> None:
    s = settings.model_copy(update={"lindel_enabled": False})
    with pytest.raises(LindelUnavailable):
        predict_lindel(_W60, settings=s)


def test_predict_lindel_validates_window_length() -> None:
    s = settings.model_copy(update={"lindel_enabled": True, "lindel_runner": "local", "lindel_python": "py"})
    with pytest.raises(LindelInferenceError):
        predict_lindel("ACGT" * 10, settings=s)  # 40 bp, not 60


def test_predict_lindel_rejects_non_acgt() -> None:
    s = settings.model_copy(update={"lindel_enabled": True, "lindel_runner": "local", "lindel_python": "py"})
    with pytest.raises(LindelInferenceError):
        predict_lindel("N" + _W60[1:], settings=s)


def test_predict_lindel_happy() -> None:
    s = settings.model_copy(update={"lindel_enabled": True, "lindel_runner": "local", "lindel_python": "py"})
    dist = predict_lindel(
        _W60,
        settings=s,
        run_fn=lambda *_a: json.dumps(
            {"results": [{"frameshift_ratio": 0.72, "predictions": {"2+0": 0.3, "1+A": 0.1}}]}
        ),
    )
    assert isinstance(dist, LindelDistribution)
    assert dist.sequence_length == 60
    assert dist.frameshift_ratio == 0.72
    assert dist.predictions == {"2+0": 0.3, "1+A": 0.1}


# --- _lindel_window framing ---------------------------------------------------------


def test_lindel_window_is_60bp_with_pam_at_33() -> None:
    # cut at index 33 (+ strand): window = target[3:63]; PAM (NGG) lands at window[33:36].
    target = list("A" * 70)
    target[36:39] = list("TGG")  # target[cut+3 : cut+6] -> window[33:36]
    window = _lindel_window("".join(target), 33, "+")
    assert len(window) == 60
    assert window[33:36] == "TGG"


def test_lindel_window_minus_strand_is_60bp() -> None:
    window = _lindel_window("ACGT" * 25, 40, "-")  # 100 bp target, cut mid-sequence
    assert len(window) == 60
    assert set(window) <= set("ACGT")


def test_lindel_window_too_close_to_end_raises() -> None:
    with pytest.raises(ToolError):
        _lindel_window("A" * 40, 33, "+")  # end = 63 > 40


# --- edit_outcome wiring ------------------------------------------------------------

_LEFT = "ACGT" * 8  # 32 bp flank
_GUIDE = "GAGTCCGAGCAGAAGAAGAA"
_RIGHT = "ACGT" * 8
_TARGET = _LEFT + _GUIDE + "AGG" + _RIGHT


def test_edit_outcome_default_has_no_lindel_distribution() -> None:
    out = asyncio.run(edit_outcome(EditOutcomeInput(target=_TARGET, guide=_GUIDE, pam="NGG")))
    assert out.model_used == "rule_of_thumb"
    assert out.lindel_distribution is None
    assert out.outcomes  # rule_of_thumb still enumerates outcomes


def test_edit_outcome_lindel_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    import bioforge.tools.sequence.models.lindel as lindel_pkg

    monkeypatch.setattr(
        lindel_pkg,
        "predict_lindel",
        lambda _window, **_kw: LindelDistribution(
            sequence_length=60, frameshift_ratio=0.7, predictions={"3+0": 0.5, "1+A": 0.2}
        ),
    )
    out = asyncio.run(edit_outcome(EditOutcomeInput(target=_TARGET, guide=_GUIDE, pam="NGG", model="lindel")))
    assert out.model_used == "lindel"
    assert out.outcomes == []  # faithful: labels live in lindel_distribution, not remapped
    assert out.lindel_distribution is not None
    assert out.lindel_distribution.frameshift_ratio == 0.7
    assert out.lindel_distribution.predictions["3+0"] == 0.5
