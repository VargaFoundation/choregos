"""The helpers that put a person in a group and let them answer a human step, shared by the group tests."""

from __future__ import annotations

from typing import Any

from httpx import ASGITransport, AsyncClient

from ..conftest import login


async def _member(client: AsyncClient, email: str, role: str, groups: list[str]) -> None:
    from choregos_api.db.models import User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    added = await client.post("/api/v1/orgs/varga/members", json={"email": email, "role": role})
    assert added.status_code in {200, 201}, added.text
    async with session_scope() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        user.groups = groups


async def _session(app: Any, email: str) -> AsyncClient:
    other = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await login(other, email)
    return other


async def _decide(app: Any, email: str, item_id: str) -> Any:
    session = await _session(app, email)
    try:
        return await session.post(f"/api/v1/work-items/{item_id}/decisions", json={"kind": "approve"})
    finally:
        await session.aclose()
