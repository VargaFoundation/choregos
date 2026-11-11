"""Live: an RFC 8693 token exchange against a real Keycloak (S63-07, ADR 0135).

    CHOREGOS_LIVE_KEYCLOAK_TOKEN_URL=https://idp.example/realms/choregos/protocol/openid-connect/token \\
    CHOREGOS_LIVE_KEYCLOAK_CLIENT_ID=choregos CHOREGOS_LIVE_KEYCLOAK_CLIENT_SECRET=... \\
    CHOREGOS_LIVE_KEYCLOAK_SUBJECT_TOKEN=<an access token of a test person> \\
    CHOREGOS_LIVE_KEYCLOAK_AUDIENCE=iris-mcp \\
    uv run pytest tests/live/test_keycloak_exchange_live.py -m live -rs

The realm must allow the client to exchange towards the audience (standard token exchange, enabled on
the client, the audience's client or scope present). Skipped when the variables are unset; failed, not
skipped, when `CHOREGOS_LIVE_REQUIRED` names `keycloak`.
"""

from __future__ import annotations

import pytest

from .conftest import require

pytestmark = pytest.mark.live

VARIABLES = (
    "CHOREGOS_LIVE_KEYCLOAK_TOKEN_URL",
    "CHOREGOS_LIVE_KEYCLOAK_CLIENT_ID",
    "CHOREGOS_LIVE_KEYCLOAK_CLIENT_SECRET",
    "CHOREGOS_LIVE_KEYCLOAK_SUBJECT_TOKEN",
    "CHOREGOS_LIVE_KEYCLOAK_AUDIENCE",
)


async def test_keycloak_exchanges_a_persons_token_for_a_sources_audience() -> None:
    env = require(*VARIABLES)
    from choregos_adapters.token_exchange import TokenExchangeClient, claims_of

    client = TokenExchangeClient(
        env["CHOREGOS_LIVE_KEYCLOAK_TOKEN_URL"],
        client_id=env["CHOREGOS_LIVE_KEYCLOAK_CLIENT_ID"],
        client_secret=env["CHOREGOS_LIVE_KEYCLOAK_CLIENT_SECRET"],
    )
    subject = env["CHOREGOS_LIVE_KEYCLOAK_SUBJECT_TOKEN"]
    audience = env["CHOREGOS_LIVE_KEYCLOAK_AUDIENCE"]
    issued = await client.exchange(subject, audience=audience, actor="assistant:live-test")

    claims, original = claims_of(issued.access_token), claims_of(subject)
    assert claims["sub"] == original["sub"], "the exchanged token is the same person's"
    aud = claims.get("aud")
    assert audience in ([aud] if isinstance(aud, str) else aud or [])
