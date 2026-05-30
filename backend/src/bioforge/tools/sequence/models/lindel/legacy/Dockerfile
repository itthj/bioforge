# Lindel (Chen 2019) legacy inference image -- Python 3 + numpy/scipy, MIT.
# Built once, then invoked out-of-process by BioForge's runner
# (`docker run --rm -i <image> python /opt/lindel/lindel_infer.py`).
#
# After building, capture the digest and set BIOFORGE_LINDEL_DOCKER_IMAGE to it
# (`docker inspect --format '{{index .RepoDigests 0}}' <image>`) for §10 provenance.
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Pin to a real Lindel commit at build time:  --build-arg LINDEL_COMMIT=<sha>
ARG LINDEL_COMMIT=master

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install numpy scipy

# Upstream Lindel (MIT), pinned to a commit. An EDITABLE install (`pip install -e .`) is
# required, NOT `setup.py install`: Lindel's setup.py declares package_data 'data/*.pkl', but
# the weight pkls actually sit directly in the Lindel/ package dir (Model_weights.pkl,
# model_prereq.pkl) -- the glob matches nothing, so a plain install drops them. Editable keeps
# Lindel.__path__[0] pointed at the source dir that holds the pkls, exactly what
# Lindel_prediction.py's loader (`os.path.join(Lindel.__path__[0], "Model_weights.pkl")`) expects.
RUN git clone https://github.com/shendurelab/Lindel.git /opt/Lindel \
    && git -C /opt/Lindel checkout "${LINDEL_COMMIT}" \
    && cd /opt/Lindel \
    && pip install -e .

# BioForge's legacy inference entrypoint (the JSON-protocol wrapper).
COPY lindel_infer.py /opt/lindel/lindel_infer.py

CMD ["python", "/opt/lindel/lindel_infer.py"]
