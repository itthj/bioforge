# Core-only MAFFT image for `align_msa` (multiple-sequence alignment).
#
# License: Debian's `mafft` package is CORE MAFFT (BSD-3-Clause; Katoh & Standley 2013).
# It does NOT bundle the restrictively-licensed Vienna RNA / MXSCARNA extensions (those live
# in a separate non-free path), so this image is commercial-clean -- exactly what the
# models/mafft/legacy/README.md requires. Do not add the extensions.
#
# The align_msa runner invokes:  docker run --rm -i <image> mafft --auto --quiet -
# (FASTA on stdin, aligned FASTA on stdout), so we only need `mafft` on PATH.
#
# Build + pin by digest (rule 19), then enable:
#   docker build -t bioforge/mafft:legacy backend/src/bioforge/tools/sequence/models/mafft/legacy
#   docker inspect --format '{{index .RepoDigests 0}}' bioforge/mafft:legacy   # if pushed to a registry
#   export BIOFORGE_MAFFT_ENABLED=true
#   export BIOFORGE_MAFFT_DOCKER_IMAGE=bioforge/mafft:legacy   # or the @sha256 digest form
FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends mafft \
    && rm -rf /var/lib/apt/lists/*

# Sanity: fail the build if this base ever started shipping the non-free extensions.
RUN test ! -e /usr/bin/mafft-xinsi && mafft --version

ENTRYPOINT []
CMD ["mafft", "--help"]
