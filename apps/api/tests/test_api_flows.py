"""Parcours fonctionnels de l'API : projets, workflow, RBAC, décisions, webhooks, API interne."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest
from httpx import AsyncClient

from .conftest import login


async def test_me_requires_session(client: AsyncClient) -> None:
    response = await client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["title"] == "Non authentifié"


async def test_login_and_me(client: AsyncClient, org: str) -> None:
    await login(client, "admin@varga.dev")
    me = (await client.get("/api/v1/me")).json()
    assert me["email"] == "admin@varga.dev"
    assert me["memberships"][0]["role"] == "org_admin"


async def test_project_creation_sets_defaults(client: AsyncClient, project: dict[str, Any]) -> None:
    assert project["status"] == "draft"
    workflow = (await client.get(f"/api/v1/projects/{project['id']}/workflow")).json()
    assert workflow["name"] == "default-simple"
    policy = (await client.get(f"/api/v1/projects/{project['id']}/policy")).json()
    assert policy["name"] == "solo"


async def test_workflow_validation_reports_line_and_column(client: AsyncClient, admin: str) -> None:
    bad = """
apiVersion: choregos/v1
kind: Workflow
metadata: { name: t, version: 1 }
actors: { dev: { type: agent, role: implement } }
states:
  a: { display: A }
  b: { display: B, terminal: true }
transitions:
  - { from: a, to: nulle_part, by: dev }
"""
    response = await client.post("/api/v1/workflows/validate", json={"yaml": bad})
    assert response.status_code == 200
    body = response.json()
    assert not body["valid"]
    error = next(e for e in body["errors"] if e["code"] == "state.unknown")
    assert error["line"] and error["path"].startswith("transitions[0]")


async def test_put_invalid_workflow_is_422(client: AsyncClient, project: dict[str, Any]) -> None:
    response = await client.put(
        f"/api/v1/projects/{project['id']}/workflow",
        json={"yaml": "apiVersion: choregos/v1\nkind: Workflow\nmetadata: {name: x, version: 1}\n"},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_put_valid_workflow_bumps_version(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_core.dsl import template_yaml

    response = await client.put(
        f"/api/v1/projects/{project['id']}/workflow", json={"yaml": template_yaml("advanced")}
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "advanced"
    current = (await client.get(f"/api/v1/projects/{project['id']}/workflow")).json()
    assert current["name"] == "advanced"


async def test_rbac_developer_cannot_write_workflow(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.db.models import Membership, Organization, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        org_row = (await session.execute(select(Organization))).scalars().first()
        user = User(email="dev@varga.dev", display_name="dev")
        session.add(user)
        await session.flush()
        session.add(Membership(user_id=user.id, org_id=org_row.id, role="developer"))

    async with AsyncClient(transport=client._transport, base_url="http://test") as dev_client:
        await login(dev_client, "dev@varga.dev")
        response = await dev_client.put(f"/api/v1/projects/{project['id']}/workflow", json={"yaml": "x: 1"})
        assert response.status_code == 403


async def test_connector_crud_and_test(client: AsyncClient, project: dict[str, Any]) -> None:
    response = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker",
        json={"type": "github-issues", "config": {"repo": "varga/billing-api"}},
    )
    assert response.status_code == 200
    listed = (await client.get(f"/api/v1/projects/{project['id']}/connectors")).json()
    assert listed[0]["kind"] == "tracker"
    tested = (await client.post(f"/api/v1/projects/{project['id']}/connectors/tracker/test")).json()
    assert tested["ok"] is True
    types = (await client.get("/api/v1/connectors/types")).json()
    assert any(t["type"] == "github-issues" for t in types)


async def test_github_webhook_signature_and_dedup(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.config import get_settings

    settings = get_settings()
    settings.github_webhook_secret = "s3cr3t"
    payload = {
        "action": "labeled",
        "repository": {"full_name": "varga/billing-api"},
        "issue": {
            "number": 42,
            "title": "Corriger les avoirs",
            "body": "…",
            "labels": [{"name": "agent-ready"}],
        },
        "label": {"name": "agent-ready"},
        "sender": {"login": "augustin"},
    }
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(b"s3cr3t", body, hashlib.sha256).hexdigest()
    headers = {
        "X-GitHub-Event": "issues",
        "X-GitHub-Delivery": "delivery-1",
        "X-Hub-Signature-256": signature,
        "content-type": "application/json",
    }
    first = await client.post("/api/v1/webhooks/github", content=body, headers=headers)
    assert first.status_code == 202, first.text
    assert first.json()["events"] == 1

    duplicate = await client.post("/api/v1/webhooks/github", content=body, headers=headers)
    assert duplicate.json()["duplicate"] is True

    bad = await client.post(
        "/api/v1/webhooks/github", content=body, headers={**headers, "X-Hub-Signature-256": "sha256=deadbeef"}
    )
    assert bad.status_code == 401
    settings.github_webhook_secret = ""


async def test_webhook_starts_one_workflow_only(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.temporal import get_temporal

    payload = {
        "action": "labeled",
        "repository": {"full_name": "varga/billing-api"},
        "issue": {"number": 7, "title": "T", "labels": [{"name": "agent-ready"}]},
        "label": {"name": "agent-ready"},
        "sender": {"login": "a"},
    }
    body = json.dumps(payload).encode()
    for delivery in ("d1", "d2"):
        await client.post(
            "/api/v1/webhooks/github",
            content=body,
            headers={
                "X-GitHub-Event": "issues",
                "X-GitHub-Delivery": delivery,
                "content-type": "application/json",
            },
        )
    started = get_temporal().started  # type: ignore[attr-defined]
    assert len([k for k in started if k.startswith("wi-billing-api")]) == 1


async def test_internal_run_api(client: AsyncClient, project: dict[str, Any]) -> None:
    """Le runner lit son input, journalise, dépose findings et résultat — et rien d'autre."""
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token
    from choregos_contracts import StageInput

    stage_input = StageInput.model_validate(
        json.loads(
            (
                __import__("pathlib").Path(
                    __import__("choregos_contracts").schemas_dir() / "examples" / "stage-input.example.json"
                )
            ).read_text()
        )
    )
    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key="varga/billing-api#1", title="T", state="ready")
        session.add(item)
        await session.flush()
        run = Run(
            id=stage_input.run_id,
            work_item_id=item.id,
            project_id=project["id"],
            stage_role="implement",
            transition_id="t-implement",
            status="running",
            stage_input=stage_input.model_dump(mode="json", by_alias=True),
            allowed_paths=["src/orders/**"],
        )
        session.add(run)
        run_id = run.id

    token = mint_run_token(
        run_id, project_slug="billing-api", work_item_key="varga/billing-api#1", ttl_minutes=30
    )
    headers = {"Authorization": f"Bearer {token}"}

    fetched = await client.get(f"/api/v1/internal/runs/{run_id}/input", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["run_id"] == run_id

    other = await client.get("/api/v1/internal/runs/autre-run/input", headers=headers)
    assert other.status_code == 403

    events = await client.post(
        f"/api/v1/internal/runs/{run_id}/events",
        headers=headers,
        json={"events": [{"seq": 1, "type": "session/update", "payload": {"text": "…"}}]},
    )
    assert events.json()["accepted"] == 1
    again = await client.post(
        f"/api/v1/internal/runs/{run_id}/events",
        headers=headers,
        json={"events": [{"seq": 1, "type": "session/update", "payload": {"text": "…"}}]},
    )
    assert again.json()["accepted"] == 0, "le journal est idempotent par seq"

    finding = await client.post(
        f"/api/v1/internal/runs/{run_id}/findings",
        headers=headers,
        json={"title": "N+1 sur les lignes", "type": "perf", "severity": "medium", "evidence": "repo.py:88"},
    )
    assert finding.status_code == 201
    assert finding.json()["accepted"] is True

    scope = await client.post(
        f"/api/v1/internal/runs/{run_id}/scope-change",
        headers=headers,
        json={"paths": ["src/shared/util.py"], "justification": "fonction commune"},
    )
    assert scope.json()["decision"] == "granted"

    denied = await client.post(
        f"/api/v1/internal/runs/{run_id}/scope-change",
        headers=headers,
        json={"paths": [".choregos/workflow.yaml"], "justification": "non"},
    )
    assert denied.json()["decision"] == "denied"

    result = {
        "schema": "choregos/StageResult/v1",
        "status": "done",
        "summary": "fait",
        "evidence": {"tests_passed": True, "tests_run": 12},
        "outputs": {"size": "M"},
    }
    posted = await client.post(f"/api/v1/internal/runs/{run_id}/result", headers=headers, json=result)
    assert posted.json()["status"] == "recorded"
    reposted = await client.post(f"/api/v1/internal/runs/{run_id}/result", headers=headers, json=result)
    assert reposted.json()["status"] == "already_recorded", "le dépôt de résultat est idempotent"

    after = await client.get(f"/api/v1/internal/runs/{run_id}/input", headers=headers)
    assert after.status_code == 409, "résultat déjà posté : le runner doit sortir en 0"


async def test_internal_requires_valid_token(client: AsyncClient) -> None:
    response = await client.get("/api/v1/internal/runs/x/input")
    assert response.status_code == 401
    response = await client.get("/api/v1/internal/runs/x/input", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 403


async def test_findings_listing_and_triage(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.db.models import Finding
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        row = Finding(
            project_id=project["id"],
            title="Fuite de connexion",
            type="bug",
            severity="high",
            evidence="db.py:12",
            status="pending",
        )
        session.add(row)
        await session.flush()
        finding_id = row.id

    listed = (await client.get(f"/api/v1/projects/{project['id']}/findings")).json()
    assert listed["items"][0]["title"] == "Fuite de connexion"

    dismissed = await client.post(f"/api/v1/findings/{finding_id}/actions", json={"action": "dismiss"})
    assert dismissed.json()["status"] == "dismissed"


async def test_costs_report(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.db.models import CostLedger
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    async with session_scope() as session:
        for index in range(3):
            session.add(
                CostLedger(
                    project_id=project["id"],
                    provider="anthropic",
                    model="platform/standard",
                    stage_role="implement" if index else "refine",
                    size="M",
                    tokens_in=1000,
                    tokens_out=100,
                    cost_usd=0.5,
                    cost_eur=0.46,
                    fx_rate=0.92,
                    ts=utcnow(),
                )
            )

    by_stage = (
        await client.get(f"/api/v1/projects/{project['id']}/costs", params={"group_by": "stage"})
    ).json()
    assert {row["key"] for row in by_stage["rows"]} == {"implement", "refine"}
    assert by_stage["total"]["cost_usd"] == pytest.approx(1.5)


async def test_audit_trail_records_mutations(client: AsyncClient, project: dict[str, Any]) -> None:
    audit = (await client.get("/api/v1/audit")).json()
    actions = {entry["action"] for entry in audit["items"]}
    assert "project.create" in actions


async def test_templates_listing_from_disk(client: AsyncClient, admin: str) -> None:
    listed = (await client.get("/api/v1/templates")).json()
    assert isinstance(listed, list)


async def test_platform_backends_and_executors(client: AsyncClient, admin: str) -> None:
    backends = (await client.get("/api/v1/platform/backends")).json()
    assert {b["name"] for b in backends} >= {"codex", "claude-code"}
    assert next(b for b in backends if b["name"] == "claude-code")["enabled"] is True
    assert "openhands" not in {b["name"] for b in backends}
    updated = await client.put(
        "/api/v1/platform/backends",
        json={"name": "codex", "enabled": False, "disabled_reason": "conformité rouge"},
    )
    assert updated.json()["enabled"] is False
    executors = (await client.get("/api/v1/platform/executors")).json()
    assert {e["kind"] for e in executors} >= {"tekton", "k8s_job"}


async def test_webhook_jira_cree_le_ticket_et_demarre_le_workflow(
    client: AsyncClient, project: dict[str, Any], monkeypatch: Any
) -> None:
    """Un webhook Jira suit exactement le même chemin qu'un webhook GitHub (S13-04)."""
    from choregos_api.config import get_settings

    monkeypatch.setattr(get_settings(), "generic_webhook_secret", "s3cret")
    payload = {
        "webhookEvent": "jira:issue_updated",
        "user": {"displayName": "Augustin"},
        "issue": {"key": "BILLING-API-9", "fields": {"summary": "Avoirs", "labels": ["agent-ready"]}},
        "changelog": {"items": [{"field": "labels", "fromString": "", "toString": "agent-ready"}]},
    }
    response = await client.post(
        "/api/v1/webhooks/jira",
        json=payload,
        headers={"X-Choregos-Secret": "s3cret", "X-Atlassian-Webhook-Identifier": "d-1"},
    )
    assert response.status_code == 202
    assert response.json()["accepted"] is True

    rejeu = await client.post(
        "/api/v1/webhooks/jira",
        json=payload,
        headers={"X-Choregos-Secret": "s3cret", "X-Atlassian-Webhook-Identifier": "d-1"},
    )
    assert rejeu.json()["duplicate"] is True, "une livraison rejouée ne compte qu'une fois"


async def test_webhook_gitlab_refuse_un_jeton_invalide(
    client: AsyncClient, project: dict[str, Any], monkeypatch: Any
) -> None:
    from choregos_api.config import get_settings

    monkeypatch.setattr(get_settings(), "generic_webhook_secret", "s3cret")
    response = await client.post(
        "/api/v1/webhooks/gitlab",
        json={"object_kind": "issue"},
        headers={"X-Gitlab-Token": "faux", "X-Gitlab-Event": "Issue Hook"},
    )
    assert response.status_code == 401


async def test_un_projet_sans_depot_se_cree(client: AsyncClient, admin: str) -> None:
    """Limite n°1 de l'ADR 0012 : un métier qui instruit des dossiers n'a pas de dépôt,
    et devait pourtant en déclarer un qui ne servait à rien."""
    reponse = await client.post(
        "/api/v1/orgs/varga/projects",
        json={
            "slug": "staffing",
            "name": "Staffing",
            "config": {"slug": "staffing", "org": "varga"},
        },
    )
    assert reponse.status_code == 201, reponse.text
    assert reponse.json()["config"].get("repo") is None

    from choregos_contracts import ProjectConfig

    config = ProjectConfig.model_validate(reponse.json()["config"])
    assert not config.has_repo
    with pytest.raises(ValueError, match="n'a pas de dépôt"):
        _ = config.repo_url


async def test_la_fiche_d_acces_replie_le_journal(
    client: AsyncClient, project: dict[str, Any], admin: str
) -> None:
    """Deux agents produisent deux cents événements en quelques minutes : personne ne les
    lit. La fiche répond à « à quoi a-t-il touché », et met les refus devant."""
    from choregos_api.db.models import Run, RunEvent, WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key="varga/x#7", title="T", state="ready")
        session.add(item)
        await session.flush()
        run = Run(
            id="run-acces-1",
            work_item_id=item.id,
            project_id=project["id"],
            stage_role="implement",
            status="succeeded",
        )
        session.add(run)
        evenements = [
            ("session/update", {"update": {"text": "je lis le ticket"}}),
            (
                "session/request_permission",
                {"kind": "read", "target": "/workspace/src/a.py", "allowed": True, "reason": "ok"},
            ),
            (
                "session/request_permission",
                {"kind": "read", "target": "/workspace/src/a.py", "allowed": True, "reason": "ok"},
            ),
            (
                "session/request_permission",
                {
                    "kind": "write",
                    "target": "/workspace/.env",
                    "allowed": False,
                    "reason": "fichier sensible",
                },
            ),
        ]
        from choregos_core import utcnow

        for seq, (type_, payload) in enumerate(evenements, start=1):
            session.add(RunEvent(run_id=run.id, seq=seq, type=type_, payload=payload, ts=utcnow()))

    reponse = await client.get("/api/v1/runs/run-acces-1/access")
    assert reponse.status_code == 200, reponse.text
    fiche = reponse.json()
    assert fiche["evenements"] == 4 and fiche["refus"] == 1
    assert fiche["acces"][0]["cible"] == "/workspace/.env", "le refus d'abord"
    assert fiche["acces"][0]["motifs"] == ["fichier sensible"]
    assert fiche["acces"][1]["demandes"] == 2, "deux lectures du même fichier, regroupées"
    # La prose de l'agent n'y entre pas : la fiche redeviendrait illisible.
    assert all("je lis le ticket" not in a["cible"] for a in fiche["acces"])


async def test_un_ticket_dont_le_workflow_est_mort_le_dit(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """L'API lit l'état Temporal ET la marque posée par le workflow : un ticket mort ne se cache plus.

    Banc du 2026-09-24 : deux workflows FAILED, deux tickets « en attente » à l'écran.
    """
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.temporal import FakeTemporal, WorkflowState, get_temporal

    async with session_scope() as session:
        item = WorkItem(
            project_id=project["id"],
            tracker_key="varga/billing-api#404",
            title="T",
            state="ready",
            temporal_wf_id="wi-billing-api-404",
            failure={"message": "le projet n'a pas de dépôt", "activity": "collect_run_artifacts"},
        )
        session.add(item)
        await session.flush()
        item_id = item.id
    fake = get_temporal()
    assert isinstance(fake, FakeTemporal)
    fake.described["wi-billing-api-404"] = WorkflowState(status="FAILED", failure="Activity task failed")

    body = (await client.get(f"/api/v1/work-items/{item_id}")).json()
    assert body["workflow_status"] == "FAILED"
    assert body["failure"]["message"] == "le projet n'a pas de dépôt"
    assert body["failure"]["activity"] == "collect_run_artifacts"

    # La liste ne demande rien à Temporal (un appel par ticket serait trop cher) : le statut
    # y est absent, mais la marque persistée, elle, y est.
    liste = (await client.get(f"/api/v1/projects/{project['id']}/work-items")).json()
    mort = next(i for i in liste["items"] if i["id"] == item_id)
    assert mort["workflow_status"] is None
    assert mort["failure"]["message"] == "le projet n'a pas de dépôt"
