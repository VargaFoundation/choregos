"""Train metrics read the train's real query and field (S26-03).

`_trains` asked for a query named `status` — the train defines `status_query` — and read a `state`
field the train does not return. The error was swallowed, so `choregos_train_frozen` and
`choregos_train_status` never had a sample.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient


async def test_a_frozen_train_is_exported(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.metriques import rafraichir
    from choregos_api.temporal import FakeTemporal, get_temporal, train_id

    fake = get_temporal()
    assert isinstance(fake, FakeTemporal)
    # The answer the workflow's `status_query` gives (workflows/train.py), nothing more.
    fake.queries[f"{train_id('billing-api', 'prod')}:status_query"] = {
        "status": "staging",
        "batch_size": 1,
        "pending_items": ["BILLING-API-1"],
        "frozen": True,
        "freeze_reason": "incident",
        "next_departure": None,
        "window_open": True,
        "release_id": None,
    }
    await rafraichir()
    body = (await client.get("/metrics")).text
    assert 'choregos_train_frozen{env="prod",project="billing-api"} 1.0' in body
    assert 'choregos_train_status{env="prod",project="billing-api"} 2.0' in body


def test_the_metrics_ask_a_query_the_train_defines() -> None:
    from choregos_api.metriques import ETATS_TRAIN, TRAIN_STATUS_QUERY
    from choregos_contracts import ReleaseStatus
    from choregos_orchestrator.workflows.train import ReleaseTrain

    assert callable(getattr(ReleaseTrain, TRAIN_STATUS_QUERY, None))
    assert set(ETATS_TRAIN) <= {str(s) for s in ReleaseStatus}, "every mapped state is a real status"
