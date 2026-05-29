# DeepCRISPR legacy runtime — VALIDATED (Python 3.6 / TensorFlow 1.13.2, Apache-2.0)

DeepCRISPR (Chuai et al. 2018, *Genome Biol* 19:80, **Apache-2.0**) is the primary deep
on-target scorer. It runs out of process; BioForge talks to it over a JSON protocol
(`deepcrispr_infer.py`). **Validated end-to-end on 2026-05-29** against the authors' image.

## Validated recipe (use this)

The authors publish a working image — `michaelchuai/deepcrispr:latest` (Python 3.6.5,
**TensorFlow 1.13.2** — the README's "1.3.0" is loose) — which already contains the
DeepCRISPR code AND the trained weights at `/root/DeepCRISPR`. This sidesteps the fragile
from-scratch TF1/Sonnet build entirely. Our `Dockerfile` is a **thin layer** over it that
just bakes in the wrapper:

```bash
cd backend/src/bioforge/tools/sequence/models/deepcrispr/legacy
docker build -t bioforge/deepcrispr:legacy .
docker inspect --format '{{index .RepoDigests 0}}' bioforge/deepcrispr:legacy   # for §10 pin
```
```
BIOFORGE_DEEPCRISPR_ENABLED=true
BIOFORGE_DEEPCRISPR_RUNNER=docker
BIOFORGE_DEEPCRISPR_DOCKER_IMAGE=bioforge/deepcrispr:legacy   # or its @sha256 digest
```
Validated base digest: `michaelchuai/deepcrispr@sha256:812deef95aac01ca1b3b07363613c6393bdb74460399c27108e2acfe5e559ad2`

## What was validated

- The image imports cleanly (`from deepcrispr import DCModelOntar`) and loads the seq-only
  regression weights `trained_models/ontar_cnn_reg_seq` (checkpoint `model.ckpt-seq`).
- **Encoding is DeepCRISPR's own** (`deepcrispr.Sgt` over a one-column `.rsgt`, `with_y=False`)
  — no reimplementation, so the earlier encoder `VERIFY` is **resolved**. A 1-column file
  reproduces the full-file scores exactly.
- The bioforge wrapper, run via `docker run -i bioforge/deepcrispr:legacy`, returns **pure
  JSON** on stdout (TF/`...loaded` chatter is routed to stderr): e.g. guides
  `ACGTTAGCAGTTTGATGGCATGG, ACCTCCAATCGGCCCACGGCTGG, CATTGACAGGATAGTGGCCAGGG`
  -> `{"model":"ontar_cnn_reg_seq","scores":[0.0307, 0.1461, 0.1910]}`.
- Score range on the example set is ~`-0.01 .. 0.19` — a regression head, **not** strictly
  `[0, 1]`; do not assume a unit interval when displaying it.

## Still open (optional, for a calibrated accuracy claim)

- A larger held-out parity check (the image ships `paper_data-regression.tar.gz`) to report a
  real Spearman, and a calibrated-uncertainty display. The plumbing + sane outputs are
  confirmed; the example set (10 near-identical-efficacy guides) is too small/narrow for a
  meaningful correlation.

## Backend B — local (alternative)

Install DeepCRISPR locally (Python 3.6 + TF 1.x + sonnet), then:
```
BIOFORGE_DEEPCRISPR_RUNNER=local
BIOFORGE_DEEPCRISPR_PYTHON=/path/to/py36/python
DEEPCRISPR_REPO_DIR=/path/to/DeepCRISPR            # if not the in-image default
DEEPCRISPR_MODEL_DIR=/path/to/trained_models/ontar_cnn_reg_seq
```
