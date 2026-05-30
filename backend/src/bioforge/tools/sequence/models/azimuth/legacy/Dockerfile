# Pinned legacy environment for Azimuth / Doench Rule Set 2 (Doench 2016). Azimuth's trained
# scikit-learn pickles (saved_models/V3_model_*.pickle) are version-fragile, so this image pins
# the scikit-learn version that deserializes them and installs the Python-3 port; the wrapper
# then loads the committed pickles AS-IS (no retrain).
#
# Azimuth + the committed pickles are BSD-3-Clause (c) 2015 Microsoft Research
# (docs/license_audit.md); redistribution must retain that notice. Cite Doench et al. 2016.
#
# Pins are the Biomatters/Azimuth (py3 port) requirements.txt FREEZE -- the tested set that
# deserializes the committed pickles. NOTE: the port's setup.py inconsistently pins
# scikit-learn==0.24.1; requirements.txt pins 0.23.2 -- we use the requirements.txt freeze and
# install the port with --no-deps so it cannot pull 0.24.1 over it.
#
# Validated 2026-05-30: V3_model_nopos.pickle loads under scikit-learn 0.23.2 in this image and
# azimuth.model_comparison.predict(...) returns a deterministic score (EMX1 30-mer -> 0.4889).

FROM python:3.8-slim

# Biomatters/Azimuth (py3 port) master @ 2022-11-21 -- the commit the image was validated from.
# Keep in sync with BIOFORGE_AZIMUTH_UPSTREAM_COMMIT.
ARG AZIMUTH_COMMIT=dbd30b9d74f90f1846c0a31bcafcec8b36215af7

# Headless matplotlib backend -- Azimuth imports matplotlib at module load.
ENV MPLBACKEND=Agg

RUN pip install --no-cache-dir \
      numpy==1.19.1 scipy==1.5.2 pandas==1.1.0 scikit-learn==0.23.2 \
      biopython==1.77 matplotlib==3.3.0 \
    && pip install --no-cache-dir --no-deps "https://github.com/Biomatters/Azimuth/archive/${AZIMUTH_COMMIT}.tar.gz"

COPY azimuth_infer.py /opt/azimuth/azimuth_infer.py

# runner.py invokes: docker run --rm -i <image> python /opt/azimuth/azimuth_infer.py
