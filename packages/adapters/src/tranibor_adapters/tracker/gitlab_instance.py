# SPDX-License-Identifier: Apache-2.0
"""The GitLab instance a connector talks to: gitlab.com or a self-managed one.

The tracker, the SCM and the CI take a `base_url`. A self-managed instance may sit under a path
prefix (`https://tools.example.org/gitlab`, a relative URL root), be signed by a private CA
(`ca_bundle`) and run an older release than gitlab.com: `MIN_GITLAB_VERSION` is the oldest one the
three adapters are written for (15.0: project access tokens on every tier, issue links with
`link_type`, pipeline variables as a list).
"""

from __future__ import annotations

import os
import ssl
from typing import Any
from urllib.parse import urlsplit

from ..errors import ConfigurationError, UpstreamError
from ..http import RestClient

GITLAB_COM = "https://gitlab.com"
MIN_GITLAB_VERSION = (15, 0)


def normalize_base_url(raw: str | None) -> str:
    """`https://host[:port][/prefix]` without a trailing slash; anything else is refused loudly,
    since a typo would otherwise surface as a 404 on every call."""
    url = (raw or GITLAB_COM).strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ConfigurationError(f"`base_url` must be an http(s) URL, got {raw!r}")
    if parts.query or parts.fragment:
        raise ConfigurationError("`base_url` carries no query string or fragment")
    return url


def build_client(cfg: dict[str, Any]) -> RestClient:
    """The REST client of a GitLab connector: base URL (with its prefix), token, optional CA bundle."""
    ca_bundle = str(cfg.get("ca_bundle") or "").strip() or None
    if ca_bundle is not None and not os.path.isfile(ca_bundle):
        raise ConfigurationError(f"`ca_bundle` is not a readable file: {ca_bundle}")
    try:
        return RestClient(
            normalize_base_url(cfg.get("base_url")),
            headers={"PRIVATE-TOKEN": cfg["token"]},
            service="gitlab",
            ca_bundle=ca_bundle,
        )
    except ssl.SSLError as error:  # a file that is not a PEM bundle
        raise ConfigurationError(f"`ca_bundle` {ca_bundle} holds no usable certificate: {error}") from error


def parse_version(raw: Any) -> tuple[int, ...] | None:
    """`16.3.2-ee` to `(16, 3, 2)`; None when it is not a version."""
    parts = str(raw or "").split("-")[0].split(".")
    return tuple(int(p) for p in parts) if parts and all(p.isdigit() for p in parts) else None


async def check_instance(client: RestClient) -> dict[str, Any]:
    """The connector's **Test** answer: is the instance reachable, trusted (TLS) and recent enough?"""
    try:
        info = await client.request("GET", "/api/v4/version")
    except UpstreamError as error:
        if error.status_code in {401, 403}:
            return {"ok": False, "reason": f"{error.status_code}: the token is refused (scope `api`)"}
        return {"ok": False, "reason": f"GitLab did not answer /api/v4/version: {error}"}
    except Exception as error:  # a TLS or connection failure is a verdict, not a crash
        return {"ok": False, "reason": f"cannot reach {client.base_url}: {error}"}
    version = parse_version(info.get("version") if isinstance(info, dict) else None)
    wanted = ".".join(str(n) for n in MIN_GITLAB_VERSION)
    if version is None or version < MIN_GITLAB_VERSION:
        found = info.get("version", "unknown") if isinstance(info, dict) else "unknown"
        return {"ok": False, "reason": f"GitLab {found} is not supported: {wanted} or later"}
    return {"ok": True, "version": str(info["version"])}
