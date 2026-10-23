# The ticket triager on the GitHub Copilot SDK, as a hosted agent (ADR 0080). Build:
#   docker build -t my-registry/copilot-sdk-triager examples/hosted/copilot-sdk
FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
RUN useradd --uid 1000 --create-home agent
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# The Copilot runtime is fetched at build time, never when a run starts: a step's network does not
# reach GitHub's releases.
ENV COPILOT_CLI_EXTRACT_DIR=/opt/copilot-cli COPILOT_SKIP_CLI_DOWNLOAD=1
# The download extracts it as root, mode 0700: the agent (uid 1000) must be able to run it (S41-23).
RUN COPILOT_SKIP_CLI_DOWNLOAD=0 python -m copilot download-runtime && chmod -R a+rX /opt/copilot-cli
COPY *.py ./
USER 1000
ENV PORT=8088 PYTHONUNBUFFERED=1
EXPOSE 8088
CMD ["python", "responses_server.py"]
