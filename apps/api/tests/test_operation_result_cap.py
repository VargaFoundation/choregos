"""How much of a tool's answer the agent reads, per operation (S40-17).

#755 and #759 held every result to one platform-wide cap, `broker_result_max_bytes`: a log reader
that needs 256 KiB and a lookup that should never say more than 4 KiB got the same 64 KiB. An
organisation administrator now sets the cap per connector operation, a catalogue entry declares
its own, and `broker_result_ceiling_bytes` bounds both. Each test below fails without its guard.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from . import test_catalogue_outils
from .test_catalogue_outils import _intercepter, _run_avec_catalogue
from .test_courtier import OUTILS, _preparer

pytestmark = pytest.mark.asyncio

OPERATION = "/api/v1/orgs/varga/connectors/fournisseur/operations/suivi_commande"
LINE = "line of a very long log\n"


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    import choregos_adapters
    from choregos_adapters.fakes.mcp import FakeMcpServer

    fake = FakeMcpServer(outils=[dict(o) for o in OUTILS], jeton="cle-fournisseur")
    monkeypatch.setattr(choregos_adapters, "FAUX_MCP", fake)
    monkeypatch.setenv("CONNECTOR_TEST_CLE_FOURNISSEUR", "cle-fournisseur")
    yield fake


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


async def _read(client: AsyncClient, base: str, headers: dict[str, str]) -> dict[str, Any]:
    answer = await client.post(f"{base}/fournisseur__suivi_commande", headers=headers, json={"numero": "42"})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["status_code"] == 200, body
    result: dict[str, Any] = body["result"]
    return result


async def test_a_lower_operation_cap_cuts_what_the_default_lets_through(
    client: AsyncClient, project: dict[str, Any], server: Any
) -> None:
    server.reponses["suivi_commande"] = LINE * 1_000  # 24 KB: under the 64 KiB default
    base, headers = await _preparer(client, project, ["*"])
    assert "truncated" not in await _read(client, base, headers)

    patched = await client.patch(OPERATION, json={"result_max_bytes": 4096})
    assert patched.status_code == 200, patched.text
    assert patched.json()["result_max_bytes"] == 4096

    result = await _read(client, base, headers)
    assert _size(result) <= 4096, "the operation's cap holds"
    assert result["truncated"]["total_bytes"] > 4096
    assert "[truncated by the platform:" in result["content"][-1]["text"]


async def test_a_higher_operation_cap_lets_through_what_the_default_cuts(
    client: AsyncClient, project: dict[str, Any], server: Any
) -> None:
    server.reponses["suivi_commande"] = LINE * 5_000  # 120 KB: over the 64 KiB default
    base, headers = await _preparer(client, project, ["*"])
    assert "truncated" in await _read(client, base, headers)

    assert (await client.patch(OPERATION, json={"result_max_bytes": 262_144})).status_code == 200
    result = await _read(client, base, headers)
    assert "truncated" not in result, "the operation reads its whole answer"
    assert result["content"][0]["text"] == LINE * 5_000


async def test_a_cap_above_the_ceiling_is_refused_and_null_restores_the_default(
    client: AsyncClient, project: dict[str, Any], server: Any
) -> None:
    from choregos_api.config import get_settings

    server.reponses["suivi_commande"] = LINE * 1_000
    base, headers = await _preparer(client, project, ["*"])
    ceiling = get_settings().broker_result_ceiling_bytes

    above = await client.patch(OPERATION, json={"result_max_bytes": ceiling + 1})
    assert above.status_code == 422, above.text
    assert str(ceiling) in above.text, "the refusal names the ceiling"
    assert (await client.patch(OPERATION, json={"result_max_bytes": 512})).status_code == 422

    assert (await client.patch(OPERATION, json={"result_max_bytes": 4096})).status_code == 200
    assert "truncated" in await _read(client, base, headers)

    cleared = await client.patch(OPERATION, json={"result_max_bytes": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["result_max_bytes"] is None
    assert "truncated" not in await _read(client, base, headers), "back to the platform's default"

    untouched = await client.patch(OPERATION, json={"policy": "allowed"})
    assert untouched.json()["result_max_bytes"] is None, "absent leaves it"


async def test_a_ceiling_lowered_later_clips_a_cap_set_before_it(
    client: AsyncClient, project: dict[str, Any], server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import get_settings

    server.reponses["suivi_commande"] = LINE * 5_000
    base, headers = await _preparer(client, project, ["*"])
    assert (await client.patch(OPERATION, json={"result_max_bytes": 262_144})).status_code == 200

    monkeypatch.setattr(get_settings(), "broker_result_ceiling_bytes", 8192)
    result = await _read(client, base, headers)
    assert _size(result) <= 8192, "the ceiling wins over a cap stored before it was lowered"
    assert "[truncated by the platform:" in result["content"][-1]["text"]


async def test_a_catalogue_tool_is_held_to_the_cap_its_entry_declares(
    client: AsyncClient, project: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        test_catalogue_outils, "CATALOGUE", test_catalogue_outils.CATALOGUE + "    result_max_bytes: 2048\n"
    )
    run_id, headers = await _run_avec_catalogue(project, tmp_path, monkeypatch)
    request = httpx.Request("GET", "https://api.annuaire.example/v1/search")
    answer = {"profils": [{"nom": "a profile"}] * 200}  # ~4 KB: under the 64 KiB default
    _intercepter(monkeypatch, httpx.Response(200, json=answer, request=request), [])

    called = await client.post(
        f"/api/v1/internal/runs/{run_id}/tools/recherche_profils", headers=headers, json={"metier": "x"}
    )
    assert called.status_code == 200, called.text
    result = called.json()["result"]
    assert _size(result) <= 2048, "the entry's cap holds"
    assert result["truncated"]["total_bytes"] > 2048
    assert result["text"].startswith('{"profils"')


async def test_a_governed_write_result_is_held_to_its_operation_cap(
    client: AsyncClient, project: dict[str, Any], server: Any
) -> None:
    """A write's result is what its action's effect recorded (#241): the same cap applies to it."""
    from choregos_api.db.models import Action, ActionEffect
    from choregos_api.db.session import TOUT, session_scope
    from sqlalchemy import select

    base, headers = await _preparer(client, project, ["*"])
    opened = await client.patch(
        "/api/v1/orgs/varga/connectors/fournisseur/operations/commander_poste",
        json={"policy": "allowed", "result_max_bytes": 4096},
    )
    assert opened.status_code == 200, opened.text
    first = (
        await client.post(f"{base}/fournisseur__commander_poste", headers=headers, json={"modele": "x14"})
    ).json()
    assert first["status_code"] == 202, first
    action_id = first["result"]["action"]

    recorded = {"content": [{"type": "text", "text": LINE * 1_000}]}  # 24 KB: under the default
    async with session_scope(orgs=TOUT) as session:
        action = await session.get(Action, action_id)
        assert action is not None
        action.status = "succeeded"
        effect = (
            await session.execute(select(ActionEffect).where(ActionEffect.action_id == action_id))
        ).scalar_one_or_none()
        if effect is None:
            session.add(
                ActionEffect(
                    org_id=action.org_id,
                    action_id=action_id,
                    position=0,
                    key=f"{action_id}:0",
                    effect="connector.call",
                    status="succeeded",
                    result=recorded,
                )
            )
        else:
            effect.status, effect.result = "succeeded", recorded

    again = await client.post(f"{base}/fournisseur__commander_poste", headers=headers, json={"modele": "x14"})
    assert again.json()["status_code"] == 200, again.text
    result = again.json()["result"]
    assert _size(result) <= 4096, "the write's cap holds"
    assert "[truncated by the platform:" in json.dumps(result)
