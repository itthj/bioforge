# Azimuth / Doench Rule Set 2 — legacy runtime (SCAFFOLD)

The secondary on-target scorer. Doench 2016 **Rule Set 2** ("Azimuth") is a gradient-boosted
scikit-learn regression over ~500 sequence features, scoring a **30-nt window**
(4 nt 5′ context + 20 nt protospacer + 3 nt PAM + 3 nt 3′ context).

**License:** BSD-3-Clause, © 2015 Microsoft Research (verified — see `docs/license_audit.md`).
Commercial use and redistribution are permitted with attribution; the trained pickles ship in
the upstream repo, so there is **no consent gate** (unlike inDelphi) and **no weight fetch**.

## Status: VALIDATED 2026-05-30

Built + validated end-to-end against `bioforge/azimuth:legacy`:

- **scikit-learn 0.23.2** (with numpy 1.19.1, scipy 1.5.2, pandas 1.1.0) — the
  `Biomatters/Azimuth` `requirements.txt` freeze — deserializes `saved_models/V3_model_nopos.pickle`
  cleanly. (The port's `setup.py` inconsistently pins 0.24.1; we use the `requirements.txt` 0.23.2.)
- **Upstream commit** `dbd30b9d74f90f1846c0a31bcafcec8b36215af7` (Biomatters/Azimuth py3 port,
  2022-11-21) — the default for `BIOFORGE_AZIMUTH_UPSTREAM_COMMIT` and the `Dockerfile` `AZIMUTH_COMMIT`.
- **`predict(seqs, aa_cut=None, percent_peptide=None)`** selects `V3_model_nopos` and returns a
  deterministic score: the 30-mer `GGGG+GAGTCCGAGCAGAAGAAGAA+AGG+TGG` (EMX1) → **0.4889**, batch
  order preserved. It uses Azimuth's own featurizer + the committed pickle, so the output is the
  published RS2 model by construction.
- Covered by `test_azimuth_real_image_end_to_end` (`-m docker`, deselected by default).

### Build + enable

```
docker build -t bioforge/azimuth:legacy backend/src/bioforge/tools/sequence/models/azimuth/legacy
# then set:  BIOFORGE_AZIMUTH_ENABLED=true   BIOFORGE_AZIMUTH_DOCKER_IMAGE=bioforge/azimuth:legacy
```

Still **off by default** — `score_guide_on_target(model="azimuth_rs2")` degrades gracefully (the
deterministic rule-based score is returned, with a caveat) until enabled. The base `python:3.8-slim`
is a versioned tag (rule 19 allows it; `:latest` is forbidden); digest-pinning it is a follow-up.

## Protocol

```
stdin :  {"thirtymers": ["<30nt>", ...], "model": "V3_model_nopos"}
stdout:  {"model": "...", "scores": [<float>, ...]}     (or {"error": "..."})
```

Invoked as: `docker run --rm -i <image> python /opt/azimuth/azimuth_infer.py`.
