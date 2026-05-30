"""Rule 19 / section 10 — reproducibility: external container images are digest-pinned.

The blueprint mandates every external-tool image be pinned by @sha256 digest, NEVER :latest
(an unpinned base silently makes a build unreproducible). This guard scans the deployable
compose stack + the model legacy Dockerfiles so a regression fails CI instead of shipping.

Scope: forbids :latest everywhere, and requires @sha256 on the compose service images and on
the external bases the thin model images build FROM. Versioned bases like python:3.10-slim
(not :latest) are allowed; pinning those to digest is a possible follow-up.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO / "docker-compose.yml"
_MODELS = _REPO / "backend/src/bioforge/tools/sequence/models"
_EXTERNAL_BASE_DOCKERFILES = [
    _MODELS / "deepcrispr/legacy/Dockerfile",
    _MODELS / "forecast/legacy/Dockerfile",
]
_ALL_MODEL_DOCKERFILES = [*_EXTERNAL_BASE_DOCKERFILES, _MODELS / "lindel/legacy/Dockerfile"]


def _compose_image_refs() -> list[str]:
    return [
        line.split("image:", 1)[1].strip()
        for line in _COMPOSE.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("image:")
    ]


def _from_refs(dockerfile: Path) -> list[str]:
    return [
        line.strip()[len("FROM ") :].strip()
        for line in dockerfile.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("FROM ")
    ]


def test_compose_declares_external_images() -> None:
    # Guard the guard: a vacuous pass if compose stops declaring image: refs.
    assert _compose_image_refs(), "expected at least one image: ref in docker-compose.yml"


def test_no_latest_tag_anywhere() -> None:
    offenders = [r for r in _compose_image_refs() if ":latest" in r]
    for dockerfile in _ALL_MODEL_DOCKERFILES:
        offenders += [f"{dockerfile.name}: {r}" for r in _from_refs(dockerfile) if ":latest" in r]
    assert not offenders, f":latest is forbidden (rule 19); offenders: {offenders}"


def test_compose_images_digest_pinned() -> None:
    for ref in _compose_image_refs():
        assert "@sha256:" in ref, f"compose image not digest-pinned (rule 19): {ref!r}"


def test_external_model_bases_digest_pinned() -> None:
    for dockerfile in _EXTERNAL_BASE_DOCKERFILES:
        froms = _from_refs(dockerfile)
        assert froms, f"no FROM in {dockerfile}"
        for ref in froms:
            assert "@sha256:" in ref, f"{dockerfile.name} base not digest-pinned (rule 19): {ref!r}"
