# FORECasT (Allen 2018) legacy inference image -- a thin layer over the authors' official
# image (quay.io/felicityallen/selftarget, Python 3.6), which already ships FORECasT, the
# compiled indelmap binary (INDELGENTARGET_EXE=/usr/local/bin/indelgentarget), and the theta
# model. We just bake in the JSON-protocol wrapper and clear the base image's web-server
# entrypoint so the runner's explicit `python ...` runs directly (mirrors the DeepCRISPR image).
#
#   docker build -t bioforge/forecast:legacy .
#   docker inspect --format '{{index .RepoDigests 0}}' bioforge/forecast:legacy   # for §10 pin
# then set BIOFORGE_FORECAST_DOCKER_IMAGE to that digest.
#
# Validated base digest (2026-05-29):
#   quay.io/felicityallen/selftarget@sha256:fa8aa329aa9c49b83cdbdb126e5d2796fa179075871c5ea5809ff860631a091a
FROM quay.io/felicityallen/selftarget

# FORECasT.py entrypoint inside the image. Its dir also holds FORECasT's RELATIVE theta model
# file, so the wrapper runs the script from there.
ENV FORECAST_SCRIPT=/app/indel_prediction/predictor/FORECasT.py

# BioForge's legacy inference entrypoint (the JSON-protocol wrapper).
COPY forecast_infer.py /opt/forecast/forecast_infer.py

# Clear the base image's uwsgi/nginx entrypoint so the runner's explicit `python ...` runs directly.
ENTRYPOINT []
CMD ["python", "/opt/forecast/forecast_infer.py"]
