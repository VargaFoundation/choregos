"""La transaction d'une requête est validée AVANT que la réponse parte — pas après.

FastAPI ferme par défaut les dépendances `yield` APRÈS l'envoi de la réponse. `get_db` y validait sa
transaction : un client recevait son `201`, puis la base écrivait. Deux conséquences, mesurées sur la
pile intégrée de l'essai (uvicorn, PostgreSQL) :
- un client rapide lit un état pas encore écrit — juste après la connexion de développement, la
  requête suivante ne trouvait pas l'utilisateur : « session périmée » ;
- un `commit` qui échoue après coup laisse au client une réponse de succès sur une écriture perdue.

`ASGITransport` attend la fin de l'application avant de rendre la réponse : il ne voyait rien. Ce
test appelle l'application ASGI directement et regarde l'ordre des événements.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_le_commit_precede_l_envoi_de_la_reponse(
    app: Any, client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    evenements: list[str] = []
    origine = AsyncSession.commit

    async def commit(self: AsyncSession) -> None:
        evenements.append("commit")
        await origine(self)

    monkeypatch.setattr(AsyncSession, "commit", commit)
    corps = json.dumps({"name": "ordre", "expires_in_days": 1}).encode()
    cookie = f"choregos_session={client.cookies.get('choregos_session')}".encode()
    portee = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/me/tokens",
        "raw_path": b"/api/v1/me/tokens",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"test"), (b"content-type", b"application/json"), (b"cookie", cookie)],
        "client": ("127.0.0.1", 50000),
        "server": ("test", 80),
    }
    lu = False

    async def recevoir() -> dict[str, Any]:
        nonlocal lu
        if not lu:
            lu = True
            return {"type": "http.request", "body": corps, "more_body": False}
        return {"type": "http.disconnect"}

    statut: list[int] = []

    async def envoyer(message: dict[str, Any]) -> None:
        evenements.append(message["type"])
        if message["type"] == "http.response.start":
            statut.append(message["status"])

    await app(portee, recevoir, envoyer)
    assert statut == [201], evenements
    assert "commit" in evenements
    assert evenements.index("commit") < evenements.index("http.response.start"), evenements
