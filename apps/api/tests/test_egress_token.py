"""The egress token (ADR 0115): what the proxy variables carry, since the agent inherits them.

It reads its run's network and reports its decisions; every other internal route refuses it, so an
agent that reads `HTTPS_PROXY` cannot post its run's result, take its git token or call its tools.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from .test_network_policy_api import ALLOWED, _batch, _run


async def _egress_token(client: AsyncClient, run_id: str, headers: dict[str, str]) -> dict[str, str]:
    response = await client.post(f"/api/v1/internal/runs/{run_id}/egress-token", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token"] and body["expires_at"]
    return {"Authorization": f"Bearer {body['token']}"}


async def test_a_run_exchanges_its_token_for_one_that_reads_its_network(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    run_token = await _run(project, "run-egress-1")
    egress = await _egress_token(client, "run-egress-1", run_token)

    network = await client.get("/api/v1/internal/runs/run-egress-1/network", headers=egress)
    assert network.status_code == 200, network.text
    decisions = await client.post(
        "/api/v1/internal/runs/run-egress-1/network/decisions",
        headers=egress,
        json=_batch({**ALLOWED, "count": 1}),
    )
    assert decisions.status_code == 202, decisions.text
    # The run token still opens them: the runner itself reports too.
    assert (
        await client.get("/api/v1/internal/runs/run-egress-1/network", headers=run_token)
    ).status_code == 200


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("GET", "/input", None),
        ("POST", "/result", {"status": "succeeded"}),
        ("POST", "/git-token", None),
        ("POST", "/events", {"events": []}),
        ("GET", "/tools", None),
        ("POST", "/egress-token", None),  # an egress token does not mint another
    ],
)
async def test_the_egress_token_opens_no_other_internal_route(
    client: AsyncClient, project: dict[str, Any], method: str, suffix: str, body: Any
) -> None:
    run_token = await _run(project, "run-egress-2")
    egress = await _egress_token(client, "run-egress-2", run_token)
    response = await client.request(
        method, f"/api/v1/internal/runs/run-egress-2{suffix}", headers=egress, json=body
    )
    assert response.status_code == 403, f"{method} {suffix}: {response.status_code} {response.text}"


async def test_an_egress_token_is_for_its_own_run_only(client: AsyncClient, project: dict[str, Any]) -> None:
    mine = await _egress_token(client, "run-egress-3", await _run(project, "run-egress-3"))
    await _run(project, "run-egress-4")
    other = await client.get("/api/v1/internal/runs/run-egress-4/network", headers=mine)
    assert other.status_code == 403
    assert (await client.post("/api/v1/internal/runs/run-egress-4/egress-token")).status_code == 401


def test_the_two_tokens_do_not_stand_in_for_each_other() -> None:
    import jwt
    from choregos_api.security import (
        RunClaims,
        mint_egress_token,
        mint_run_token,
        verify_egress_token,
        verify_run_token,
    )

    run_token = mint_run_token("run-x", project_slug="p", work_item_key="P-1", ttl_minutes=5)
    claims = verify_run_token(run_token)
    egress = mint_egress_token(claims)
    assert verify_egress_token(egress) == RunClaims("run-x", "p", "P-1", claims.expires_at)
    with pytest.raises(jwt.InvalidAudienceError):
        verify_run_token(egress)
    with pytest.raises(jwt.InvalidAudienceError):
        verify_egress_token(run_token)
