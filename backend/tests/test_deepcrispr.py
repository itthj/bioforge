"""DeepCRISPR on-target integration — tests (no Docker, no TensorFlow).

The modern-side glue is exercised with injected fakes: an in-memory tarball for the
(optional) weight fetcher, a fake `run_fn` for the subprocess, and a monkeypatched
`predict_on_target` for the score tool. Real numeric inference is validated separately in
the legacy environment (validated end-to-end against the authors' image, 2026-05-29 — see
models/deepcrispr/legacy/README.md).
"""

from __future__ import annotations

import asyncio
import io
import json
import tarfile
from pathlib import Path

import bioforge.tools  # noqa: F401 — ensure tools are registered
import pytest
from bioforge.config import settings
from bioforge.tools.base import ToolError
from bioforge.tools.sequence.models.deepcrispr import (
    DeepCRISPRFetchError,
    DeepCRISPRInferenceError,
    DeepCRISPRUnavailable,
    ensure_available,
    predict_on_target,
)
from bioforge.tools.sequence.models.deepcrispr import schema as dc_schema
from bioforge.tools.sequence.models.deepcrispr.runner import build_command, run_inference
from bioforge.tools.sequence.score_guide_on_target import (
    ScoreGuideOnTargetInput,
    _score_with_deepcrispr,
    score_guide_on_target,
)

_EMX1 = "GAGTCCGAGCAGAAGAAGAA"
_PAM = "AGG"
_GUIDE23 = _EMX1 + _PAM


def _make_tar_gz(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


# --- Fetcher (optional weight-fetch path; the official image bakes weights in) ------


def test_ensure_available_fetches_extracts_and_pins(tmp_path: Path) -> None:
    s = settings.model_copy(update={"deepcrispr_data_dir": str(tmp_path), "deepcrispr_upstream_commit": "testcommit"})
    blob = _make_tar_gz({"checkpoint": b"model-state\n", "ontar.ckpt.meta": b"meta\n"})
    calls: list[str] = []

    def fake_dl(url: str) -> bytes:
        calls.append(url)
        return blob

    paths = ensure_available("ontar_cnn_reg_seq", settings=s, download_fn=fake_dl)
    assert paths.model_dir.exists()
    assert (paths.model_dir / "checkpoint").read_bytes() == b"model-state\n"
    assert paths.archive_path.exists()
    assert (paths.data_dir / "pinned_hashes.json").exists()
    ensure_available("ontar_cnn_reg_seq", settings=s, download_fn=fake_dl)
    assert len(calls) == 1


def test_ensure_available_detects_lfs_pointer(tmp_path: Path) -> None:
    s = settings.model_copy(update={"deepcrispr_data_dir": str(tmp_path), "deepcrispr_upstream_commit": "c"})
    pointer = b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 999\n"
    with pytest.raises(DeepCRISPRFetchError) as ei:
        ensure_available(settings=s, download_fn=lambda _url: pointer)
    assert "lfs" in str(ei.value).lower()


def test_ensure_available_rejects_unknown_model(tmp_path: Path) -> None:
    s = settings.model_copy(update={"deepcrispr_data_dir": str(tmp_path)})
    with pytest.raises(DeepCRISPRFetchError):
        ensure_available("ontar_pt_cnn_reg", settings=s, download_fn=lambda _u: b"")  # type: ignore[arg-type]


# --- Runner: command construction (thin image is self-contained — no weight mount) --


def test_build_command_docker() -> None:
    s = settings.model_copy(update={"deepcrispr_runner": "docker", "deepcrispr_docker_image": "img@sha256:dead"})
    argv = build_command(s)
    assert argv[0] == "docker"
    assert "img@sha256:dead" in argv
    assert argv[-1].endswith("deepcrispr_infer.py")
    assert "-v" not in argv  # weights are baked into the image
    assert "--model-dir" not in argv


def test_build_command_docker_requires_image() -> None:
    s = settings.model_copy(update={"deepcrispr_runner": "docker", "deepcrispr_docker_image": ""})
    with pytest.raises(DeepCRISPRUnavailable):
        build_command(s)


def test_build_command_local() -> None:
    s = settings.model_copy(update={"deepcrispr_runner": "local", "deepcrispr_python": "/envs/dc/bin/python"})
    argv = build_command(s)
    assert argv[0] == "/envs/dc/bin/python"
    assert argv[1].endswith("deepcrispr_infer.py")


def test_build_command_unknown_runner() -> None:
    with pytest.raises(DeepCRISPRUnavailable):
        build_command(settings.model_copy(update={"deepcrispr_runner": "weird"}))


# --- Runner: protocol ---------------------------------------------------------------


def test_run_inference_happy_path() -> None:
    s = settings.model_copy(
        update={"deepcrispr_runner": "local", "deepcrispr_python": "py", "deepcrispr_timeout_seconds": 12.0}
    )
    captured: dict[str, object] = {}

    def fake_run(argv: list[str], stdin_text: str, timeout: float) -> str:
        captured["stdin"] = stdin_text
        captured["timeout"] = timeout
        return json.dumps({"model": "ontar_cnn_reg_seq", "scores": [0.5, 0.9]})

    payload = run_inference([_GUIDE23, "C" * 23], "ontar_cnn_reg_seq", s, run_fn=fake_run)
    assert payload["scores"] == [0.5, 0.9]
    assert json.loads(captured["stdin"])["guides"] == [_GUIDE23, "C" * 23]  # type: ignore[arg-type]
    assert captured["timeout"] == 12.0


def test_run_inference_length_mismatch() -> None:
    s = settings.model_copy(update={"deepcrispr_runner": "local", "deepcrispr_python": "py"})
    with pytest.raises(DeepCRISPRInferenceError):
        run_inference([_GUIDE23], "m", s, run_fn=lambda *_a: json.dumps({"scores": [0.1, 0.2]}))


def test_run_inference_error_payload() -> None:
    s = settings.model_copy(update={"deepcrispr_runner": "local", "deepcrispr_python": "py"})
    with pytest.raises(DeepCRISPRInferenceError):
        run_inference([_GUIDE23], "m", s, run_fn=lambda *_a: json.dumps({"error": "boom"}))


def test_run_inference_non_json() -> None:
    s = settings.model_copy(update={"deepcrispr_runner": "local", "deepcrispr_python": "py"})
    with pytest.raises(DeepCRISPRInferenceError):
        run_inference([_GUIDE23], "m", s, run_fn=lambda *_a: "not json")


# --- Inference orchestration --------------------------------------------------------


def test_predict_on_target_disabled_raises() -> None:
    s = settings.model_copy(update={"deepcrispr_enabled": False})
    with pytest.raises(DeepCRISPRUnavailable):
        predict_on_target([_GUIDE23], settings=s)


def test_predict_on_target_happy() -> None:
    s = settings.model_copy(
        update={
            "deepcrispr_enabled": True,
            "deepcrispr_runner": "local",
            "deepcrispr_python": "py",
            "deepcrispr_upstream_commit": "abc123",
        }
    )
    res = predict_on_target(
        [_GUIDE23, "ACGTACGTACGTACGTACGTGGG"],
        settings=s,
        run_fn=lambda _argv, _stdin, _timeout: json.dumps({"scores": [0.7, 0.3]}),
    )
    assert [sc.score for sc in res.scores] == [0.7, 0.3]
    assert res.scores[0].guide == _GUIDE23
    assert res.model_version == "ontar_cnn_reg_seq@abc123"


def test_predict_on_target_validates_guide_length() -> None:
    s = settings.model_copy(update={"deepcrispr_enabled": True})
    with pytest.raises(DeepCRISPRInferenceError):
        predict_on_target(["TOOSHORT"], settings=s)


# --- score_guide_on_target integration ----------------------------------------------


def test_score_with_deepcrispr_bad_pam_raises() -> None:
    with pytest.raises(ToolError):
        _score_with_deepcrispr(_EMX1, "NGG")  # N is not concrete ACGT
    with pytest.raises(ToolError):
        _score_with_deepcrispr(_EMX1, "AG")  # not 3 nt


def test_score_with_deepcrispr_unavailable_is_graceful() -> None:
    # Default settings have deepcrispr_enabled=False, so predict raises Unavailable; the
    # helper must degrade to (None, None, [caveat]) rather than propagate.
    score, version, caveats = _score_with_deepcrispr(_EMX1, _PAM)
    assert score is None
    assert version is None
    assert any("unavailable" in c.lower() for c in caveats)


def test_score_with_deepcrispr_success(monkeypatch: pytest.MonkeyPatch) -> None:
    import bioforge.tools.sequence.models.deepcrispr as dc

    def fake_predict(guides: list[str], **_kw: object) -> dc_schema.DeepCRISPROnTargetResult:
        return dc_schema.DeepCRISPROnTargetResult(
            model="ontar_cnn_reg_seq",
            model_version="ontar_cnn_reg_seq@xyz",
            scores=[dc_schema.DeepCRISPROnTargetScore(guide=guides[0], score=0.83)],
        )

    monkeypatch.setattr(dc, "predict_on_target", fake_predict)
    score, version, caveats = _score_with_deepcrispr(_EMX1, _PAM)
    assert score == 0.83
    assert version == "ontar_cnn_reg_seq@xyz"
    assert any("side-by-side" in c.lower() for c in caveats)
    assert any("out-of-distribution" in c.lower() for c in caveats)


def test_tool_rule_based_default_is_unchanged() -> None:
    out = asyncio.run(score_guide_on_target(ScoreGuideOnTargetInput(protospacer=_EMX1, pam=_PAM)))
    assert out.deepcrispr_on_target_score is None
    assert out.deepcrispr_model_version is None
    assert 0.0 <= out.on_target_score <= 1.0
    assert out.score_breakdown is not None


def test_tool_deepcrispr_mode_returns_both_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    import bioforge.tools.sequence.models.deepcrispr as dc

    monkeypatch.setattr(
        dc,
        "predict_on_target",
        lambda guides, **_kw: dc_schema.DeepCRISPROnTargetResult(
            model="ontar_cnn_reg_seq",
            model_version="ontar_cnn_reg_seq@xyz",
            scores=[dc_schema.DeepCRISPROnTargetScore(guide=guides[0], score=0.91)],
        ),
    )
    out = asyncio.run(score_guide_on_target(ScoreGuideOnTargetInput(protospacer=_EMX1, pam=_PAM, model="deepcrispr")))
    assert out.deepcrispr_on_target_score == 0.91
    assert out.deepcrispr_model_version == "ontar_cnn_reg_seq@xyz"
    assert 0.0 <= out.on_target_score <= 1.0
