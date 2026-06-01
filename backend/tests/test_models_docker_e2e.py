"""Docker-gated end-to-end tests for the out-of-process ML model wrappers.

Deselected by default (run with `-m docker`). Unlike the mocked unit tests in test_lindel.py
/ test_forecast.py, these run the REAL pinned images through the real runner + JSON protocol +
typed mapping -- the layer mocks cannot cover. They are the reproducibility net: a future
weight/image/commit bump that shifts the numbers will trip the tight-but-tolerant assertions.

Build the images first (see each models/.../legacy/README.md):
    docker build --build-arg LINDEL_COMMIT=fdcad580ba76bcfb7a98f58c3769b76f31693d63 \
        -t bioforge/lindel:legacy   backend/src/bioforge/tools/sequence/models/lindel/legacy
    docker build -t bioforge/forecast:legacy backend/src/bioforge/tools/sequence/models/forecast/legacy
    docker build -t bioforge/azimuth:legacy  backend/src/bioforge/tools/sequence/models/azimuth/legacy

Each test SKIPS (not fails) when Docker or the image is absent, so `-m docker` is safe to run
anywhere. The edit_outcome window/strand/pam wiring is covered by the mocked tests; here we
validate the actual model inference end-to-end.
"""

from __future__ import annotations

import math
import shutil
import subprocess

import pytest
from bioforge.config import settings

pytestmark = pytest.mark.docker

_LINDEL_IMAGE = "bioforge/lindel:legacy"
_FORECAST_IMAGE = "bioforge/forecast:legacy"
_AZIMUTH_IMAGE = "bioforge/azimuth:legacy"
_DEEPCRISPR_IMAGE = "bioforge/deepcrispr:legacy"


def _require_image(image: str) -> None:
    """Skip (don't fail) unless Docker is present and `image` has been built locally."""
    if shutil.which("docker") is None:
        pytest.skip("docker not on PATH")
    try:
        proc = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:  # pragma: no cover - environment guard
        pytest.skip(f"could not query docker images: {e}")
    if proc.returncode != 0:
        pytest.skip(f"image {image} not built (see the model's legacy/README.md)")


def test_lindel_real_image_end_to_end() -> None:
    """predict_lindel -> docker runner -> bioforge/lindel:legacy -> typed LindelDistribution.

    Reference: Lindel @ fdcad58 on its own example seq_1 -- frameshift 0.8912, top class
    `-2+4` ~ 0.309, full distribution sums to 1.0.
    """
    _require_image(_LINDEL_IMAGE)
    from bioforge.tools.sequence.models.lindel import LindelDistribution, predict_lindel

    s = settings.model_copy(
        update={"lindel_enabled": True, "lindel_runner": "docker", "lindel_docker_image": _LINDEL_IMAGE}
    )
    seq = "TAACGTTATCAACGCCTATATTAAAGCGACCGTCGGTTGAACTGCGTGGATCAATGCGTC"  # Lindel example seq_1 (60 bp)
    dist = predict_lindel(seq, settings=s)

    assert isinstance(dist, LindelDistribution)
    assert dist.sequence_length == 60
    assert abs(sum(dist.predictions.values()) - 1.0) < 1e-6
    assert abs(dist.frameshift_ratio - 0.8912) < 1e-3
    top_label, top_freq = max(dist.predictions.items(), key=lambda kv: kv[1])
    assert top_label == "-2+4"
    assert abs(top_freq - 0.309) < 5e-3


def test_forecast_real_image_end_to_end() -> None:
    """predict_forecast -> docker runner -> bioforge/forecast:legacy -> typed ForecastDistribution.

    Reference: top indel `I1_L-3C2R0` ~ 0.237 (228/961), the fixed `-` null placeholder
    dropped, distribution over real indels sums to 1.0.
    """
    _require_image(_FORECAST_IMAGE)
    from bioforge.tools.sequence.models.forecast import ForecastDistribution, predict_forecast

    s = settings.model_copy(
        update={"forecast_enabled": True, "forecast_runner": "docker", "forecast_docker_image": _FORECAST_IMAGE}
    )
    seq = "ACGT" * 8 + "GAGTCCGAGCAGAAGAAGAA" + "AGG" + "ACGT" * 8  # PAM "AGG" at 0-based index 52
    dist = predict_forecast(seq, 52, settings=s)

    assert isinstance(dist, ForecastDistribution)
    assert "-" not in dist.predictions  # the injected wild-type null is not a prediction
    assert abs(sum(dist.predictions.values()) - 1.0) < 1e-6
    top_label, top_freq = max(dist.predictions.items(), key=lambda kv: kv[1])
    assert top_label == "I1_L-3C2R0"
    assert abs(top_freq - 0.2373) < 5e-3


def test_azimuth_real_image_end_to_end() -> None:
    """predict_on_target -> docker runner -> bioforge/azimuth:legacy -> typed AzimuthOnTargetResult.

    Reference: Azimuth V3_model_nopos on the 30-mer GGGG+EMX1+AGG+TGG scores ~0.4889 (validated
    2026-05-30 against the Biomatters/Azimuth py3 port @ dbd30b9, scikit-learn 0.23.2). The model
    uses Azimuth's own featurizer + committed pickle, so this is the published RS2 output.
    """
    _require_image(_AZIMUTH_IMAGE)
    from bioforge.tools.sequence.models.azimuth import AzimuthOnTargetResult, predict_on_target

    s = settings.model_copy(
        update={"azimuth_enabled": True, "azimuth_runner": "docker", "azimuth_docker_image": _AZIMUTH_IMAGE}
    )
    thirtymer = "GGGG" + "GAGTCCGAGCAGAAGAAGAA" + "AGG" + "TGG"  # 4 + 20 + 3 + 3 = 30 nt

    result = predict_on_target([thirtymer], settings=s)
    assert isinstance(result, AzimuthOnTargetResult)
    assert len(result.scores) == 1
    assert result.scores[0].thirtymer == thirtymer
    assert 0.0 <= result.scores[0].score <= 1.0
    assert abs(result.scores[0].score - 0.4889) < 5e-3


@pytest.mark.online  # also needs the network (fetches the Chari-2015 eval set on first use)
def test_deepcrispr_chari2015_on_target_efficiency_e2e(tmp_path) -> None:
    """benchmarks.run_on_target_efficiency -> real DeepCRISPR image over all 1234 Chari-2015 guides.

    The §13 on-target accuracy benchmark end-to-end: fetch-on-first-use eval set (sha256-verified)
    -> one DeepCRISPR Docker call over the 1234 23-mers -> tie-aware numpy Spearman. Build the
    thin image first (FROM michaelchuai/deepcrispr:latest -- see models/deepcrispr/legacy/). The
    correlation is a CROSS-DATASET, leakage-UNVERIFIED rho; live value was 0.130 (2026-05-30), so
    the band brackets it without over-pinning a number this code path has not re-reproduced here.
    """
    _require_image(_DEEPCRISPR_IMAGE)
    from bioforge.benchmarks.effdata import EffDataFetchError
    from bioforge.benchmarks.on_target_efficiency import run_on_target_efficiency

    s = settings.model_copy(
        update={
            "deepcrispr_enabled": True,
            "deepcrispr_runner": "docker",
            "deepcrispr_docker_image": _DEEPCRISPR_IMAGE,
            "crispor_effdata_consent": True,
            "crispor_effdata_dir": str(tmp_path),
        }
    )
    try:
        result = run_on_target_efficiency("chari2015Train", model="deepcrispr", settings=s)
    except EffDataFetchError as e:  # network unreachable under bare -m docker -- skip, don't fail
        pytest.skip(f"could not fetch the Chari-2015 eval set: {e}")

    assert result.n == 1234
    assert len(result.pairs) == 1234
    # Honest labels travel with the number -- never a held-out claim, always cross-dataset framed.
    assert result.leakage_status == "unknown"
    assert result.dataset_relationship == "cross_dataset"
    # Deterministic scorer + pinned data: a finite, modest cross-dataset rho bracketing the live 0.130.
    assert not math.isnan(result.spearman_rho)
    assert 0.05 <= result.spearman_rho <= 0.25
