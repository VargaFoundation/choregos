"""Validation returns the "process" view: the console shows it beside the map (ADR 0031)."""

from __future__ import annotations

import pathlib

from httpx import AsyncClient

ROOT = pathlib.Path(__file__).resolve().parents[3]


async def test_validation_states_each_transition_in_plain_words(client: AsyncClient) -> None:
    source = (ROOT / "demo/workflows/staffing.yaml").read_text(encoding="utf-8")
    response = await client.post("/api/v1/workflows/validate", json={"yaml": source})
    assert response.status_code == 200, response.text
    steps = response.json()["process"]
    assert len(steps) == 5
    assert all(e["sentence"].startswith("From “") for e in steps)
    assert {e["actor_type"] for e in steps} == {"agent", "human"}
