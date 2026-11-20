# syntax=docker/dockerfile:1
# The `investment-research` job image (ADR 0181 O4-3, ADR 0056): the image the operator maps for the
# plugin's job steps (`TRANIBOR_JOB_IMAGES`, chart `global.images.jobs.investment-research`). A job step
# runs the runner, which runs the step's command: so the image is the SAME release's runner image plus
# the plugin's `choregos-quant`, and the release builds it FROM the runner it has just published
# (`.github/workflows/release.yml`, job `job-image`), so the runner inside always matches the platform.
#
#   docker build -f plugins/investment-research/Dockerfile \
#     --build-arg RUNNER_IMAGE=<registry>/tranibor-runner@sha256:… .
#
# No default: a job image built FROM another release's runner is the mismatch this file exists to avoid.
ARG RUNNER_IMAGE

FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS wheel
COPY --from=ghcr.io/astral-sh/uv:0.5.11@sha256:0ac957607303916420297a4c9c213bb33fbd3c888f9cd7f4f7273596ebf42b85 /uv /usr/local/bin/uv
COPY plugins/investment-research /src/investment-research
RUN uv build --wheel --out-dir /dist /src/investment-research

FROM ${RUNNER_IMAGE}
USER root
COPY --from=wheel /dist /tmp/dist
# Without its dependencies: the command needs only the standard library (`quant/__init__.py`), and the
# runner's environment is the release's own, untouched. The command must START here, and is linked by
# name like the runner's own (#713): a job's argv says `choregos-quant`.
RUN set -eux; \
    uv pip install --python /app/.venv/bin/python --no-deps --no-cache /tmp/dist/*.whl; \
    rm -rf /tmp/dist; \
    ln -sf /app/.venv/bin/choregos-quant /usr/local/bin/choregos-quant; \
    cd /tmp && choregos-quant --help >/dev/null; \
    choregos-runner --help >/dev/null
WORKDIR /workspace
USER 1000
