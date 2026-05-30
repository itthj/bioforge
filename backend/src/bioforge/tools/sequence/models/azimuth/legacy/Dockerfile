# SCAFFOLD -- not yet built/validated. Pinned legacy environment for Azimuth / Doench Rule
# Set 2 (Doench 2016). Azimuth's trained scikit-learn pickles (saved_models/V3_model_*.pickle)
# are version-fragile, so this image pins the scikit-learn version that deserializes them and
# installs the Python-3 port; the wrapper then loads the committed pickles AS-IS (no retrain).
#
# Azimuth + the committed pickles are BSD-3-Clause (c) 2015 Microsoft Research
# (docs/license_audit.md); redistribution must retain that notice. Cite Doench et al. 2016.
#
# VERIFY before building:
#   * the exact scikit-learn version that loads V3_model_nopos.pickle (the pickle is sklearn-
#     version-specific -- match it or the load fails);
#   * the Biomatters/Azimuth commit to pin (BIOFORGE_AZIMUTH_UPSTREAM_COMMIT must match);
#   * the azimuth.model_comparison.predict(...) call in azimuth_infer.py.
# Then build, tag by digest, and set BIOFORGE_AZIMUTH_DOCKER_IMAGE to the @sha256 reference.

FROM python:3.8-slim

# Pin to a real Biomatters/Azimuth (py3 port) commit before building.
ARG AZIMUTH_COMMIT=master

RUN pip install --no-cache-dir numpy scipy pandas \
    # VERIFY: pin scikit-learn to the exact version that deserializes the committed pickles.
    "scikit-learn" \
    && pip install --no-cache-dir "git+https://github.com/Biomatters/Azimuth.git@${AZIMUTH_COMMIT}"

COPY azimuth_infer.py /opt/azimuth/azimuth_infer.py

# runner.py invokes: docker run --rm -i <image> python /opt/azimuth/azimuth_infer.py
