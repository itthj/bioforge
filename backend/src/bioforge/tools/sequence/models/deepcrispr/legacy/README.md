# DeepCRISPR legacy runtime (Python 3.6 / TensorFlow 1.x)

DeepCRISPR (Chuai et al. 2018, *Genome Biol* 19:80, **Apache-2.0**) is the primary
deep on-target scorer. It runs TensorFlow 1.3 / Python 3.6, which **cannot** coexist
with BioForge's Python 3.11 stack — so it executes **out of process** in the pinned
legacy environment built here, and BioForge talks to it over a tiny JSON protocol
(`deepcrispr_infer.py`).

This directory is a different-runtime artifact: it is **excluded from the repo's ruff
config** and is never imported by the BioForge package.

## Which model

Only the **sequence-only on-target regression** model (`ontar_cnn_reg_seq`) is wired
up — 4-channel one-hot, 23 bp (20 nt protospacer + 3 nt PAM). The 8-channel
epigenetic models (CTCF/DNase/H3K4me3/RRBS) are out of scope: BioForge has no
per-locus epigenetic tracks for arbitrary targets, and fabricating them would
violate the project's grounding rules.

## Backend A — Docker (default, recommended)

```bash
# from this directory; pin to a real DeepCRISPR commit
docker build --build-arg DEEPCRISPR_COMMIT=<sha> -t bioforge/deepcrispr:legacy .
# capture the digest for reproducible provenance (§10)
docker inspect --format '{{index .RepoDigests 0}}' bioforge/deepcrispr:legacy
```

Then set in BioForge's environment:

```
BIOFORGE_DEEPCRISPR_ENABLED=true
BIOFORGE_DEEPCRISPR_RUNNER=docker
BIOFORGE_DEEPCRISPR_DOCKER_IMAGE=bioforge/deepcrispr@sha256:<digest>
BIOFORGE_DEEPCRISPR_UPSTREAM_COMMIT=<sha>
```

## Backend B — conda (fallback, run inside WSL Ubuntu)

```bash
conda env create -f environment.yml
git clone https://github.com/bm2-lab/DeepCRISPR.git   # add to PYTHONPATH
```

```
BIOFORGE_DEEPCRISPR_ENABLED=true
BIOFORGE_DEEPCRISPR_RUNNER=local
BIOFORGE_DEEPCRISPR_PYTHON=/path/to/envs/deepcrispr/bin/python
```

## Weights

BioForge fetches `trained_models/ontar_cnn_reg_seq.tar.gz` on first use (Apache-2.0,
no consent gate), pins its sha256, and extracts it under
`~/.bioforge/data/deepcrispr/<commit>/`. **If that tarball is tracked with Git LFS**,
the fetcher will detect the pointer and tell you to place the real archive there
(`git lfs pull` a clone, or download the LFS media URL it prints).

## Validation checklist (the part that needs a human)

This scaffolding ships behind a graceful "unavailable" path with mocked-subprocess
tests; **nothing below has been run end-to-end yet.** Before flipping it on:

1. **Reconcile the legacy deps.** `tensorflow==1.3.0` + `dm-sonnet==1.9` is fragile
   (sonnet 1.9 usually wants TF ≥ 1.8). Find a self-consistent set; record it here.
2. **Pin commits/digests.** Set `DEEPCRISPR_COMMIT` and the image digest.
3. **VERIFY the encoding.** Confirm `deepcrispr_infer.py::_encode` (A,C,G,T → 4
   channels, `[N,4,1,23]`) matches DeepCRISPR's training encoding. Prefer the repo's
   own encoder if it exposes one.
4. **Parity check.** Score a few known guides and confirm the outputs are sane and
   track the paper's expected behavior; confirm the score range/normalization before
   surfacing it as a calibrated number.
5. **Then** set `BIOFORGE_DEEPCRISPR_ENABLED=true` and run the `online`-marked
   DeepCRISPR test.
