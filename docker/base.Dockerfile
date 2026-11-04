# The platform's Python base image: system dependencies, non-root user, uv.
# Published as `ghcr.io/vargafoundation/choregos-python-base`.
FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# Debian's security fixes, applied at build time: the base image (a tag, rebuilt at its own pace)
# can carry a package already fixed in `bookworm-security` — perl-base 5.36.0-7+deb12u3 and its
# three CRITICALs stopped release 0.16.0 at the Trivy scan.
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git tini \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 1000 --create-home --shell /usr/sbin/nologin choregos

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app
ENTRYPOINT ["/usr/bin/tini", "--"]
