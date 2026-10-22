# The ticket triager on Microsoft Agent Framework, as a hosted agent (ADR 0080). Build:
#   docker build -t my-registry/agent-framework-triager examples/hosted/agent-framework
FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
RUN useradd --uid 1000 --create-home agent
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./
USER 1000
ENV PORT=8088 PYTHONUNBUFFERED=1
EXPOSE 8088
CMD ["python", "responses_server.py"]
