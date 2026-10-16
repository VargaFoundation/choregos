# SPDX-License-Identifier: Apache-2.0
"""The run's credentials at the egress proxy (ADR 0070).

The per-run proxy (`choregos-egress`) does not know which run is calling until the run says so: the
runner adds its run id and run token to the proxy URL, as proxy credentials, before it starts
anything that makes a network call. The proxy hands the token back to the API to learn the run's
network. The token is already readable by any process of the pod; this adds no exposure.
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import quote, urlsplit, urlunsplit

PROXY_VARIABLES = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
#: `CHOREGOS_EGRESS_AUTH=run` is what the orchestrator sets on the pod when the deployment's proxy
#: wants credentials.
AUTH_VARIABLE = "CHOREGOS_EGRESS_AUTH"


def authenticate_proxy_env(env: Mapping[str, str], run_id: str, token: str) -> dict[str, str]:
    """The proxy variables to set so that the proxy knows this run — and nothing else.

    Empty (nothing to change) unless `CHOREGOS_EGRESS_AUTH=run`, a token exists, and the variable
    holds a proxy URL without user-info (an operator's own credentials are never overwritten).
    `NO_PROXY` is not touched.
    """
    if env.get(AUTH_VARIABLE) != "run" or not token or not run_id:
        return {}
    changes: dict[str, str] = {}
    for name in PROXY_VARIABLES:
        value = env.get(name, "")
        if not value:
            continue
        parts = urlsplit(value)
        if not parts.hostname or parts.username is not None or parts.password is not None:
            continue
        host = parts.hostname if ":" not in parts.hostname else f"[{parts.hostname}]"
        netloc = f"{quote(run_id, safe='')}:{quote(token, safe='')}@{host}"
        if parts.port is not None:
            netloc += f":{parts.port}"
        changes[name] = urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return changes
