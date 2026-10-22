# The reference hosted agent, standard library only (ADR 0080). Build:
#   docker build -t my-registry/reference-triager examples/hosted/reference
FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
RUN useradd --uid 1000 --create-home agent
WORKDIR /app
COPY *.py ./
USER 1000
ENV PORT=8088 PYTHONUNBUFFERED=1
EXPOSE 8088
CMD ["python", "responses_server.py"]
