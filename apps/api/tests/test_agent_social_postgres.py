"""Row-level security of the watch, star and notification tables on a real PostgreSQL (ADR 0152)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from .conftest import without_postgres

pytestmark = without_postgres


@pytest.fixture
async def world(pg_app: Any) -> dict[str, str]:
    """Organisations `a` and `b`; in `a` an agent and two people; in `b` an agent."""
    from choregos_api.db.models import Agent, Organization, User
    from choregos_api.db.session import session_scope

    ids: dict[str, str] = {}
    async with session_scope(orgs="*") as session:
        for slug in ("a", "b"):
            org = Organization(slug=slug, name=slug)
            session.add(org)
            await session.flush()
            agent = Agent(org_id=org.id, slug="reviewer", kind="internal", display_name="r", status="active")
            session.add(agent)
            await session.flush()
            ids[f"org_{slug}"], ids[f"agent_{slug}"] = org.id, agent.id
        for name in ("alice", "bob"):
            user = User(email=f"{name}@a.test", display_name=name)
            session.add(user)
            await session.flush()
            ids[name] = user.id
    return ids


async def test_the_three_tables_are_isolated_by_organisation_and_notifications_by_person(
    world: dict[str, str],
) -> None:
    from choregos_api.db.models import AgentNotification, AgentStar, AgentWatch
    from choregos_api.db.session import limit_to_organisations, name_the_user, session_scope

    async with session_scope(orgs="*") as session:
        for who in ("alice", "bob"):
            session.add(AgentWatch(org_id=world["org_a"], agent_id=world["agent_a"], user_id=world[who]))
            session.add(AgentStar(org_id=world["org_a"], agent_id=world["agent_a"], user_id=world[who]))
            session.add(
                AgentNotification(
                    org_id=world["org_a"],
                    agent_id=world["agent_a"],
                    user_id=world[who],
                    kind="version_published",
                    detail={},
                )
            )
        session.add(AgentStar(org_id=world["org_b"], agent_id=world["agent_b"], user_id=world["alice"]))

    async def count(model: Any) -> int:
        return len((await session.execute(select(model))).all())

    async with session_scope(orgs=["a"]) as session:
        await name_the_user(session, world["alice"])
        assert await count(AgentWatch) == 2 and await count(AgentStar) == 2, "the organisation's counters"
        assert await count(AgentNotification) == 1, "only my own items"
    async with session_scope(orgs=["b"]) as session:
        await name_the_user(session, world["alice"])
        assert await count(AgentWatch) == 0 and await count(AgentStar) == 1
        assert await count(AgentNotification) == 0
    async with session_scope() as session:
        await limit_to_organisations(session, ["a"])  # no user named
        assert await count(AgentNotification) == 0
        assert await count(AgentStar) == 2
    async with session_scope() as session:  # no scope at all
        assert await count(AgentWatch) == 0 and await count(AgentNotification) == 0
    async with session_scope(orgs="*") as session:
        assert await count(AgentNotification) == 2, "the platform scope reads"


async def test_another_organisation_cannot_write_a_row_and_another_person_cannot_change_an_item(
    world: dict[str, str],
) -> None:
    from choregos_api.db.models import AgentNotification, AgentStar
    from choregos_api.db.session import name_the_user, session_scope
    from sqlalchemy import update

    with pytest.raises(DBAPIError):
        async with session_scope(orgs=["b"]) as session:
            session.add(AgentStar(org_id=world["org_a"], agent_id=world["agent_a"], user_id=world["alice"]))
    async with session_scope(orgs="*") as session:
        session.add(
            AgentNotification(
                org_id=world["org_a"],
                agent_id=world["agent_a"],
                user_id=world["alice"],
                kind="forked",
                detail={},
            )
        )
    async with session_scope(orgs=["a"]) as session:
        await name_the_user(session, world["bob"])
        done = await session.execute(update(AgentNotification).values(read_at=None))
        assert done.rowcount == 0, "bob updates nothing of alice's"  # type: ignore[attr-defined]
    async with session_scope(orgs=["a"]) as session:
        await name_the_user(session, world["alice"])
        done = await session.execute(update(AgentNotification).values(read_at=None))
        assert done.rowcount == 1  # type: ignore[attr-defined]
