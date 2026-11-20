# syntax=docker/dockerfile:1
# The `procurement` job image (ADR 0198, ADR 0056): the image the operator maps for the plugin's job
# steps (`TRANIBOR_JOB_IMAGES`, chart `global.images.jobs.procurement`). A job step runs the runner, which
# runs the step's command: so the image is the SAME release's runner image plus the plugin's
# `tranibor-procurement`, and the release builds it FROM the runner it has just published
# (`.github/workflows/release.yml`, job `procurement-job-image`), so the runner inside always matches the
# platform.
#
#   docker build -f plugins/procurement/Dockerfile \
#     --build-arg RUNNER_IMAGE=<registry>/tranibor-runner@sha256:… .
#
# No default: a job image built FROM another release's runner is the mismatch this file exists to avoid.
#
# Unlike `investment-research`, the command has dependencies (document conversion). They go in a virtual
# environment of their own, `/opt/procurement`, installed from `plugins/procurement/requirements.txt`
# (hashes, exported from `uv.lock` by `make procurement-lock`), so that the runner's environment
# (`/app/.venv`) stays the release's own, byte for byte: the build proves it.
ARG RUNNER_IMAGE

FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS wheel
COPY --from=ghcr.io/astral-sh/uv:0.5.11@sha256:0ac957607303916420297a4c9c213bb33fbd3c888f9cd7f4f7273596ebf42b85 /uv /usr/local/bin/uv
COPY plugins/procurement /src/procurement
RUN uv build --wheel --out-dir /dist /src/procurement

FROM ${RUNNER_IMAGE}
USER root
COPY --from=wheel /dist /tmp/dist
COPY plugins/procurement/requirements.txt /tmp/procurement-requirements.txt
# The dependencies by hash, then the wheel without them. The command is linked by name like the runner's
# own (#713): a job's argv says `tranibor-procurement`. It must START here, next to the runner.
RUN set -eux; \
    uv pip freeze --python /app/.venv/bin/python > /tmp/runner-before.txt; \
    uv venv /opt/procurement --python /usr/local/bin/python3.12; \
    VIRTUAL_ENV=/opt/procurement uv pip install --require-hashes --no-deps --no-cache \
      -r /tmp/procurement-requirements.txt; \
    VIRTUAL_ENV=/opt/procurement uv pip install --no-deps --no-cache /tmp/dist/*.whl; \
    ln -sf /opt/procurement/bin/tranibor-procurement /usr/local/bin/tranibor-procurement; \
    cd /tmp && tranibor-procurement --help >/dev/null; \
    choregos-runner --help >/dev/null; \
    uv pip freeze --python /app/.venv/bin/python > /tmp/runner-after.txt; \
    cmp /tmp/runner-before.txt /tmp/runner-after.txt; \
    rm -rf /tmp/dist /tmp/procurement-requirements.txt /tmp/runner-before.txt /tmp/runner-after.txt
WORKDIR /workspace
USER 1000
