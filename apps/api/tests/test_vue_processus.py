"""La validation rend la vue « processus » : la console l'affiche à côté de la carte (ADR 0031)."""

from __future__ import annotations

import pathlib

from httpx import AsyncClient

RACINE = pathlib.Path(__file__).resolve().parents[3]


async def test_la_validation_dit_chaque_transition_en_clair(client: AsyncClient) -> None:
    source = (RACINE / "demo/workflows/staffing.yaml").read_text(encoding="utf-8")
    reponse = await client.post("/api/v1/workflows/validate", json={"yaml": source})
    assert reponse.status_code == 200, reponse.text
    etapes = reponse.json()["process"]
    assert len(etapes) == 5
    assert all(e["sentence"].startswith("From “") for e in etapes)
    assert {e["actor_type"] for e in etapes} == {"agent", "human"}
