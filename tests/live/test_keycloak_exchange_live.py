"""Live: an RFC 8693 token exchange against a real Keycloak (S63-07, ADR 0135).

    TRANIBOR_LIVE_KEYCLOAK_TOKEN_URL=https://idp.example/realms/choregos/protocol/openid-connect/token \\
    TRANIBOR_LIVE_KEYCLOAK_CLIENT_ID=choregos TRANIBOR_LIVE_KEYCLOAK_CLIENT_SECRET=... \\
    TRANIBOR_LIVE_KEYCLOAK_SUBJECT_TOKEN=<an access token of a test person> \\
    TRANIBOR_LIVE_KEYCLOAK_AUDIENCE=iris-mcp \\
    uv run pytest tests/live/test_keycloak_exchange_live.py -m live -rs

The realm must allow the client to exchange towards the audience (standard token exchange, enabled on
the client, the audience's client or scope present). Skipped when the variables are unset; failed, not
skipped, when `TRANIBOR_LIVE_REQUIRED` names `keycloak`.
"""

from __future__ import annotations

import pytest

from .conftest import require

pytestmark = pytest.mark.live

VARIABLES = (
    "TRANIBOR_LIVE_KEYCLOAK_TOKEN_URL",
    "TRANIBOR_LIVE_KEYCLOAK_CLIENT_ID",
    "TRANIBOR_LIVE_KEYCLOAK_CLIENT_SECRET",
    "TRANIBOR_LIVE_KEYCLOAK_SUBJECT_TOKEN",
    "TRANIBOR_LIVE_KEYCLOAK_AUDIENCE",
)


async def test_keycloak_exchanges_a_persons_token_for_a_sources_audience() -> None:
    env = require(*VARIABLES)
    from tranibor_adapters.token_exchange import TokenExchangeClient, claims_of

    client = TokenExchangeClient(
        env["TRANIBOR_LIVE_KEYCLOAK_TOKEN_URL"],
        client_id=env["TRANIBOR_LIVE_KEYCLOAK_CLIENT_ID"],
        client_secret=env["TRANIBOR_LIVE_KEYCLOAK_CLIENT_SECRET"],
    )
    subject = env["TRANIBOR_LIVE_KEYCLOAK_SUBJECT_TOKEN"]
    audience = env["TRANIBOR_LIVE_KEYCLOAK_AUDIENCE"]
    issued = await client.exchange(subject, audience=audience, actor="assistant:live-test")

    claims, original = claims_of(issued.access_token), claims_of(subject)
    assert claims["sub"] == original["sub"], "the exchanged token is the same person's"
    aud = claims.get("aud")
    assert audience in ([aud] if isinstance(aud, str) else aud or [])
