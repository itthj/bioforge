# FORECasT legacy runtime (Python 3 + C++ indelmap, MIT)

FORECasT (Allen et al. 2018, *Nat Biotech*, **MIT**) is a per-guide edit-outcome predictor.
It needs a compiled C++ component (`indelmap`), so BioForge runs it **out of process** in the
authors' official image (`quay.io/felicityallen/selftarget`) or a local install, talking to
it over a JSON protocol (`forecast_infer.py`).

This directory targets the FORECasT env, not the modern interpreter, and is **excluded from
the repo's ruff config**.

## Input contract

FORECasT takes a **target sequence + the 0-based PAM index** on the protospacer strand.
`edit_outcome(model="forecast")` passes the PAM index it already located, on the correct
strand; FORECasT enforces its own window, so a wrong index fails loudly rather than scoring
the wrong site.

## Backend A — Docker (default)

The default `BIOFORGE_FORECAST_DOCKER_IMAGE` is the authors' image; the runner **bind-mounts**
`forecast_infer.py` into it, so no build is required:
```
BIOFORGE_FORECAST_ENABLED=true
BIOFORGE_FORECAST_RUNNER=docker
BIOFORGE_FORECAST_DOCKER_IMAGE=quay.io/felicityallen/selftarget   # or a digest-pinned thin image
```
For a self-contained pinned image, build the thin `Dockerfile` here and set the image to its
digest.

## Backend B — local

Install FORECasT (SelfTarget) following its README (compiles `indelmap`), then:
```
BIOFORGE_FORECAST_ENABLED=true
BIOFORGE_FORECAST_RUNNER=local
BIOFORGE_FORECAST_PYTHON=/path/to/forecast/python
```

## Validation checklist (the part that needs a human)

Scaffolding ships behind a graceful "unavailable" path with mocked tests; **nothing below
has run end-to-end yet.** Before enabling:

1. **VERIFY `forecast_infer.py`** against the FORECasT version in the image: the `FORECasT.py`
   entrypoint path (set `FORECAST_SCRIPT` if needed) and which output file is the predicted-
   indel profile + its column layout (the wrapper assumes first col = label, last numeric col
   = count). The wrapper raises loudly if it guessed wrong — fix it, never ship a misparse.
2. **Pin** the image digest for provenance.
3. **Parity check** a few gRNAs against FORECasT's own CLI / website; confirm the label set
   and frequencies.
4. **Then** set `BIOFORGE_FORECAST_ENABLED=true` and run the `online`-marked FORECasT test.
