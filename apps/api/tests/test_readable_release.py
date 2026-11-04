# SPDX-License-Identifier: Apache-2.0
"""A release is readable, whatever onboarding signal it stores (S22-25).

On the dev tenant, on 10/10, the first real release answered 500 to `getTrainStatus` and
`listReleases`: the train stores each item as the interpreter reported it, and the item contract
refuses the fields it does not know.
"""

from __future__ import annotations

from choregos_api.db.models import Release
from choregos_api.services import release_dto

#: The batch item as `train-dev-prod` stored it for #4, on 10/10.
SIGNAL_OF_4 = {
    "approval": {"group": None, "required": False},
    "infra_pr_url": None,
    "labels": [],
    "merged_at": "2026-10-09T15:40:04.531105+00:00",
    "pr_url": "https://github.com/DiametralGroup/choregos-sandbox-dev/pull/17",
    "risk": "medium",
    "sha": "",
    "start_payload": {"env": "prod", "project_slug": "dev"},
    "title": "Credits are not deducted from the invoice total",
    "work_item_key": "DiametralGroup/choregos-sandbox-dev#4",
}


def test_a_release_that_stores_the_raw_signal_is_readable() -> None:
    release = Release(
        id="r-1",
        project_id="p-1",
        env="prod",
        batch_no=1,
        status="departing",
        items=[SIGNAL_OF_4],
        verdict={},
    )

    dto = release_dto(release, "dev")

    [item] = dto.items
    assert item.work_item_key == "DiametralGroup/choregos-sandbox-dev#4"
    assert item.pr_url == "https://github.com/DiametralGroup/choregos-sandbox-dev/pull/17"
    assert item.risk == "medium"
    assert "start_payload" not in item.model_dump()
