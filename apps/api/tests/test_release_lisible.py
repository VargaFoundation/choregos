# SPDX-License-Identifier: Apache-2.0
"""Une release se lit, quel que soit le signal d'embarquement qu'elle range (S22-25).

Sur le locataire dev, le 10/10, la première release réelle rendait 500 à `getTrainStatus` et
`listReleases` : le train range chaque élément tel que l'interpréteur l'a signalé, et le contrat de
l'élément refuse les champs qu'il ne connaît pas.
"""

from __future__ import annotations

from choregos_api.db.models import Release
from choregos_api.services import release_dto

#: L'élément du lot tel que `train-dev-prod` l'a rangé pour #4, le 10/10.
SIGNAL_DE_4 = {
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


def test_une_release_qui_range_le_signal_brut_se_lit() -> None:
    release = Release(
        id="r-1",
        project_id="p-1",
        env="prod",
        batch_no=1,
        status="departing",
        items=[SIGNAL_DE_4],
        verdict={},
    )

    dto = release_dto(release, "dev")

    [item] = dto.items
    assert item.work_item_key == "DiametralGroup/choregos-sandbox-dev#4"
    assert item.pr_url == "https://github.com/DiametralGroup/choregos-sandbox-dev/pull/17"
    assert item.risk == "medium"
    assert "start_payload" not in item.model_dump()
