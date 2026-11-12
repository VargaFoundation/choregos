# The `choregos-outpost` daemon: what an outpost's operator runs in their own network (ADR 0147).
#
# It is not one of the platform's own workloads. It dials one hostname, the door's, and the MCP
# servers its policy file names; it listens on nothing, so the image has neither `EXPOSE` nor
# `HEALTHCHECK`. Nothing of the operator's is baked in: the policy file, the keys and the
# credentials are mounted, and the image carries no secret and no address.
FROM python:3.12-slim-bookworm AS builder
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY packages packages
COPY apps/api/pyproject.toml apps/api/
COPY apps/orchestrator/pyproject.toml apps/orchestrator/
# `--no-editable`: the daemon and the three workspace packages it uses (the contract, the core's
# egress matcher, the adapters' MCP client and sealing) are installed INTO the virtual environment.
# The runtime stage then copies that environment and nothing else: whoever runs this image in their
# network gets the daemon, not the rest of the repository beside it.
RUN mkdir -p apps/api/src apps/orchestrator/src \
 && uv sync --frozen --no-dev --no-editable --package choregos-outpost

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH" \
    CHOREGOS_OUTPOST_POLICY=/etc/choregos-outpost/policy.yaml \
    CHOREGOS_OUTPOST_STATE_DIR=/var/lib/choregos-outpost
# Debian's security fixes, applied at build time: the base image (a tag, rebuilt at its own pace)
# can carry a package already fixed in `bookworm-security` — perl-base 5.36.0-7+deb12u3 and its
# three CRITICALs stopped release 0.16.0 at the Trivy scan.
# The state directory (the keys, the journal of call ids) and the audit log's directory belong to
# the daemon's user and to nobody else; a deployment mounts a volume that outlives the container
# over the first.
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
 && apt-get install -y --no-install-recommends ca-certificates tini \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 1000 --create-home --shell /usr/sbin/nologin choregos \
 && install -d -o 1000 -g 1000 -m 0700 /var/lib/choregos-outpost \
 && install -d -o 1000 -g 1000 -m 0750 /var/log/choregos-outpost
COPY --from=builder --chown=1000:1000 /app/.venv /app/.venv
USER 1000
# The daemon must START in this image, and find the contract it was built against: the wheel of
# `choregos-contracts` carries the door's document and the policy file's schema, and a daemon that
# cannot read them refuses every policy. Checked here, where a missing file fails the build.
RUN choregos-outpost --version \
 && python -c "import choregos_contracts as c, choregos_outpost.policy as p; \
      c.load_outpost_door(); p.load(c.schemas_dir() / 'examples' / 'outpost-policy.example.yaml')" \
 && test ! -e /app/packages
ENTRYPOINT ["/usr/bin/tini", "--", "choregos-outpost"]
CMD ["run"]
