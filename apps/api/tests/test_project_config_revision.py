# SPDX-License-Identifier: Apache-2.0
"""A project configuration carries a revision; a PATCH from a stale base answers 409 (S47-06).

Two editors of the same configuration used to overwrite each other: `PATCH /projects/{id}` replaces the
whole `config`. `GET` now returns `config_revision` (an opaque digest of the name and the configuration);
a PATCH that sends the `base_revision` it read answers 409 `stale_base` when the project changed since,
and writes nothing. A client that sends no revision keeps the last-write-wins behaviour.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient


def _config(project: dict[str, Any], **keys: Any) -> dict[str, Any]:
    """The project's current configuration with some keys replaced."""
    return {**project["config"], **keys}


async def _get(client: AsyncClient, project: dict[str, Any]) -> dict[str, Any]:
    response = await client.get(f"/api/v1/projects/{project['id']}")
    assert response.status_code == 200, response.text
    return dict(response.json())


async def test_a_stale_base_revision_is_refused_and_nothing_is_lost(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    url = f"/api/v1/projects/{project['id']}"
    read = await _get(client, project)
    revision = read["config_revision"]
    assert revision, "GET /projects/{id} returns the revision to send back"

    first = await client.patch(
        url, json={"config": _config(read, notify={"slack_channel": "#one"}), "base_revision": revision}
    )
    assert first.status_code == 200, first.text
    second_revision = first.json()["config_revision"]
    assert second_revision and second_revision != revision

    # The second editor read the same project as the first, and writes from that older read.
    stale = await client.patch(
        url, json={"config": _config(read, notify={"slack_channel": "#two"}), "base_revision": revision}
    )
    assert stale.status_code == 409, stale.text
    assert stale.headers["content-type"].startswith("application/problem+json")
    assert stale.json()["code"] == "stale_base"
    assert stale.json()["latest_revision"] == second_revision

    after = await _get(client, project)
    assert after["config"]["notify"]["slack_channel"] == "#one", "the refused write changed nothing"
    assert after["config"] == first.json()["config"]
    assert after["config_revision"] == second_revision

    # Starting again from the revision it was told about, the second editor's change goes through.
    retried = await client.patch(
        url,
        json={"config": _config(after, notify={"slack_channel": "#two"}), "base_revision": second_revision},
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["config"]["notify"]["slack_channel"] == "#two"


async def test_a_stale_base_revision_refuses_a_rename_too(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    url = f"/api/v1/projects/{project['id']}"
    revision = (await _get(client, project))["config_revision"]
    assert (
        await client.patch(url, json={"name": "Billing v2", "base_revision": revision})
    ).status_code == 200
    stale = await client.patch(url, json={"name": "Billing v3", "base_revision": revision})
    assert stale.status_code == 409 and stale.json()["code"] == "stale_base"
    assert (await _get(client, project))["name"] == "Billing v2"


async def test_a_patch_without_base_revision_still_writes(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    url = f"/api/v1/projects/{project['id']}"
    read = await _get(client, project)
    # Someone else writes in between: a client that sends no revision overwrites, as before.
    assert (await client.patch(url, json={"name": "Renamed"})).status_code == 200
    without = await client.patch(url, json={"config": _config(read, labels={"team": "core"})})
    assert without.status_code == 200, without.text
    assert without.json()["config"]["labels"] == {"team": "core"}
    explicit_null = await client.patch(url, json={"name": "Again", "base_revision": None})
    assert explicit_null.status_code == 200, explicit_null.text


async def test_the_revision_follows_the_configuration(client: AsyncClient, project: dict[str, Any]) -> None:
    url = f"/api/v1/projects/{project['id']}"
    first = await _get(client, project)
    again = await _get(client, project)
    assert first["config_revision"] == again["config_revision"]
    assert len(first["config_revision"]) == 32

    renamed = await client.patch(url, json={"name": "Billing, renamed"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["config_revision"] != first["config_revision"]
    assert (await _get(client, project))["config_revision"] == renamed.json()["config_revision"]

    # A PATCH that changes nothing leaves the revision where it was.
    unchanged = await client.patch(url, json={"name": "Billing, renamed"})
    assert unchanged.json()["config_revision"] == renamed.json()["config_revision"]


async def test_the_models_put_moves_the_revision(client: AsyncClient, project: dict[str, Any]) -> None:
    before = (await _get(client, project))["config_revision"]
    put = await client.put(
        f"/api/v1/projects/{project['id']}/models",
        json={"profiles": {"standard": {"litellm_model": "platform/standard"}}, "allow_unvalidated": True},
    )
    assert put.status_code == 200, put.text
    after = (await _get(client, project))["config_revision"]
    assert after != before, "a client that read before the PUT answers 409 on its next PATCH"
    stale = await client.patch(
        f"/api/v1/projects/{project['id']}", json={"name": "Late", "base_revision": before}
    )
    assert stale.status_code == 409 and stale.json()["latest_revision"] == after


async def test_the_audit_entry_names_what_changed(client: AsyncClient, project: dict[str, Any]) -> None:
    url = f"/api/v1/projects/{project['id']}"
    read = await _get(client, project)
    patched = await client.patch(
        url,
        json={
            "config": _config(read, notify={"slack_channel": "#ops"}),
            "base_revision": read["config_revision"],
        },
    )
    assert patched.status_code == 200, patched.text
    audit = (await client.get("/api/v1/audit", params={"target_type": "project"})).json()["items"]
    (entry,) = [e for e in audit if e["action"] == "project.update"]
    changes = entry["payload"]["changes"]
    assert changes["notify"] == {
        "before": read["config"].get("notify"),
        "after": patched.json()["config"]["notify"],
    }
    assert changes["notify"]["after"]["slack_channel"] == "#ops"
    assert "repo" not in changes, "a key whose value did not change is not listed"
    assert entry["payload"]["name_changed"] is False

    await client.patch(url, json={"name": "Billing, renamed"})
    audit = (await client.get("/api/v1/audit", params={"target_type": "project"})).json()["items"]
    (renamed,) = [e for e in audit if e["action"] == "project.update" and e["payload"]["name_changed"]]
    assert renamed["payload"]["changes"] == {}
