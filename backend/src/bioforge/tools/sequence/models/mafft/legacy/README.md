# MAFFT runtime for `align_msa` (multiple-sequence alignment)

`align_msa` runs MAFFT **out of process** in a digest-pinned container (or a local `mafft`
binary). There is no pure-Python fallback — the tool refuses with setup guidance when MAFFT
is not configured rather than fake an alignment.

## License (verified 2026-06-02 — see `docs/license_audit.md`)

- **Core MAFFT is BSD-3-Clause** — commercial use and redistribution permitted, keep attribution
  (Katoh & Standley, *Mol Biol Evol* 2013).
- ⚠ **The bundled MAFFT *extensions* (Vienna RNA Package, MXSCARNA) are NOT BSD** — they carry a
  restrictive "not redistributed for any fee" clause (`license66.txt`). The image **must be
  core-only**: do **not** install the extensions package. (`align_msa` only needs the core aligner.)

## Image (digest-pinned, rule 19)

The blueprint mandates `@sha256:` digest pins, never `:latest`. Use a core-only MAFFT image and
pin it by digest, then point the runner at it:

```bash
# Example: a biocontainers core MAFFT image. Resolve and pin the DIGEST (not the tag):
docker pull quay.io/biocontainers/mafft:<version>--<build>
docker inspect --format='{{index .RepoDigests 0}}' quay.io/biocontainers/mafft:<version>--<build>
# -> quay.io/biocontainers/mafft@sha256:<digest>

export BIOFORGE_MAFFT_ENABLED=true
export BIOFORGE_MAFFT_DOCKER_IMAGE='quay.io/biocontainers/mafft@sha256:<digest>'
```

Verify the chosen image ships **core MAFFT only** (no Vienna RNA / MXSCARNA extensions) before use.

## Local backend (alternative)

```bash
export BIOFORGE_MAFFT_ENABLED=true
export BIOFORGE_MAFFT_RUNNER=local
export BIOFORGE_MAFFT_BINARY=/usr/bin/mafft   # a core MAFFT install
```

## Protocol

Native MAFFT: input FASTA on stdin, aligned FASTA on stdout. The runner invokes
`mafft --auto --quiet -`. `align_msa` builds the input FASTA, then parses the aligned FASTA and
applies biological soundness checks (equal column count; residues unchanged after de-gapping;
the same set of sequence IDs returned) before accepting the result.

The real-image end-to-end test is marked `-m docker` and skips when the image is absent.
