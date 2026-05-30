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

## Backend A — Docker (default, VALIDATED)

```bash
cd backend/src/bioforge/tools/sequence/models/lindel/legacy
docker build --build-arg LINDEL_COMMIT=fdcad580ba76bcfb7a98f58c3769b76f31693d63 -t bioforge/lindel:legacy .
docker inspect --format '{{index .RepoDigests 0}}' bioforge/lindel:legacy
```
```
BIOFORGE_LINDEL_ENABLED=true
BIOFORGE_LINDEL_RUNNER=docker
BIOFORGE_LINDEL_DOCKER_IMAGE=bioforge/lindel:legacy   # or its @sha256 digest
BIOFORGE_LINDEL_UPSTREAM_COMMIT=fdcad580ba76bcfb7a98f58c3769b76f31693d63
```
Validated image digest (2026-05-29):
`bioforge/lindel@sha256:66bbe6f7d61bf0d30bc6fb8ecdca7cb5094707fe599f3dec57175d719baa6bba`
(`lindel_upstream_commit` now defaults to the pinned commit, so the §10 provenance pin is correct out of the box.)

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

## Validated end-to-end (2026-05-29)

Run through the real bioforge path (`predict_lindel` -> docker runner -> wrapper -> typed
`LindelDistribution`) AND the `edit_outcome(model="lindel")` tool, against
`bioforge/lindel:legacy` (Lindel @ fdcad58). Resolved `VERIFY:` items:

- **Editable install is required.** Lindel's `setup.py` declares `package_data 'data/*.pkl'`,
  but the weights sit directly in `Lindel/` (`Model_weights.pkl`, `model_prereq.pkl`), so the
  glob matches nothing and `setup.py install` drops them. The Dockerfile uses `pip install -e .`
  so `Lindel.__path__[0]` points at the source dir that holds the pkls — exactly what
  `Lindel_prediction.py`'s loader expects.
- **`rev_index = prereq[1]`** — `prereq` is the 4-tuple `(label, rev_index, features,
  frame_shift)`; the wrapper now mirrors upstream (the earlier dict-heuristic guess is gone).
  The label map mirrors upstream's `{rev_index[i]: y_hat[i] ... if y_hat[i] != 0}`.
- **Parity:** Lindel's own example sequences reproduce byte-for-byte through the wrapper
  (seq_1 frameshift 0.8912, top `-2+4`=0.3090; seq_2 frameshift 0.8391); distributions sum to
  1.0; stdout is pure JSON (library chatter routed to stderr).

`edit_outcome(model="lindel")` degrades gracefully to `rule_of_thumb` when the env is absent.
