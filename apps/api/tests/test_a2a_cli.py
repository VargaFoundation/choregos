# SPDX-License-Identifier: Apache-2.0
"""`choregos a2a`'s client against the real A2A door (S62-03), through ASGI.

The commands themselves are `packages/cli/tests/test_a2a.py`'s, against a mocked door; this drives the
CLI's own client code (`choregos_cli.a2a`): the card's fields, a message with fields and a file, its
idempotency, the follow across the door's hour cut, and the refusal of a `*` token.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from choregos_cli.a2a import A2aDoor, DoorError, build_message, file_part, follow, skills_of
from httpx import ASGITransport, AsyncClient

from .test_a2a_guide import _publish, _token

SKILL = "billing-api/ship-a-change"


def _door(app: Any, token: str) -> A2aDoor:
    return A2aDoor("http://test", "varga", token, transport=ASGITransport(app=app))


async def test_the_cli_starts_a_workflow_with_a_file_and_follows_it_across_the_hour_cut(
    app: Any, client: AsyncClient, project: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.a2a import door as door_module
    from choregos_api.db.models import ArtifactRecord, WorkItem
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await _publish(client, project)
    token = await _token(client)
    spec = tmp_path / "spec.pdf"
    spec.write_bytes(b"%PDF-1.7\n")

    async with _door(app, token) as door:
        skills = skills_of(await door.card())
        assert [s["id"] for s in skills] == [SKILL]
        assert skills[0]["fields"]["required"] == ["ticket"]

        message = build_message(
            SKILL,
            texts=["Apply the attached specification."],
            fields={"ticket": "FIN-43"},
            files=[file_part(str(spec))],
            title="Apply the specification",
        )
        sent = await door.call("message/send", {"message": message})
        assert sent["status"]["state"] == "working", sent
        # The same command again (its default message id): the same task, nothing started twice.
        again = build_message(
            SKILL,
            texts=["Apply the attached specification."],
            fields={"ticket": "FIN-43"},
            files=[file_part(str(spec))],
            title="Apply the specification",
        )
        assert (await door.call("message/send", {"message": again}))["id"] == sent["id"]

        async with session_scope() as session:
            item = await session.get(WorkItem, sent["id"])
            assert item is not None and item.title == "Apply the specification"
            assert (item.fields or {}).get("ticket") == "FIN-43"
            names = (
                await session.execute(
                    select(ArtifactRecord.name).where(ArtifactRecord.work_item_id == sent["id"])
                )
            ).scalars()
            assert list(names) == ["spec.pdf"]

        # Every stream is cut at once, as the door does after an hour: the CLI opens it again, and the
        # task fails while it is away.
        monkeypatch.setattr(door_module, "STREAM_LIFETIME", 0.0)
        reconnections = 0

        async def away() -> None:
            nonlocal reconnections
            reconnections += 1
            async with session_scope() as session:
                failed = await session.get(WorkItem, sent["id"])
                assert failed is not None
                failed.failure = {"reason": "boom"}

        seen: list[dict[str, Any]] = []
        final = await follow(door, sent["id"], seen.append, pause=away)
        assert reconnections == 1
        assert seen[0]["status"]["state"] == "working"
        assert seen[-1]["kind"] == "status-update" and seen[-1]["final"] is True
        assert final["status"]["state"] == "failed"
        assert final["metadata"]["choregos"]["key"] == item.tracker_key


async def test_the_cli_says_a_star_token_does_not_open_the_door(
    app: Any, client: AsyncClient, project: dict[str, Any]
) -> None:
    created = await client.post("/api/v1/me/tokens", json={"name": "cli", "scopes": ["*"]})
    assert created.status_code == 201, created.text
    async with _door(app, created.json()["token"]) as door:
        with pytest.raises(DoorError) as refused:
            await door.card()
    assert refused.value.status == 403 and refused.value.missing_scope
    assert "--scope a2a" in refused.value.explain()


async def test_refused_fields_reach_the_cli_with_their_reasons(
    app: Any, client: AsyncClient, project: dict[str, Any]
) -> None:
    await _publish(client, project)
    token = await _token(client)
    message = build_message(SKILL, texts=["no ticket"], fields={"priority": "high"}, files=[])
    async with _door(app, token) as door:
        with pytest.raises(DoorError) as refused:
            await door.call("message/send", {"message": message})
    assert refused.value.code == -32602
    assert "fields refused by workflow" in refused.value.explain()
    assert "'ticket' is a required property" in refused.value.explain()
