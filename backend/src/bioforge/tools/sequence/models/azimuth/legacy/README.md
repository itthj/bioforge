# Azimuth / Doench Rule Set 2 — legacy runtime (SCAFFOLD)

The secondary on-target scorer. Doench 2016 **Rule Set 2** ("Azimuth") is a gradient-boosted
scikit-learn regression over ~500 sequence features, scoring a **30-nt window**
(4 nt 5′ context + 20 nt protospacer + 3 nt PAM + 3 nt 3′ context).

**License:** BSD-3-Clause, © 2015 Microsoft Research (verified — see `docs/license_audit.md`).
Commercial use and redistribution are permitted with attribution; the trained pickles ship in
the upstream repo, so there is **no consent gate** (unlike inDelphi) and **no weight fetch**.

## Status: NOT yet built or validated end-to-end

This directory is the **contract** for the legacy environment, not a validated path. It mirrors
the DeepCRISPR / Lindel / FORECasT out-of-process pattern. Before flipping
`BIOFORGE_AZIMUTH_ENABLED=true`, a validation slice must:

1. **Pin scikit-learn** to the exact version that deserializes `saved_models/V3_model_nopos.pickle`
   (the pickle is sklearn-version-specific). Update the `Dockerfile` and rebuild.
2. **Pin the upstream commit** — set `BIOFORGE_AZIMUTH_UPSTREAM_COMMIT` and the `Dockerfile`
   `AZIMUTH_COMMIT` to the same `Biomatters/Azimuth` (py3 port) SHA.
3. **VERIFY the `azimuth.model_comparison.predict(...)` call** in `azimuth_infer.py` against the
   upstream README — confirm the signature and that passing no cut-site context selects
   `V3_model_nopos`.
4. **Build + digest-pin** the image; set `BIOFORGE_AZIMUTH_DOCKER_IMAGE` to its `@sha256:` ref.
5. **Validate numerically** against a few published Rule Set 2 scores, then record the result
   here (mirroring `models/deepcrispr/legacy/README.md`).

Until all five are done, `score_guide_on_target(model="azimuth_rs2")` degrades gracefully: the
deterministic rule-based score is still returned, with a caveat that RS2 is unavailable.

## Protocol

```
stdin :  {"thirtymers": ["<30nt>", ...], "model": "V3_model_nopos"}
stdout:  {"model": "...", "scores": [<float>, ...]}     (or {"error": "..."})
```

Invoked as: `docker run --rm -i <image> python /opt/azimuth/azimuth_infer.py`.
