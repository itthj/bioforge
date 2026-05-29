# Lindel legacy runtime (Python 3 + numpy/scipy, MIT)

Lindel (Chen et al. 2019, *NAR*, **MIT**) is a logistic-regression per-guide edit-outcome
predictor. It is pure numpy/scipy with bundled weights, but BioForge runs it **out of
process** in a pinned env — uniform with the other ML scorers, and so a weight/version bump
is a clean env rebuild. BioForge talks to it over a JSON protocol (`lindel_infer.py`).

This directory targets the Lindel env, not the modern interpreter, and is **excluded from
the repo's ruff config**.

## Input contract

Lindel scores a **60 bp** window: 30 bp upstream + 30 bp downstream of the cut, ACGT only,
with the PAM (`NGG`) near position 33. BioForge's `edit_outcome(model="lindel")` builds that
window from the target + cut and hard-validates it; Lindel itself enforces the PAM/cut
framing, so a misframe fails loudly rather than scoring the wrong sequence.

## Backend A — Docker (default)

```bash
docker build --build-arg LINDEL_COMMIT=<sha> -t bioforge/lindel:legacy .
docker inspect --format '{{index .RepoDigests 0}}' bioforge/lindel:legacy
```
```
BIOFORGE_LINDEL_ENABLED=true
BIOFORGE_LINDEL_RUNNER=docker
BIOFORGE_LINDEL_DOCKER_IMAGE=bioforge/lindel@sha256:<digest>
BIOFORGE_LINDEL_UPSTREAM_COMMIT=<sha>
```

## Backend B — conda (fallback)

```bash
conda env create -f environment.yml
git clone https://github.com/shendurelab/Lindel.git && cd Lindel && python setup.py install
```
```
BIOFORGE_LINDEL_ENABLED=true
BIOFORGE_LINDEL_RUNNER=local
BIOFORGE_LINDEL_PYTHON=/path/to/envs/lindel/bin/python
```

## Validation checklist (the part that needs a human)

Scaffolding ships behind a graceful "unavailable" path with mocked tests; **nothing below
has run end-to-end yet.** Before enabling:

1. **Pin the commit** (`LINDEL_COMMIT`) and the image digest.
2. **VERIFY `lindel_infer.py`** against `Lindel_prediction.py` at that commit: the weight /
   prereq filenames + location in the package dir, and how `rev_index` (array-position ->
   outcome label) is stored in `model_prereq.pkl`. The wrapper raises loudly if it guessed
   wrong — fix the one line, never ship a misdecoded distribution.
3. **Parity check** a few guides against Lindel's own CLI; confirm the label set + frequencies.
4. **Then** set `BIOFORGE_LINDEL_ENABLED=true` and run the `online`-marked Lindel test.
