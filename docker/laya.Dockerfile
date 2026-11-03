# Laya (ADR 0121): the self-hosted decision model LiteLLM serves as `platform/decide`.
# `laya-serve` speaks TypeSafe Jev's wire (`POST /v1/systemone`) on port 8000, on CPU.
#
# The multilingual checkpoint (`convaiinnovations/laya-multilingual`, mmBERT-base, 322M parameters,
# Apache-2.0) is fetched HERE, at the commit Laya itself lists as reviewed, then loaded again with the
# network forbidden: the running server never downloads anything (`HF_HUB_OFFLINE=1`).
FROM python:3.12-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv
# `LAYA_REVISION`: the commit `laya.revisions.PINNED_REVISIONS` names for
# `convaiinnovations/laya-multilingual`; `LAYA_EXTRA_MODELS` makes it the only repository loaded. The
# same two values at run time make the offline load resolve to exactly this snapshot.
ENV UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 \
    HF_HOME=/opt/laya/models \
    LAYA_REVISION=e4e9ddf21a7b1903b7acffd8814ad4307bf63a67 \
    LAYA_DEVICE=cpu LAYA_MODELS=multilingual LAYA_DEFAULT_MODEL=multilingual \
    LAYA_EXTRA_MODELS='{"multilingual": "convaiinnovations/laya-multilingual", "english": "convaiinnovations/laya-multilingual", "typed-decisions": "convaiinnovations/laya-multilingual"}'
COPY docker/laya/requirements.txt /tmp/laya-requirements.txt
# `--require-hashes` checks every wheel against `docker/laya/requirements.txt` (`make laya-lock`).
# torch is the `+cpu` build, which lives on PyTorch's own index; the hashes pin it whatever the index.
RUN uv venv /opt/laya/venv --python /usr/local/bin/python3.12 \
 && VIRTUAL_ENV=/opt/laya/venv uv pip install --require-hashes --no-deps \
      --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match \
      -r /tmp/laya-requirements.txt
# `build_router` is what `laya-serve` runs at start-up: the same code path downloads, then proves
# that everything it needs is on disk once the Hub is out of reach.
RUN set -eux; \
    warm='from laya.serve import build_router; r = build_router(); assert r.loaded == ["multilingual"], r.loaded'; \
    /opt/laya/venv/bin/python -c "$warm"; \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /opt/laya/venv/bin/python -c "$warm"
# The Hub cache writes some of its files `0600` as root: readable by the server's uid, or it logs a
# "corrupted tree cache" at every start (seen on the first build).
RUN chmod -R a+rX /opt/laya/models

FROM python:3.12-slim-bookworm AS runtime
# Debian's security fixes first: the base image may carry a package already fixed in
# `bookworm-security` (see `tools.Dockerfile`).
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
 && apt-get install -y --no-install-recommends ca-certificates tini \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 1000 --create-home --shell /usr/sbin/nologin laya
COPY --from=builder /opt/laya /opt/laya
# CPU only, offline, one checkpoint resident and loaded before the port opens.
# `LAYA_EXTRA_MODELS` points the three bundled names at the standalone multilingual repository, the one
# fetched above (Laya's default loads `multilingual` from a subfolder of `convaiinnovations/laya`): a
# request without `model=` that Laya would route to `english` (English text) or `typed-decisions`
# finds the weights on disk instead of failing offline. LiteLLM always names `multilingual` anyway.
# `LAYA_API_KEY` is not set here: the chart and compose give it from a secret.
ENV PYTHONUNBUFFERED=1 PATH="/opt/laya/venv/bin:$PATH" \
    HF_HOME=/opt/laya/models HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 \
    LAYA_REVISION=e4e9ddf21a7b1903b7acffd8814ad4307bf63a67 \
    LAYA_HOST=0.0.0.0 LAYA_PORT=8000 \
    LAYA_DEVICE=cpu LAYA_MODELS=multilingual LAYA_DEFAULT_MODEL=multilingual \
    LAYA_EXTRA_MODELS='{"multilingual": "convaiinnovations/laya-multilingual", "english": "convaiinnovations/laya-multilingual", "typed-decisions": "convaiinnovations/laya-multilingual"}' \
    LAYA_PRELOAD=1 LAYA_MAX_LOADED=1
USER 1000
EXPOSE 8000
# `/health` answers `{"status": "ok"}` without a credential even when `LAYA_API_KEY` is set.
HEALTHCHECK --interval=15s --timeout=3s --start-period=60s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health')"
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["laya-serve"]
