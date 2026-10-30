"""HTTP triggers (S51-01, ADR 0113): another system opens a work item in a given workflow, or in the one
the project's routing picks, with its own token; a person can fire the same trigger by hand.

Every call that reaches the project leaves a `received_events` row, refused ones included, whose
summary holds what the platform decided and nothing the payload said.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

INCIDENT = """apiVersion: choregos/v1
kind: Workflow
metadata:
  name: incident
  version: 1
  inputs:
    type: object
    required: [number]
    additionalProperties: false
    properties:
      number: {type: string, minLength: 1}
      ci: {type: string}
actors:
  desk: {type: human, group: service-desk}
states:
  triage: {display: Triage, kind: wait}
  done: {display: Done, terminal: true}
transitions:
  - {id: t-close, from: triage, to: done, by: desk}
"""

CHANGE = INCIDENT.replace("name: incident", "name: change").replace("required: [number]", "required: []")

TRIGGERS = [
    {
        "name": "incident",
        "title": "{/number} — {/short_description}",
        "body": "Caller: {/caller/name}",
        "key": "/sys_id",
        "labels": ["{/category}"],
        "fields": {"number": "/number", "ci": "/cmdb_ci"},
        "workflow": "incident",
    },
    {"name": "routed", "title": "{/summary}", "labels": ["{/kind}"]},
    {
        "name": "urgent",
        "title": "{/summary}",
        "workflow": "change",
        "when": {"/priority": ["1", "2"], "/active": "true"},
    },
    {"name": "signed", "title": "{/summary}", "workflow": "change", "auth": "signature"},
]

SNOW = {
    "sys_id": "9d385017c611228701d22104cc95c371",
    "number": "INC0010042",
    "short_description": "Payroll export fails",
    "caller": {"name": "Ada Lovelace"},
    "category": "software",
    "cmdb_ci": "payroll-db",
}


async def _project(client: AsyncClient, slug: str = "desk") -> dict[str, Any]:
    created = await client.post(
        "/api/v1/orgs/varga/projects",
        json={"slug": slug, "name": slug, "config": {"slug": slug, "org": "varga", "triggers": TRIGGERS}},
    )
    assert created.status_code == 201, created.text
    project = dict(created.json())
    for name, text in (("incident", INCIDENT), ("change", CHANGE)):
        published = await client.put(
            f"/api/v1/projects/{project['id']}/workflows/{name}", json={"yaml": text}
        )
        assert published.status_code in {200, 201}, published.text
    return project


async def _token(client: AsyncClient, project: dict[str, Any], name: str) -> dict[str, Any]:
    minted = await client.post(f"/api/v1/projects/{project['id']}/triggers/{name}/token")
    assert minted.status_code == 201, minted.text
    return dict(minted.json())


async def _call(client: AsyncClient, name: str, token: str, payload: Any, **headers: str) -> Any:
    return await client.post(
        f"/api/v1/webhooks/triggers/varga/desk/{name}",
        json=payload,
        headers={"Authorization": f"Bearer {token}", **headers},
    )


async def _received(client: AsyncClient, project: dict[str, Any]) -> list[dict[str, Any]]:
    page = await client.get(f"/api/v1/projects/{project['id']}/received-events")
    assert page.status_code == 200, page.text
    return list(reversed(page.json()["items"]))


async def _item(client: AsyncClient, project: dict[str, Any], key: str) -> dict[str, Any]:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        row = (
            await session.execute(
                select(WorkItem).where(WorkItem.project_id == project["id"], WorkItem.tracker_key == key)
            )
        ).scalar_one()
        return {
            "title": row.title,
            "body": row.body_snapshot,
            "fields": row.fields,
            "state": row.state,
            "labels": (row.documents or {}).get("labels"),
            "initiated_by": row.initiated_by,
            "created_by": row.created_by,
        }


async def test_a_call_opens_and_starts_a_work_item_in_the_trigger_s_workflow(
    client: AsyncClient, admin: str
) -> None:
    from choregos_api.temporal import get_temporal

    project = await _project(client)
    minted = await _token(client, project, "incident")
    assert minted["token"].startswith("chtrg_")
    assert minted["url"].endswith("/api/v1/webhooks/triggers/varga/desk/incident")

    answer = await _call(client, "incident", minted["token"], SNOW)

    assert answer.status_code == 202, answer.text
    ack = answer.json()
    assert ack["outcome"] == "created_work_item" and ack["workflow"] == "incident"
    assert ack["work_item_key"] == "DESK-incident.9d385017c611228701d22104cc95c371"
    item = await _item(client, project, ack["work_item_key"])
    assert item["title"] == "INC0010042 — Payroll export fails"
    assert item["fields"] == {"number": "INC0010042", "ci": "payroll-db"}
    assert item["state"] == "triage" and item["body"] == "Caller: Ada Lovelace"
    assert item["initiated_by"] == "trigger:incident"
    assert item["labels"] == ["trigger:incident", "software"]
    started = get_temporal().started  # type: ignore[attr-defined]
    assert any(ack["work_item_key"] in workflow_id for workflow_id in started), started


async def test_without_a_workflow_the_project_s_routing_chooses(client: AsyncClient, admin: str) -> None:
    project = await _project(client)
    routing = {
        "default": "change",
        "rules": [{"when": {"labels_all": ["trigger:routed", "outage"]}, "workflow": "incident"}],
    }
    put = await client.put(f"/api/v1/projects/{project['id']}/workflow-routing", json=routing)
    assert put.status_code == 200, put.text
    token = (await _token(client, project, "routed"))["token"]

    outage = await _call(client, "routed", token, {"summary": "Site down", "kind": "outage"})
    other = (await _call(client, "routed", token, {"summary": "New laptop", "kind": "request"})).json()

    # The rule sent the outage to `incident`, whose required `number` this trigger does not map.
    assert outage.status_code == 422 and "workflow `incident`" in outage.json()["detail"], outage.text
    assert other["outcome"] == "created_work_item" and other["workflow"] == "change", "the default"


async def test_the_routing_reads_the_trigger_and_the_payload_s_labels(
    client: AsyncClient, admin: str
) -> None:
    project = await _project(client)
    routing = {
        "default": "incident",
        "rules": [{"when": {"labels_all": ["trigger:routed", "request"]}, "workflow": "change"}],
    }
    assert (
        await client.put(f"/api/v1/projects/{project['id']}/workflow-routing", json=routing)
    ).status_code == 200
    token = (await _token(client, project, "routed"))["token"]

    routed = (await _call(client, "routed", token, {"summary": "New laptop", "kind": "request"})).json()

    assert routed["outcome"] == "created_work_item" and routed["workflow"] == "change"
    item = await _item(client, project, routed["work_item_key"])
    assert item["labels"] == ["trigger:routed", "request"]


async def test_a_replayed_delivery_and_a_second_call_for_the_same_key_open_nothing(
    client: AsyncClient, admin: str
) -> None:
    project = await _project(client)
    token = (await _token(client, project, "incident"))["token"]

    first = (await _call(client, "incident", token, SNOW)).json()
    replay = (await _call(client, "incident", token, SNOW)).json()
    update = (await _call(client, "incident", token, SNOW | {"short_description": "Still failing"})).json()
    keyed = (await _call(client, "incident", token, SNOW, **{"Idempotency-Key": "d-1"})).json()
    keyed_again = (await _call(client, "incident", token, SNOW, **{"Idempotency-Key": "d-1"})).json()

    assert first["outcome"] == "created_work_item"
    assert replay["duplicate"] is True and replay["outcome"] is None
    assert update["outcome"] == "already_open" and update["work_item_key"] == first["work_item_key"]
    assert keyed["outcome"] == "already_open" and keyed_again["duplicate"] is True
    rows = await _received(client, project)
    assert [r["outcome"] for r in rows] == ["created_work_item", "already_open", "already_open"]


async def test_a_wrong_token_or_an_unknown_trigger_is_refused_and_records_nothing(
    client: AsyncClient, admin: str
) -> None:
    project = await _project(client)
    token = (await _token(client, project, "incident"))["token"]

    wrong = await _call(client, "incident", token + "x", SNOW)
    none = await client.post("/api/v1/webhooks/triggers/varga/desk/incident", json=SNOW)
    no_token_yet = await _call(client, "routed", token, {"summary": "x"})
    unknown = await _call(client, "nope", token, SNOW)
    other_org = await client.post(
        "/api/v1/webhooks/triggers/acme/desk/incident", json=SNOW, headers={"X-Choregos-Token": token}
    )

    assert wrong.status_code == 401 and none.status_code == 401
    assert no_token_yet.status_code == 404 and unknown.status_code == 404 and other_org.status_code == 404
    assert await _received(client, project) == []


async def test_a_rotated_token_replaces_the_old_one(client: AsyncClient, admin: str) -> None:
    project = await _project(client)
    old = (await _token(client, project, "incident"))["token"]
    new = (await _token(client, project, "incident"))["token"]

    assert (await _call(client, "incident", old, SNOW)).status_code == 401
    by_header = await client.post(
        "/api/v1/webhooks/triggers/varga/desk/incident", json=SNOW, headers={"X-Choregos-Token": new}
    )
    assert by_header.status_code == 202, by_header.text
    by_query = await client.post(
        f"/api/v1/webhooks/triggers/varga/desk/incident?token={new}", json=SNOW | {"sys_id": "other"}
    )
    assert by_query.status_code == 202, by_query.text


async def test_a_refused_call_answers_why_and_is_recorded_without_the_payload(
    client: AsyncClient, admin: str
) -> None:
    project = await _project(client)
    token = (await _token(client, project, "incident"))["token"]

    no_key = await _call(client, "incident", token, {"number": "INC1", "short_description": "secret text"})
    again = await _call(client, "incident", token, {"number": "INC1", "short_description": "secret text"})
    no_number = await _call(client, "incident", token, {"sys_id": "abc", "short_description": "secret text"})
    not_json = await client.post(
        "/api/v1/webhooks/triggers/varga/desk/incident",
        content=b"short_description=secret text",
        headers={"Authorization": f"Bearer {token}"},
    )
    fixed = await _call(client, "incident", token, {"sys_id": "abc", "number": "INC2"})

    assert no_key.status_code == 422 and "/sys_id" in no_key.json()["detail"]
    assert again.status_code == 422, "a refusal keeps no dedup row: the same call is judged again"
    assert no_number.status_code == 422 and "fields refused by workflow" in no_number.json()["detail"]
    assert not_json.status_code == 422
    assert fixed.status_code == 202 and fixed.json()["outcome"] == "created_work_item", fixed.text
    rows = await _received(client, project)
    assert [r["outcome"] for r in rows] == ["refused"] * 4 + ["created_work_item"]
    assert all(r["source"] == "trigger:incident" and r["event_type"] == "trigger.http" for r in rows)
    assert "workflow incident" in rows[-1]["summary"]
    page = await client.get(f"/api/v1/projects/{project['id']}/received-events")
    for secret in ("secret text", "INC1", "INC2"):
        assert secret not in page.text, secret


async def test_an_external_tracker_refuses_the_trigger(
    client: AsyncClient, admin: str, monkeypatch: Any
) -> None:
    from choregos_api.services import tickets

    project = await _project(client)
    token = (await _token(client, project, "incident"))["token"]

    async def external(*_: Any) -> bool:
        return False

    monkeypatch.setattr(tickets, "le_tracker_est_interne", external)
    refused = await _call(client, "incident", token, SNOW)

    assert refused.status_code == 409, refused.text
    assert [r["outcome"] for r in await _received(client, project)] == ["refused"]


async def test_a_person_fires_the_trigger_by_hand(client: AsyncClient, admin: str) -> None:
    project = await _project(client)

    fired = await client.post(f"/api/v1/projects/{project['id']}/triggers/incident/fire", json=SNOW)

    assert fired.status_code == 200, fired.text
    item = await _item(client, project, fired.json()["work_item_key"])
    assert item["initiated_by"] == "person:admin@varga.dev"
    (row,) = await _received(client, project)
    assert row["event_type"] == "trigger.manual" and row["outcome"] == "created_work_item"


async def test_the_list_shows_the_url_and_whether_a_token_exists_never_the_token(
    client: AsyncClient, admin: str
) -> None:
    project = await _project(client)
    token = (await _token(client, project, "incident"))["token"]

    listed = await client.get(f"/api/v1/projects/{project['id']}/triggers")

    assert listed.status_code == 200, listed.text
    assert [(t["name"], t["has_token"], t["workflow"]) for t in listed.json()] == [
        ("incident", True, "incident"),
        ("routed", False, None),
        ("urgent", False, "change"),
        ("signed", False, "change"),
    ]
    assert token not in listed.text
    assert (await client.post(f"/api/v1/projects/{project['id']}/triggers/nope/token")).status_code == 404


async def test_a_viewer_mints_no_token_and_fires_nothing(client: AsyncClient, admin: str) -> None:
    from .conftest import login

    project = await _project(client)
    added = await client.post(
        "/api/v1/orgs/varga/members", json={"email": "viewer@varga.dev", "role": "viewer"}
    )
    assert added.status_code in {200, 201}, added.text
    await login(client, "viewer@varga.dev")

    minted = await client.post(f"/api/v1/projects/{project['id']}/triggers/incident/token")
    fired = await client.post(f"/api/v1/projects/{project['id']}/triggers/incident/fire", json=SNOW)

    assert minted.status_code == 403 and fired.status_code == 403


async def test_a_payload_its_when_does_not_match_is_acknowledged_and_ignored(
    client: AsyncClient, admin: str
) -> None:
    """S51-05: a filter is not a refusal: 202, nothing opened, a row that says so."""
    project = await _project(client)
    token = (await _token(client, project, "urgent"))["token"]

    low = await _call(client, "urgent", token, {"summary": "Slow page", "priority": 4, "active": True})
    missing = await _call(client, "urgent", token, {"summary": "No priority", "active": True})
    high = await _call(client, "urgent", token, {"summary": "Site down", "priority": 1, "active": True})

    assert low.status_code == 202 and low.json()["outcome"] == "ignored" and not low.json()["work_item_key"]
    assert missing.json()["outcome"] == "ignored"
    assert high.json()["outcome"] == "created_work_item", high.text
    rows = await _received(client, project)
    assert [r["outcome"] for r in rows] == ["ignored", "ignored", "created_work_item"]
    assert "did not match `when`" in rows[0]["summary"]


async def test_a_signed_trigger_takes_the_signature_and_not_the_token(
    client: AsyncClient, admin: str
) -> None:
    """S51-04: `X-Choregos-Signature: sha256=<HMAC of the body>`, hex or base64; a token is refused."""
    import base64
    import hashlib
    import hmac
    import json

    project = await _project(client)
    minted = await _token(client, project, "signed")
    secret = minted["signing_secret"].encode()
    body = json.dumps({"summary": "Signed"}).encode()
    mac = hmac.new(secret, body, hashlib.sha256)
    url = "/api/v1/webhooks/triggers/varga/desk/signed"

    def post(headers: dict[str, str], content: bytes = body) -> Any:
        return client.post(url, content=content, headers={"Content-Type": "application/json", **headers})

    by_token = await post({"Authorization": f"Bearer {minted['token']}"})
    wrong = await post({"X-Choregos-Signature": "sha256=" + "0" * 64})
    tampered = await post({"X-Choregos-Signature": f"sha256={mac.hexdigest()}"}, body + b" ")
    by_hex = await post({"X-Choregos-Signature": f"sha256={mac.hexdigest()}"})
    by_b64 = await post(
        {"X-Choregos-Signature": "sha256=" + base64.b64encode(mac.digest()).decode(), "Idempotency-Key": "2"}
    )

    assert by_token.status_code == 401 and wrong.status_code == 401 and tampered.status_code == 401
    assert by_hex.status_code == 202 and by_hex.json()["outcome"] == "created_work_item", by_hex.text
    assert by_b64.status_code == 202 and by_b64.json()["outcome"] == "created_work_item", by_b64.text
    rotated = (await _token(client, project, "signed"))["signing_secret"]
    assert rotated != minted["signing_secret"], "the signing secret changes with the token"
    stale = await post({"X-Choregos-Signature": f"sha256={mac.hexdigest()}", "Idempotency-Key": "3"})
    assert stale.status_code == 401
