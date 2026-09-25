"""Les routeurs que rien n'exerçait : tickets, runs, coûts, trains, webhooks, templates, plateforme.

Troisième tranche de P1-3 (couverture 78 % → 80 %). Chaque test parle à l'API par HTTP,
avec les fakes ; ce qui a besoin d'une ligne en base (un run, une release, une dépense)
l'insère par le modèle, comme l'orchestrateur le ferait.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest
from choregos_core.domain import utcnow
from httpx import AsyncClient

from .conftest import login


async def _interne(client: AsyncClient, project: dict[str, Any]) -> dict[str, Any]:
    """Le projet de test reçoit ses demandes DANS Choregos, et une première est posée."""
    put = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker", json={"type": "internal", "config": {}}
    )
    assert put.status_code in {200, 201}, put.text
    created = await client.post(
        f"/api/v1/projects/{project['id']}/work-items",
        json={"title": "Un chef de projet", "body": "6 mois", "size": "M"},
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


async def _run(project: dict[str, Any], item: dict[str, Any], **champs: Any) -> str:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        run = Run(
            work_item_id=item["id"],
            project_id=project["id"],
            stage_role="implement",
            attempt=1,
            status=champs.pop("status", "succeeded"),
            backend="claude-code",
            model="platform/standard",
            **champs,
        )
        session.add(run)
        await session.flush()
        return str(run.id)


# ───────────────────────── tickets ─────────────────────────


async def test_un_ticket_se_liste_se_lit_et_raconte_sa_timeline(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    item = await _interne(client, project)
    page = (await client.get(f"/api/v1/projects/{project['id']}/work-items")).json()
    assert [i["tracker_key"] for i in page["items"]] == ["BILLING-API-1"]
    lu = (await client.get(f"/api/v1/work-items/{item['id']}")).json()
    assert lu["title"] == "Un chef de projet" and lu["size"] == "M" and lu["temporal_wf_id"]
    timeline = await client.get(f"/api/v1/work-items/{item['id']}/timeline")
    assert timeline.status_code == 200 and isinstance(timeline.json(), list)
    assert (await client.get("/api/v1/work-items/absent")).status_code == 404


async def test_un_workflow_mort_se_voit_sur_le_ticket(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.temporal import WorkflowState, get_temporal

    item = await _interne(client, project)
    get_temporal().described[item["temporal_wf_id"]] = WorkflowState(status="FAILED")
    lu = (await client.get(f"/api/v1/work-items/{item['id']}")).json()
    assert lu["workflow_status"] == "FAILED"


async def test_les_actions_de_controle_partent_au_workflow_et_s_auditent(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.temporal import get_temporal

    item = await _interne(client, project)
    for action, paused in (("pause", True), ("resume", False), ("stop", False)):
        reponse = await client.post(
            f"/api/v1/work-items/{item['id']}/actions", json={"action": action, "reason": "test"}
        )
        assert reponse.status_code == 202, reponse.text
        assert (await client.get(f"/api/v1/work-items/{item['id']}")).json()["paused"] is paused
    assert len(get_temporal().signals) >= 3
    audit = (await client.get("/api/v1/audit", params={"target_type": "work_item"})).json()
    assert {e["action"] for e in audit["items"]} >= {"workitem.pause", "workitem.resume", "workitem.stop"}
    assert (
        await client.post(f"/api/v1/work-items/{item['id']}/actions", json={"action": "mark_agent_ready"})
    ).status_code == 202


async def test_une_decision_tranche_la_demande_humaine_en_attente(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope

    item = await _interne(client, project)
    sans = await client.post(f"/api/v1/work-items/{item['id']}/decisions", json={"kind": "approve"})
    assert sans.status_code == 409, "aucune demande en attente : rien à trancher"
    async with session_scope(orgs="*") as session:
        session.add(
            HumanRequest(
                work_item_id=item["id"],
                project_id=project["id"],
                kind="question",
                payload={"question": "en euros ?"},
                requested_at=utcnow(),
            )
        )
    reponse = await client.post(
        f"/api/v1/work-items/{item['id']}/decisions", json={"kind": "answer", "answer": "oui, en euros"}
    )
    assert reponse.status_code == 202, reponse.text
    assert reponse.json()["decided_by"] == "admin@varga.dev"
    encore = await client.post(f"/api/v1/work-items/{item['id']}/decisions", json={"kind": "approve"})
    assert encore.status_code == 409, "déjà tranchée"


# ───────────────────────── runs ─────────────────────────


async def test_un_run_se_lit_avec_ses_evenements_son_diff_et_ses_acces(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import CostLedger, RunEvent
    from choregos_api.db.session import session_scope

    item = await _interne(client, project)
    run_id = await _run(
        project,
        item,
        allowed_paths=["src/**"],
        transcript_url="s3://choregos/runs/r/transcript.ndjson",
        result={
            "schema": "choregos/StageResult/v1",
            "status": "done",
            "summary": "ok",
            "artifacts": {
                "reports": {
                    "diff_files": json.dumps(
                        [
                            {"path": "src/a.py", "additions": 3, "deletions": 1},
                            {"path": "infra/main.tf", "status": "added"},
                        ]
                    )
                }
            },
        },
    )
    async with session_scope(orgs="*") as session:
        for seq, (type_, payload) in enumerate(
            [
                (
                    "session/request_permission",
                    {
                        "kind": "write",
                        "target": "/workspace/.env",
                        "allowed": False,
                        "reason": "fichier sensible",
                    },
                ),
                (
                    "session/request_permission",
                    {"kind": "read", "target": "/workspace/src/a.py", "allowed": True},
                ),
                ("run.result", {"status": "done"}),
            ],
            start=1,
        ):
            session.add(RunEvent(run_id=run_id, seq=seq, type=type_, payload=payload, ts=utcnow()))
        session.add(
            CostLedger(
                project_id=project["id"],
                work_item_id=item["id"],
                run_id=run_id,
                kind="tool",
                provider="adresse",
                model="verifier_adresse",
                cost_eur=0.002,
                ts=utcnow(),
            )
        )

    runs = (await client.get(f"/api/v1/work-items/{item['id']}/runs")).json()
    assert [r["id"] for r in runs] == [run_id]
    assert (await client.get(f"/api/v1/runs/{run_id}")).json()["stage_role"] == "implement"
    assert (await client.get("/api/v1/runs/absent")).status_code == 404

    events = (await client.get(f"/api/v1/runs/{run_id}/events")).json()
    assert [e["seq"] for e in events] == [1, 2, 3]
    assert [
        e["seq"] for e in (await client.get(f"/api/v1/runs/{run_id}/events", params={"after_seq": 2})).json()
    ] == [3]

    acces = (await client.get(f"/api/v1/runs/{run_id}/access")).json()
    assert acces["refus"] == 1 and acces["cout_outils_eur"] == 0.002
    assert any(a["nature"] == "tool" for a in acces["acces"]), "l'appel au catalogue compte comme un accès"

    diff = (await client.get(f"/api/v1/runs/{run_id}/diff")).json()
    assert {f["path"]: f["in_scope"] for f in diff["files"]} == {"src/a.py": True, "infra/main.tf": False}
    assert (await client.get(f"/api/v1/runs/{run_id}/transcript")).json()[
        "content_type"
    ] == "application/x-ndjson"


async def test_un_run_sans_transcript_repond_404(client: AsyncClient, project: dict[str, Any]) -> None:
    item = await _interne(client, project)
    run_id = await _run(project, item)
    assert (await client.get(f"/api/v1/runs/{run_id}/transcript")).status_code == 404
    assert (await client.get(f"/api/v1/runs/{run_id}/diff")).json()["files"] == []


# ───────────────────────── coûts ─────────────────────────


async def _depenses(client: AsyncClient, project: dict[str, Any]) -> dict[str, Any]:
    from choregos_api.db.models import CostLedger
    from choregos_api.db.session import session_scope

    item = await _interne(client, project)
    run_id = await _run(project, item)
    async with session_scope(orgs="*") as session:
        maintenant = utcnow()
        session.add(
            CostLedger(
                project_id=project["id"],
                work_item_id=item["id"],
                run_id=run_id,
                kind="model",
                provider="anthropic",
                model="claude",
                stage_role="implement",
                tokens_in=1000,
                tokens_out=200,
                cost_usd=0.5,
                cost_eur=0.46,
                ts=maintenant,
            )
        )
        session.add(
            CostLedger(
                project_id=project["id"],
                work_item_id=item["id"],
                run_id=run_id,
                kind="model",
                provider="anthropic",
                model="claude",
                stage_role="verify",
                tokens_in=100,
                cost_usd=0.1,
                cost_eur=0.092,
                ts=maintenant - timedelta(days=1),
            )
        )
        session.add(
            CostLedger(
                project_id=project["id"],
                work_item_id=item["id"],
                run_id=run_id,
                kind="tool",
                provider="adresse",
                model="verifier_adresse",
                cost_usd=0.01,
                cost_eur=0.0092,
                ts=maintenant,
            )
        )
        session.add(
            CostLedger(
                project_id=project["id"],
                kind="model",
                model="vieux",
                cost_usd=99,
                cost_eur=99,
                ts=maintenant - timedelta(days=60),
            )
        )
    return item


async def test_le_rapport_de_couts_se_groupe_et_borne_sa_fenetre(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _depenses(client, project)
    par_nature = (
        await client.get(f"/api/v1/projects/{project['id']}/costs", params={"group_by": "kind"})
    ).json()
    assert {r["key"]: r["runs"] for r in par_nature["rows"]} == {"model": 2, "tool": 1}
    assert par_nature["total"]["cost_usd"] == pytest.approx(0.61), "la dépense de deux mois est hors fenêtre"
    par_etape = (
        await client.get(f"/api/v1/projects/{project['id']}/costs", params={"group_by": "stage"})
    ).json()
    assert {r["key"] for r in par_etape["rows"]} >= {"implement", "verify"}
    par_jour = (await client.get(f"/api/v1/projects/{project['id']}/costs")).json()
    assert par_jour["group_by"] == "day" and len(par_jour["rows"]) == 2
    assert (
        await client.get(f"/api/v1/projects/{project['id']}/costs", params={"group_by": "planete"})
    ).status_code == 422
    depuis = utcnow().date().isoformat()
    assert (
        len(
            (await client.get(f"/api/v1/projects/{project['id']}/costs", params={"since": depuis})).json()[
                "rows"
            ]
        )
        == 1
    )


async def test_le_csv_porte_le_total_le_budget_et_le_bom_pour_excel(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _depenses(client, project)
    reponse = await client.get(f"/api/v1/projects/{project['id']}/costs.csv", params={"group_by": "kind"})
    assert reponse.status_code == 200 and reponse.headers["content-type"].startswith("text/csv")
    assert 'filename="couts-billing-api-kind-' in reponse.headers["content-disposition"]
    texte = reponse.text
    assert texte.startswith("\ufeff"), "le BOM, pour qu'Excel lise les accents"
    en_tete = texte.lstrip("\ufeff").splitlines()[0]
    assert en_tete == "kind;cout_usd;cout_eur;tokens_in;tokens_out;tokens_caches;runs"
    assert any(ligne.startswith("total;") for ligne in texte.splitlines())
    assert any(ligne.startswith("budget_quotidien_usd;") for ligne in texte.splitlines())


async def test_les_couts_d_une_organisation_se_lisent_par_projet(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _depenses(client, project)
    rapport = (await client.get("/api/v1/orgs/varga/costs", params={"group_by": "project"})).json()
    assert [r["key"] for r in rapport["rows"]] == ["billing-api"], "l'identifiant est traduit en slug"
    assert (await client.get("/api/v1/orgs/inconnue/costs")).status_code == 404


# ───────────────────────── trains ─────────────────────────


async def test_l_etat_du_train_vient_du_workflow_quand_il_repond(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.temporal import get_temporal, train_id

    get_temporal().queries[f"{train_id('billing-api', 'prod')}:status_query"] = {
        "status": "collecting",
        "batch_size": 2,
        "pending_items": ["BILLING-API-1", "BILLING-API-2"],
        "frozen": True,
        "freeze_reason": "rollback",
        "window_open": False,
    }
    etat = (await client.get(f"/api/v1/projects/{project['id']}/trains/prod")).json()
    assert (
        etat["batch_size"] == 2
        and etat["frozen"]
        and etat["freeze_reason"] == "rollback"
        and not etat["window_open"]
    )
    sans = (await client.get(f"/api/v1/projects/{project['id']}/trains/staging")).json()
    assert sans["status"] == "collecting" and sans["batch_size"] == 0


async def test_departs_gels_et_degels_sont_des_signaux_traces(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.temporal import get_temporal

    base = f"/api/v1/projects/{project['id']}/trains/prod"
    assert (await client.post(f"{base}/depart")).status_code == 202
    assert (await client.post(f"{base}/freeze", json={"reason": "x"})).status_code == 422, (
        "un motif de moins de trois caractères n'est pas un motif"
    )
    assert (await client.post(f"{base}/freeze", json={"reason": "incident en cours"})).status_code == 202
    assert (await client.post(f"{base}/unfreeze")).status_code == 202
    noms = [nom for wf, nom, _ in get_temporal().signals if wf == "train-billing-api-prod"]
    assert noms == ["depart_now", "freeze", "unfreeze"]
    audit = (await client.get("/api/v1/audit", params={"target_type": "train"})).json()
    assert {e["action"] for e in audit["items"]} == {"train.depart", "train.freeze", "train.unfreeze"}


async def test_une_release_s_approuve_et_s_abandonne(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.db.models import Release
    from choregos_api.db.session import session_scope
    from choregos_api.temporal import get_temporal

    async with session_scope(orgs="*") as session:
        release = Release(
            project_id=project["id"],
            env="prod",
            batch_no=3,
            status="awaiting_approval",
            items=[{"work_item_key": "BILLING-API-1", "sha": "abc123"}],
        )
        session.add(release)
        await session.flush()
        release_id = str(release.id)
    page = (await client.get(f"/api/v1/projects/{project['id']}/releases", params={"env": "prod"})).json()
    assert [r["id"] for r in page["items"]] == [release_id]
    lue = (await client.get(f"/api/v1/releases/{release_id}")).json()
    assert lue["batch_no"] == 3 and lue["project_slug"] == "billing-api"
    assert (
        await client.post(f"/api/v1/releases/{release_id}/approve", json={"note": "ok"})
    ).status_code == 202
    assert (await client.get(f"/api/v1/releases/{release_id}")).json()["approved_by"] == "admin@varga.dev"
    assert (
        await client.post(f"/api/v1/releases/{release_id}/abort", json={"reason": "smoke KO"})
    ).status_code == 202
    noms = [nom for wf, nom, _ in get_temporal().signals if wf == "train-billing-api-prod"]
    assert noms == ["approve", "abort"]
    assert (await client.get("/api/v1/releases/absente")).status_code == 404


# ───────────────────────── webhooks ─────────────────────────


async def test_un_cloudevent_tekton_est_achemine_une_seule_fois(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    corps = {
        "pipelineRun": {
            "metadata": {
                "name": "run-1",
                "labels": {
                    "choregos/project": "billing-api",
                    "choregos/work-item": "BILLING-API-1",
                    "choregos/sha": "abc",
                },
            }
        }
    }
    entetes = {"ce-type": "dev.tekton.event.pipelinerun.successful.v1", "ce-id": "ev-1"}
    premier = await client.post("/api/v1/webhooks/tekton", json=corps, headers=entetes)
    assert premier.status_code == 202 and premier.json()["duplicate"] is False
    second = await client.post("/api/v1/webhooks/tekton", json=corps, headers=entetes)
    assert second.json()["duplicate"] is True, "même ce-id : rien n'est acheminé deux fois"


async def test_argocd_et_alertmanager_exigent_le_secret_partage_puis_signalent(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import get_settings
    from choregos_api.temporal import get_temporal

    monkeypatch.setattr(get_settings(), "generic_webhook_secret", "s3cret")
    refus = await client.post("/api/v1/webhooks/argocd", json={"app": "billing-prod"})
    assert refus.status_code == 401
    ok = await client.post(
        "/api/v1/webhooks/argocd",
        json={
            "app": "billing-api-prod",
            "health": "Healthy",
            "project": "billing-api",
            "env": "prod",
            "revision": "abc",
            "delivery_id": "d-1",
        },
        headers={"X-Choregos-Secret": "s3cret"},
    )
    assert ok.status_code == 202 and ok.json()["events"] == 1
    assert any(
        wf == "train-billing-api-prod" and nom == "deploy_event" for wf, nom, _ in get_temporal().signals
    )
    encore = await client.post(
        "/api/v1/webhooks/argocd",
        json={"app": "x", "project": "billing-api", "env": "prod", "delivery_id": "d-1"},
        headers={"X-Choregos-Secret": "s3cret"},
    )
    assert encore.json()["duplicate"] is True

    alertes = {
        "alerts": [
            {
                "status": "firing",
                "fingerprint": "f-1",
                "labels": {"alertname": "ErrorBudget", "severity": "page", "project": "billing-api"},
            },
            {"status": "resolved", "fingerprint": "f-2", "labels": {"project": "inconnu"}},
        ]
    }
    reponse = await client.post(
        "/api/v1/webhooks/alertmanager", json=alertes, headers={"X-Choregos-Secret": "s3cret"}
    )
    assert reponse.status_code == 202 and reponse.json()["events"] == 2
    assert any(wf == "mem-billing-api" and nom == "alert" for wf, nom, _ in get_temporal().signals)
    assert (
        await client.post(
            "/api/v1/webhooks/alertmanager", json=alertes, headers={"X-Choregos-Secret": "s3cret"}
        )
    ).json()["events"] == 0


async def test_github_sans_secret_est_tolere_hors_production(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    reponse = await client.post(
        "/api/v1/webhooks/github",
        json={"zen": "ping"},
        headers={"X-GitHub-Event": "ping", "X-GitHub-Delivery": "p-1"},
    )
    assert reponse.status_code == 202 and reponse.json()["events"] == 0


# ───────────────────────── templates ─────────────────────────


async def test_les_templates_du_depot_se_listent_et_se_lisent(client: AsyncClient, admin: str) -> None:
    liste = (await client.get("/api/v1/templates")).json()
    assert liste, "le dépôt livre au moins un template"
    premier = liste[0]
    detail = (await client.get(f"/api/v1/templates/{premier['name']}")).json()
    assert detail["manifest"]["metadata"]["name"] == premier["name"]
    assert (await client.get("/api/v1/templates/inexistant")).status_code == 404


async def test_chaque_template_livre_passe_le_schema_des_manifestes(client: AsyncClient, admin: str) -> None:
    """Un template lu depuis le dépôt est servi sans validation ; le même, envoyé par l'API,
    était refusé : `github-aca` déclarait `requires.azure_capabilities`, que le schéma ne
    connaissait pas (trouvé par cette tranche de tests). Le schéma le porte désormais, et
    ce test empêche un template livré de dériver du contrat."""
    for resume in (await client.get("/api/v1/templates")).json():
        manifeste = (await client.get(f"/api/v1/templates/{resume['name']}")).json()["manifest"]
        manifeste["metadata"] = {**manifeste["metadata"], "name": f"copie-{resume['name']}"}
        reponse = await client.post("/api/v1/templates", json={"manifest": manifeste})
        assert reponse.status_code in {200, 201}, f"{resume['name']} : {reponse.text}"


async def test_un_template_se_cree_et_se_met_a_jour_par_un_administrateur(
    client: AsyncClient, admin: str
) -> None:
    # Un manifeste valide est celui d'un template livré, renommé : le schéma exige
    # `requires`, `defaults`, `inputs` et `steps`, et un test qui les invente vieillirait mal.
    livre = (await client.get("/api/v1/templates")).json()[0]
    manifeste = dict((await client.get(f"/api/v1/templates/{livre['name']}")).json()["manifest"])
    manifeste["metadata"] = {
        **manifeste["metadata"],
        "name": "maison",
        "version": "1.0.0",
        "display": "Maison",
    }
    cree = await client.post("/api/v1/templates", json={"manifest": manifeste, "is_published": False})
    assert cree.status_code in {200, 201}, cree.text
    assert cree.json()["name"] == "maison" and cree.json()["is_published"] is False
    manifeste["metadata"] = {**manifeste["metadata"], "version": "1.1.0"}
    maj = await client.put("/api/v1/templates/maison", json={"manifest": manifeste, "is_published": True})
    assert maj.status_code == 200 and maj.json()["version"] == "1.1.0" and maj.json()["is_published"] is True
    assert any(t["name"] == "maison" for t in (await client.get("/api/v1/templates")).json())
    invalide = await client.post("/api/v1/templates", json={"manifest": {"kind": "Autre"}})
    assert invalide.status_code == 422


# ───────────────────────── plateforme ─────────────────────────


async def test_backends_et_executeurs_se_listent_et_se_reglent(client: AsyncClient, admin: str) -> None:
    backends = (await client.get("/api/v1/platform/backends")).json()
    assert {b["name"] for b in backends} >= {"claude-code", "codex"} and next(
        b for b in backends if b["name"] == "claude-code"
    )["enabled"]
    coupe = await client.put(
        "/api/v1/platform/backends",
        json={"name": "codex", "enabled": False, "disabled_reason": "conformité KO"},
    )
    assert coupe.status_code == 200 and coupe.json()["enabled"] is False
    assert (
        next(b for b in (await client.get("/api/v1/platform/backends")).json() if b["name"] == "codex")[
            "disabled_reason"
        ]
        == "conformité KO"
    )
    executeurs = (await client.get("/api/v1/platform/executors")).json()
    assert {e["kind"] for e in executeurs} == {"tekton", "k8s_job", "local_docker", "aca"}
    regle = await client.put(
        "/api/v1/platform/executors",
        json={
            "kind": "k8s_job",
            "enabled": True,
            "namespace_pattern": "choregos",
            "runner_image": "local/choregos-runner:demo",
        },
    )
    assert regle.status_code == 200
    assert [e["kind"] for e in (await client.get("/api/v1/platform/executors")).json()] == ["k8s_job"], (
        "dès qu'une ligne existe, c'est elle qui fait foi"
    )
    assert (await client.get("/api/v1/platform/gateway/keys")).json() == []


async def test_les_reglages_de_plateforme_sont_reserves_aux_administrateurs(
    client: AsyncClient, org: str
) -> None:
    await login(client, "dev@varga.dev")
    assert (
        await client.put("/api/v1/platform/backends", json={"name": "codex", "enabled": False})
    ).status_code == 403
    assert (
        await client.put("/api/v1/platform/executors", json={"kind": "aca", "enabled": True})
    ).status_code == 403
    assert (await client.get("/api/v1/platform/gateway/keys")).status_code == 403
    assert (await client.get("/api/v1/platform/backends")).status_code == 200, "lire reste ouvert"


# ───────────── la timeline complète, le provisioning, l'API interne, la CLI ─────────────


async def test_la_timeline_raconte_runs_decisions_etats_et_findings(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import Event, Finding, HumanRequest
    from choregos_api.db.session import session_scope

    item = await _interne(client, project)
    run_id = await _run(project, item, result={"summary": "trois profils"}, cost_usd=0.4)
    async with session_scope(orgs="*") as session:
        session.add(
            HumanRequest(
                work_item_id=item["id"],
                project_id=project["id"],
                kind="approval",
                payload={"summary": "valider ?"},
                requested_at=utcnow(),
                decided_at=utcnow(),
                decided_by="marie@varga.dev",
                decision={"kind": "approve", "reason": "ok"},
            )
        )
        session.add(
            Event(
                project_id=project["id"],
                work_item_id=item["id"],
                type="choregos.workitem.state_changed",
                subject="BILLING-API-1",
                payload={"from": "demande", "to": "sourcing", "by": "orchestrateur"},
                ts=utcnow(),
            )
        )
        session.add(
            Finding(
                project_id=project["id"],
                origin_work_item_id=item["id"],
                origin_run_id=run_id,
                title="Base de profils manquante",
                type="docs",
                severity="medium",
                evidence="aucune base",
                status="pending",
            )
        )
    timeline = (await client.get(f"/api/v1/work-items/{item['id']}/timeline")).json()
    genres = [e["kind"] for e in timeline]
    assert {"run", "decision", "state_change", "finding"} <= set(genres)
    assert genres.count("decision") == 2, "la demande et la décision sont deux entrées"
    run_entry = next(e for e in timeline if e["kind"] == "run")
    assert run_entry["detail"] == "trois profils" and run_entry["cost_usd"] == 0.4
    assert next(e for e in timeline if e["kind"] == "state_change")["title"] == "demande → sourcing"


async def test_le_provisioning_demarre_un_workflow_et_expose_son_etat(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.temporal import get_temporal

    sec = await client.post(
        f"/api/v1/projects/{project['id']}/provision", json={"dry_run": True, "inputs": {"env": "dev"}}
    )
    assert sec.status_code == 202 and sec.json()["status"] == "running"
    assert not get_temporal().started, "un dry-run ne démarre rien"
    vrai = await client.post(f"/api/v1/projects/{project['id']}/provision", json={"inputs": {"region": "eu"}})
    assert vrai.status_code == 202
    ((workflow_id, payload),) = get_temporal().started.items()
    assert workflow_id == vrai.json()["workflow_id"] and payload["inputs"] == {
        "env": "dev",
        "region": "eu",
    }, "les entrées s'accumulent d'un appel à l'autre"
    etat = (await client.get(f"/api/v1/projects/{project['id']}/provision")).json()
    assert etat["status"] == "running" and etat["workflow_id"] == workflow_id
    assert (await client.get(f"/api/v1/projects/{project['id']}")).json()["status"] == "provisioning"


async def test_l_api_interne_sert_l_agent_avec_son_jeton_de_run(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.config import get_settings
    from choregos_api.security import mint_run_token
    from choregos_api.temporal import get_temporal

    item = await _interne(client, project)
    run_id = await _run(
        project,
        item,
        status="running",
        context_pack={
            "query": "avoirs",
            "paths": [],
            "kinds": [],
            "budget_tokens": 100,
            "tokens_estimated": 0,
            "truncated": False,
            "memories": [],
            "incidents": [],
            "related_items": [],
            "generated_at": "2026-09-25T00:00:00Z",
        },
    )
    jeton = mint_run_token(
        run_id,
        project_slug="billing-api",
        work_item_key="BILLING-API-1",
        ttl_minutes=5,
        settings=get_settings(),
    )
    entetes = {"Authorization": f"Bearer {jeton}"}
    client.cookies.clear()  # l'agent n'a pas de session : seul le jeton de run l'authentifie

    ticket = await client.get(f"/api/v1/internal/runs/{run_id}/ticket", headers=entetes)
    assert ticket.status_code == 200, ticket.text
    assert ticket.json()["key"] == "BILLING-API-1" and ticket.json()["body"] == "6 mois"
    assert (await client.get(f"/api/v1/internal/runs/{run_id}/context", headers=entetes)).json()[
        "query"
    ] == "avoirs"

    lot = {
        "events": [
            {"seq": 1, "type": "session/update", "payload": {"text": "bonjour"}},
            {"seq": 2, "type": "session/update", "payload": {}},
        ]
    }
    assert (
        await client.post(f"/api/v1/internal/runs/{run_id}/events", json=lot, headers=entetes)
    ).json() == {"accepted": 2}
    assert (
        await client.post(f"/api/v1/internal/runs/{run_id}/events", json=lot, headers=entetes)
    ).json() == {"accepted": 0}, "rejouer le lot n'écrit rien"

    question = await client.post(
        f"/api/v1/internal/runs/{run_id}/question",
        json={"text": "en euros ?", "options": ["oui", "non"]},
        headers=entetes,
    )
    assert question.status_code == 202 and question.json()["request_id"]
    assert any(nom == "question_asked" for _, nom, _ in get_temporal().signals)

    sans = await client.get(f"/api/v1/internal/runs/{run_id}/ticket")
    assert sans.status_code == 401
    autre = mint_run_token(
        "autre-run",
        project_slug="billing-api",
        work_item_key="BILLING-API-1",
        ttl_minutes=5,
        settings=get_settings(),
    )
    assert (
        await client.get(
            f"/api/v1/internal/runs/{run_id}/ticket", headers={"Authorization": f"Bearer {autre}"}
        )
    ).status_code in {403, 404}, "un jeton ne vaut que pour son run"


async def test_choregos_admin_frappe_un_jeton_et_rejoue_l_amorcage(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from choregos_api.config import get_settings
    from choregos_api.db import session as db_session
    from choregos_api.outils import app as cli
    from typer.testing import CliRunner

    runner = CliRunner()

    async def invoque(*args: str) -> Any:
        # La CLI fait `asyncio.run()` : dans un fil à part, avec un moteur neuf — celui du
        # test est lié à la boucle de pytest, et un moteur ne se partage pas entre boucles.
        db_session._engine = None
        db_session._sessionmaker = None
        return await asyncio.to_thread(runner.invoke, cli, list(args))

    frappe = await invoque(
        "tokens", "create", "--email", "Ops@Varga.dev", "--name", "ci", "--expires-in-days", "7"
    )
    assert frappe.exit_code == 0, frappe.output
    jeton = frappe.output.strip()
    assert jeton and len(jeton) > 20, "le clair est affiché une fois"
    monkeypatch.setattr(get_settings(), "bootstrap_org", "acme")
    monkeypatch.setattr(get_settings(), "bootstrap_org_name", "ACME")
    monkeypatch.setattr(get_settings(), "bootstrap_admins", "ops@varga.dev")
    amorce = await invoque("orgs", "bootstrap")
    assert amorce.exit_code == 0, amorce.output
    assert "acme" in amorce.output and "ops@varga.dev" in amorce.output
    encore = await invoque("orgs", "bootstrap")
    assert "aucune" in encore.output, "idempotent : la seconde fois ne crée rien"
    assert (await invoque()).exit_code != 0, "sans commande, l'aide"
    db_session._engine = None
    db_session._sessionmaker = None
