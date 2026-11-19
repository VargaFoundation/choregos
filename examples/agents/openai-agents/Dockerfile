# The openai-agents example, FROM the runner image (ADR 0063). Build from the repository root:
#   docker build -f examples/agents/openai-agents/Dockerfile \
#     --build-arg RUNNER_IMAGE=ghcr.io/graalsystems/choregos-runner:<tag> -t my-registry/openai-agents-triager .
ARG RUNNER_IMAGE=ghcr.io/vargafoundation/choregos-runner:0.17.15
FROM ghcr.io/astral-sh/uv:0.13.9@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d AS uv

FROM ${RUNNER_IMAGE}
USER root
COPY --from=uv /uv /usr/local/bin/uv
COPY packages/contracts /src/contracts
COPY packages/agent-sdk /src/agent-sdk
COPY examples/agents/openai-agents /src/openai-agents
RUN uv venv /opt/agents/openai-agents --python python3.12 \
 && uv pip install --python /opt/agents/openai-agents/bin/python --no-cache /src/contracts /src/agent-sdk /src/openai-agents \
 && rm -rf /src /usr/local/bin/uv
USER 1000
# The version's runtime: {kind: code, image: openai-agents-triager, command: [/opt/agents/openai-agents/bin/python, -m, openai_agents_triager]}
