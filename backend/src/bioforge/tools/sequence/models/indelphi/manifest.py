"""The set of upstream files inDelphi needs at inference time.

Path layout MIRRORS the upstream repo so that loading code can reference files
by their original names (the model loader builds paths from `celltype` and
file-name conventions baked into inDelphi.py).

We pin the `model-sklearn-0.20.0/` directory rather than `0.18.1/` because
modern sklearn (>=1.0) reads 0.20-era pickles with only deprecation warnings,
while 0.18.1 pickles fail outright on modern numpy ABI.

The set of cell types ships small (mESC only) so the consent-gated download is
fast on first use. Adding U2OS / HEK293 / HCT116 / K562 later is a one-line
manifest change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CellType = Literal["mESC", "U2OS", "HEK293", "HCT116", "K562"]

# Files we need from the upstream repo. Paths are relative to the repo root
# at the pinned commit. `category` distinguishes shared model files (loaded
# once) from per-celltype files (loaded based on the chosen cell type).
SUPPORTED_CELLTYPES: tuple[CellType, ...] = ("mESC",)

_SKLEARN_DIR = "model-sklearn-0.20.0"


@dataclass(frozen=True)
class UpstreamFile:
    """One file to fetch from the upstream inDelphi repo.

    `local_relpath` is where the file ends up under the BioForge data dir; we
    preserve the upstream layout so the loader can address files using the
    same name patterns inDelphi.py expects.
    """

    upstream_relpath: str
    local_relpath: str


def shared_files() -> tuple[UpstreamFile, ...]:
    """Files that all cell types use — fetched once."""
    return (
        UpstreamFile("inDelphi.py", "inDelphi.py"),
        UpstreamFile(f"{_SKLEARN_DIR}/aax_aag_nn.pkl", f"{_SKLEARN_DIR}/aax_aag_nn.pkl"),
        UpstreamFile(f"{_SKLEARN_DIR}/aax_aag_nn2.pkl", f"{_SKLEARN_DIR}/aax_aag_nn2.pkl"),
    )


def celltype_files(celltype: CellType) -> tuple[UpstreamFile, ...]:
    """Files specific to one cell type — fetched per `predict()` cell type."""
    return (
        UpstreamFile(
            f"{_SKLEARN_DIR}/bp_model_{celltype}.pkl",
            f"{_SKLEARN_DIR}/bp_model_{celltype}.pkl",
        ),
        UpstreamFile(
            f"{_SKLEARN_DIR}/rate_model_{celltype}.pkl",
            f"{_SKLEARN_DIR}/rate_model_{celltype}.pkl",
        ),
        UpstreamFile(
            f"{_SKLEARN_DIR}/Normalizer_{celltype}.pkl",
            f"{_SKLEARN_DIR}/Normalizer_{celltype}.pkl",
        ),
    )


def required_files(celltype: CellType) -> tuple[UpstreamFile, ...]:
    return shared_files() + celltype_files(celltype)


def model_dir_relpath() -> str:
    """The sklearn model subdir inside the data dir — what inDelphi.init_model wants."""
    return _SKLEARN_DIR


def raw_url(commit_sha: str, upstream_relpath: str) -> str:
    """Return the raw.githubusercontent.com URL for a given file + commit."""
    return f"https://raw.githubusercontent.com/maxwshen/inDelphi-model/{commit_sha}/{upstream_relpath}"
