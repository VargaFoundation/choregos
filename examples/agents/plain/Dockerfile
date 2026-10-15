# An agent image is built FROM the runner image (ADR 0063): the runner is the entrypoint, the agent
# is a subprocess it starts from the version's `command`. The agent gets its own virtualenv, so its
# dependencies never shadow the runner's.
#
# Build from the repository root (until the SDK is on PyPI; then `uv pip install choregos-agent-sdk`):
#   docker build -f examples/agents/plain/Dockerfile \
#     --build-arg RUNNER_IMAGE=ghcr.io/vargafoundation/choregos-runner:<tag> -t my-registry/ticket-triager .
# Then pin the pushed image by digest in the operator's `CHOREGOS_AGENT_IMAGES`.
ARG RUNNER_IMAGE=ghcr.io/vargafoundation/choregos-runner:0.17.15
FROM ghcr.io/astral-sh/uv:0.5.11 AS uv

FROM ${RUNNER_IMAGE}
USER root
COPY --from=uv /uv /usr/local/bin/uv
COPY packages/contracts /src/contracts
COPY packages/agent-sdk /src/agent-sdk
COPY examples/agents/plain /src/plain
RUN uv venv /opt/agents/plain --python python3.12 \
 && uv pip install --python /opt/agents/plain/bin/python --no-cache /src/contracts /src/agent-sdk /src/plain \
 && rm -rf /src /usr/local/bin/uv
USER 1000
# The version's runtime: {kind: code, image: ticket-triager, command: [/opt/agents/plain/bin/python, -m, choregos_agent_examples.plain]}
