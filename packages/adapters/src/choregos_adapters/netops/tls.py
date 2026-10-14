# SPDX-License-Identifier: Apache-2.0
"""TLS verification for the netops connectors (ADR 0052): verified by default, against a CA the
connector names when the device carries a private one, and never turned off silently.

A firewall's web interface usually carries a self-signed certificate, and the first reflex is
`verify=False`. That is how a stolen API key ends up talking to whoever answers on the address. So:

- `ca_cert` (a PEM, configuration, not a secret) is the way to trust a private CA;
- `verify_tls: false` is accepted in `dev` only, logged as a warning every time the connector is
  built, and refused in `staging` and `prod` — a connector there either verifies or does not start.
"""

from __future__ import annotations

import os
import ssl
from typing import Any

import structlog

from ..errors import ConfigurationError

log = structlog.get_logger(__name__)

#: The environments where an unverified connection is refused outright.
STRICT_ENVIRONMENTS = frozenset({"staging", "prod"})


def tls_verification(connector: str, config: dict[str, Any]) -> bool | ssl.SSLContext:
    """What `httpx` receives as `verify`: an SSL context trusting `ca_cert`, `True`, or — in dev
    only, said out loud — `False`."""
    environment = os.environ.get("CHOREGOS_ENV", "dev")
    if config.get("verify_tls", True) is False:
        if environment in STRICT_ENVIRONMENTS:
            raise ConfigurationError(
                f"{connector}: `verify_tls: false` is refused in {environment}. Give the device's CA "
                "as `ca_cert` (PEM) instead."
            )
        log.warning(
            "netops.tls_verification_disabled",
            connector=connector,
            environment=environment,
            reason="verify_tls: false in the connector configuration",
        )
        return False
    pem = str(config.get("ca_cert") or "").strip()
    if pem:
        try:
            return ssl.create_default_context(cadata=pem)
        except ssl.SSLError as error:
            raise ConfigurationError(
                f"{connector}: `ca_cert` is not a valid PEM certificate: {error}"
            ) from error
    return True
