# The pydantic-ai example, FROM the runner image (ADR 0063). Build from the repository root:
#   docker build -f examples/agents/pydantic-ai/Dockerfile \
#     --build-arg RUNNER_IMAGE=ghcr.io/vargafoundation/choregos-runner:<tag> -t my-registry/pydantic-ai-triager .
ARG RUNNER_IMAGE=ghcr.io/vargafoundation/choregos-runner:0.17.15
FROM ghcr.io/astral-sh/uv:0.5.11 AS uv

FROM ${RUNNER_IMAGE}
USER root
COPY --from=uv /uv /usr/local/bin/uv
COPY packages/contracts /src/contracts
COPY packages/agent-sdk /src/agent-sdk
COPY examples/agents/pydantic-ai /src/pydantic-ai
RUN uv venv /opt/agents/pydantic-ai --python python3.12 \
 && uv pip install --python /opt/agents/pydantic-ai/bin/python --no-cache /src/contracts /src/agent-sdk /src/pydantic-ai \
 && rm -rf /src /usr/local/bin/uv
USER 1000
# The version's runtime: {kind: code, image: pydantic-ai-triager, command: [/opt/agents/pydantic-ai/bin/python, -m, pydantic_ai_triager]}
