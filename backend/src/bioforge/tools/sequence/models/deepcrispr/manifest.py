"""Upstream artifacts DeepCRISPR (Chuai 2018) needs for seq-only on-target inference.

We integrate ONLY the sequence-feature-only CNN regression model
(`ontar_cnn_reg_seq`). The 8-channel epigenetic models (CTCF / DNase / H3K4me3 /
RRBS) are deliberately out of scope: BioForge does not have per-locus epigenetic
tracks for arbitrary targets, so feeding them would be fabricating inputs.

DeepCRISPR is Apache-2.0, so unlike inDelphi there is NO consent gate. The weights
live in the upstream repo under `trained_models/<model>.tar.gz` and are fetched +
extracted on first use, pinned to a commit for reproducible provenance.

The on-target input is a 23 bp window: the 20 nt protospacer plus its 3 nt PAM
(`[batch, 4, 1, 23]` one-hot for the seq-only model). That 23 bp framing is why the
score tool needs the PAM, not just the protospacer, in DeepCRISPR mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Only the sequence-only on-target regression model is wired up.
OnTargetModel = Literal["ontar_cnn_reg_seq"]
SUPPORTED_ONTARGET_MODELS: tuple[OnTargetModel, ...] = ("ontar_cnn_reg_seq",)

# DeepCRISPR on-target input window: 20 nt protospacer + 3 nt PAM.
GUIDE_LENGTH_BP = 23
PROTOSPACER_LENGTH_BP = 20
PAM_LENGTH_BP = 3

_TRAINED_MODELS_DIR = "trained_models"
_REPO = "bm2-lab/DeepCRISPR"


@dataclass(frozen=True)
class UpstreamArchive:
    """One weights tarball to fetch from the upstream repo.

    `upstream_relpath` is the path within the repo at the pinned commit;
    `local_archive_relpath` is where the tarball lands under the data dir;
    `extract_dirname` is the directory the archive is extracted into (the
    `on_target_model_dir` handed to DeepCRISPR's `DCModelOntar`).
    """

    upstream_relpath: str
    local_archive_relpath: str
    extract_dirname: str


def required_archive(model: OnTargetModel) -> UpstreamArchive:
    return UpstreamArchive(
        upstream_relpath=f"{_TRAINED_MODELS_DIR}/{model}.tar.gz",
        local_archive_relpath=f"{model}.tar.gz",
        extract_dirname=model,
    )


def raw_url(commit_sha: str, upstream_relpath: str) -> str:
    """raw.githubusercontent.com URL for a file at a pinned commit.

    Serves the real bytes only if the file is committed normally. If the tarball
    is tracked with Git LFS, this returns a small pointer file instead — the
    fetcher detects that and raises with the LFS media URL below.
    """
    return f"https://raw.githubusercontent.com/{_REPO}/{commit_sha}/{upstream_relpath}"


def lfs_media_url(commit_sha: str, upstream_relpath: str) -> str:
    """github.com `/raw/` URL, which resolves Git-LFS pointers to real media."""
    return f"https://github.com/{_REPO}/raw/{commit_sha}/{upstream_relpath}"
