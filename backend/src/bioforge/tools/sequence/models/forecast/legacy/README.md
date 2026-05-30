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

## Backend A — Docker (default, VALIDATED)

Build the thin image (FROM the authors' official image, baking in the JSON wrapper and
clearing the base uwsgi/nginx entrypoint), then point the runner at it:
```bash
cd backend/src/bioforge/tools/sequence/models/forecast/legacy
docker build -t bioforge/forecast:legacy .
docker inspect --format '{{index .RepoDigests 0}}' bioforge/forecast:legacy   # for §10 pin
```
```
BIOFORGE_FORECAST_ENABLED=true
BIOFORGE_FORECAST_RUNNER=docker
BIOFORGE_FORECAST_DOCKER_IMAGE=bioforge/forecast:legacy   # or its @sha256 digest
```
Validated image digest (2026-05-29):
`bioforge/forecast@sha256:92284be51de9ef4ee1ab27b3895c834c696b6aa945a3acfc2b12c3e076dafd4a`
(base `quay.io/felicityallen/selftarget@sha256:fa8aa329aa9c49b83cdbdb126e5d2796fa179075871c5ea5809ff860631a091a`).
No bind-mount is used — the wrapper is baked in — which also sidesteps host bind-mount flakiness.

## Backend B — local

Install FORECasT (SelfTarget) following its README (compiles `indelmap`), then:
```
BIOFORGE_FORECAST_ENABLED=true
BIOFORGE_FORECAST_RUNNER=local
BIOFORGE_FORECAST_PYTHON=/path/to/forecast/python
FORECAST_SCRIPT=/path/to/indel_prediction/predictor/FORECasT.py   # its dir must hold the theta model
INDELGENTARGET_EXE=/path/to/indelgentarget                        # the compiled indelmap binary
```

## Validated end-to-end (2026-05-29)

Run through the real bioforge path (`predict_forecast` -> docker runner -> thin image ->
typed `ForecastDistribution`) AND the `edit_outcome(model="forecast")` tool. Resolved
`VERIFY:` items:

- **Entrypoint + CWD.** Single mode is `python FORECasT.py <seq> <pam_index> <prefix>`, at
  `/app/indel_prediction/predictor/FORECasT.py`. FORECasT's `DEFAULT_MODEL` theta file is a
  RELATIVE path, so the wrapper runs the script with `cwd` = its own dir (which ships the theta
  alongside it). `INDELGENTARGET_EXE` (the compiled indelmap) is set in the image env
  (`/usr/local/bin/indelgentarget`); `FORECAST_SCRIPT` is baked into the thin image.
- **Output file + columns.** The predicted profile is `<prefix>_predictedindelsummary.txt`,
  lines `<indel_label>\t-\t<count>` (label first, count last) — selected explicitly, counts
  normalized to sum to 1.
- **The `-` null is dropped.** `predictMutationsSingle` injects `p_predict['-'] = 1000` (a
  fixed, input-independent wild-type reference read for plotting — `_predictedreads.txt` shows
  `-` = the full WT sequence), ~51% of the raw mass. It is NOT a model prediction, so the
  wrapper excludes it; the emitted distribution is over real indels only.
- **Parity:** a gRNA reproduces FORECasT's profile through the wrapper (top `I1_L-3C2R0`
  ≈ 0.237 = 228/961, `D3_L-8C7R3` ≈ 0.116, ...); 227 indels sum to 1.0; stdout is pure JSON.

`edit_outcome(model="forecast")` degrades gracefully to `rule_of_thumb` when the env is absent.
