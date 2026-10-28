# SPDX-License-Identifier: Apache-2.0
"""`python -m choregos_api.relay [serve]` runs the relay; `… mint --org <slug> --days N` prints a relay
token (it needs the run-token key, `CHOREGOS_RUN_TOKEN_PRIVATE_KEY`).

The relay reads its environment and refuses to start without it:

- `CHOREGOS_RELAY_ORG`: the one organisation it serves (a slug);
- `CHOREGOS_RELAY_API_URL`: the API, e.g. `http://choregos-api:8000/api/v1`;
- `CHOREGOS_RELAY_API_TOKEN`: its relay token for that organisation (`mint`);
- `CHOREGOS_RELAY_CLIENT_TOKEN`: the password its clients (SearXNG, Firecrawl) give, 16 characters or more;
- `CHOREGOS_RELAY_LISTEN`: `host:port`, `0.0.0.0:3128` by default.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from choregos_core.redaction import remember_secret

from .server import MAX_HEAD_BYTES, Relay
from .source import ApiProxySource, CachedProxySource

MIN_CLIENT_TOKEN = 16
UNRESOLVED = ("<no value>", "<nil>")


@dataclass(frozen=True, slots=True)
class RelaySettings:
    org: str
    api_url: str
    api_token: str
    client_token: str
    host: str
    port: int


def settings_from(env: Mapping[str, str]) -> RelaySettings:
    """The relay's settings, or `SystemExit` naming every one that is missing or wrong."""
    names = (
        "CHOREGOS_RELAY_ORG",
        "CHOREGOS_RELAY_API_URL",
        "CHOREGOS_RELAY_API_TOKEN",
        "CHOREGOS_RELAY_CLIENT_TOKEN",
    )
    missing = [name for name in names if not env.get(name, "").strip()]
    if missing:
        raise SystemExit(f"the relay needs {', '.join(missing)}")
    unresolved = [
        name for name in (*names, "CHOREGOS_RELAY_LISTEN") if env.get(name, "").strip() in UNRESOLVED
    ]
    if unresolved:
        # A secret template whose key is missing writes this text and syncs fine (see `config.py`): taken as a
        # token, every call to the API would be refused and the relay would fail closed on all traffic.
        raise SystemExit(f"{', '.join(unresolved)} holds an unresolved template value, not a setting")
    client_token = env["CHOREGOS_RELAY_CLIENT_TOKEN"].strip()
    if len(client_token) < MIN_CLIENT_TOKEN:
        raise SystemExit(f"CHOREGOS_RELAY_CLIENT_TOKEN must be {MIN_CLIENT_TOKEN} characters or more")
    host, _, port = env.get("CHOREGOS_RELAY_LISTEN", "0.0.0.0:3128").rpartition(":")
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise SystemExit("CHOREGOS_RELAY_LISTEN is host:port")
    return RelaySettings(
        org=env["CHOREGOS_RELAY_ORG"].strip(),
        api_url=env["CHOREGOS_RELAY_API_URL"].strip(),
        api_token=env["CHOREGOS_RELAY_API_TOKEN"].strip(),
        client_token=client_token,
        host=host or "0.0.0.0",  # noqa: S104 - a pod
        port=int(port),
    )


async def serve(settings: RelaySettings) -> None:
    for secret in (settings.api_token, settings.client_token):
        remember_secret(secret)
    source = CachedProxySource(ApiProxySource(settings.api_url, settings.org, settings.api_token))
    relay = Relay(source, settings.client_token)
    server = await asyncio.start_server(relay.handle, settings.host, settings.port, limit=MAX_HEAD_BYTES)
    async with server:
        await server.serve_forever()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m choregos_api.relay")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("serve", help="run the relay (the default)")
    mint = commands.add_parser("mint", help="print a relay token for one organisation")
    mint.add_argument("--org", required=True)
    mint.add_argument("--days", type=int, required=True)
    args = parser.parse_args(argv)
    if args.command == "mint":
        from ..security import mint_relay_token

        try:
            print(mint_relay_token(args.org, days=args.days))
        except ValueError as bad:
            raise SystemExit(str(bad)) from None
        return 0
    asyncio.run(serve(settings_from(os.environ)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
