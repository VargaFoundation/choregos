"""The links the orchestrator writes for an organisation use its verified host (ADR 0175, S74-03).

A question to a person goes to the tracker (a comment) and to the notifier (a courtesy): both carry the
console link of the work item. `northwind` owns `agentic.northwind.example`; without it the link is the instance's.
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_api import tenant_host

from .conftest import Fixture

pytestmark = pytest.mark.asyncio

HOST = "agentic.northwind.example"


def _declare(*, verified: bool) -> None:
    tenant_host.reset()
    tenant_host.declare_host_resolver(lambda session, host: "northwind" if verified and host == HOST else None)
    tenant_host.declare_org_host_resolver(lambda session, slug: HOST if slug == "northwind" else None)


@pytest.fixture(autouse=True)
def _no_host() -> Any:
    tenant_host.reset()
    yield
    tenant_host.reset()


async def _ask(setup: Fixture) -> tuple[list[str], list[str]]:
    from choregos_orchestrator.activities.tracker import create_human_request

    await create_human_request(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "transition_id": "t-1",
            "kind": "question",
            "payload": {"question": "Which currency?"},
        }
    )
    notifier: Any = setup.adapters.notify
    tracker: Any = setup.adapters.tracker
    comments = [comment.body for item in tracker.items.values() for comment in item.comments]
    return [str(message.url) for _, message in notifier.sent], comments


async def test_a_question_links_the_work_item_on_the_organisations_host(setup: Fixture) -> None:
    _declare(verified=True)
    urls, comments = await _ask(setup)
    assert urls and all(url.startswith(f"http://{HOST}/p/billing-api/items/") for url in urls), urls
    assert comments and f"http://{HOST}" in " ".join(comments)


async def test_without_a_verified_host_the_link_is_the_instances(setup: Fixture) -> None:
    _declare(verified=False)
    urls, comments = await _ask(setup)
    assert urls and all(url.startswith("http://localhost:3000/p/billing-api/items/") for url in urls), urls
    assert comments and "http://localhost:3000" in " ".join(comments) and HOST not in " ".join(comments)
