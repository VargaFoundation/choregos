"""Tekton, exécuteur et CI : ce que l'API Kubernetes reçoit, sans cluster."""

from __future__ import annotations

import base64

import pytest
from choregos_adapters.ci.tekton import TektonCi
from choregos_adapters.errors import UpstreamError
from choregos_adapters.executor.tekton import CORE_API, TEKTON_API, KubernetesClient, TektonExecutor
from choregos_contracts import InboundEventType
from choregos_core.domain import ExecRef, StageJobSpec

from ._transport import Fil

K8S = "https://k8s.test"
NS = "proj-billing-runners"


def k8s(fil: Fil) -> KubernetesClient:
    return KubernetesClient(K8S, token="sa-token", client=fil.client())


def spec(**kw: object) -> StageJobSpec:
    base: dict[str, object] = {
        "run_id": "wi-1-t-implement-1",
        "project_slug": "billing",
        "namespace": NS,
        "runner_image": "ghcr.io/vargafoundation/choregos-runner:0.3.0",
        "api_url": "http://choregos-api:8000",
        "run_token": "jeton-de-run",
        "timeout_minutes": 40,
        "labels": {"choregos/work-item": "billing-12"},
    }
    base.update(kw)
    return StageJobSpec(**base)  # type: ignore[arg-type]


async def test_le_client_kubernetes_traduit_404_en_absence_et_le_reste_en_erreur() -> None:
    fil = Fil({("GET", "/x"): (403, "forbidden: no RBAC")})
    client = k8s(fil)
    assert await client.request("GET", "/absent") is None
    with pytest.raises(UpstreamError, match="403") as exc:
        await client.request("GET", "/x")
    assert exc.value.service == "kubernetes"
    assert fil.requetes[0].headers["Authorization"] == "Bearer sa-token"


async def test_un_patch_annonce_son_content_type_sans_perdre_le_jeton() -> None:
    fil = Fil({("PATCH", "/x"): (200, {})})
    await k8s(fil).request("PATCH", "/x", json={}, headers={"Content-Type": "application/merge-patch+json"})
    entetes = fil.requetes[0].headers
    assert (
        entetes["Content-Type"] == "application/merge-patch+json"
        and entetes["Authorization"] == "Bearer sa-token"
    )


async def test_demarrer_cree_le_secret_du_run_puis_le_pipelinerun() -> None:
    fil = Fil(
        {
            ("POST", f"{CORE_API}/namespaces/{NS}/secrets"): (201, {}),
            ("POST", f"{TEKTON_API}/namespaces/{NS}/pipelineruns"): (201, {}),
        }
    )
    ref = await TektonExecutor(k8s(fil), pipeline="choregos-agent").start(spec(runtime_class="gvisor"))
    assert ref.name == "run-wi-1-t-implement-1" and ref.namespace == NS
    assert fil.envoyees("POST") == [
        ("POST", f"{CORE_API}/namespaces/{NS}/secrets"),
        ("POST", f"{TEKTON_API}/namespaces/{NS}/pipelineruns"),
    ]
    secret = fil.corps(-2)
    assert base64.b64decode(secret["data"]["token"]).decode() == "jeton-de-run"
    assert secret["metadata"]["labels"]["choregos/run-id"] == "wi-1-t-implement-1"
    pr = fil.corps(-1)
    params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
    assert params["repo-url"] == "" and params["revision"] == "main", "sans dépôt : le clonage saute"
    assert params["runner-image"].endswith(":0.3.0")
    assert pr["spec"]["timeouts"] == {"pipeline": "40m"}
    modele = pr["spec"]["taskRunTemplate"]["podTemplate"]
    assert modele["runtimeClassName"] == "gvisor" and modele["securityContext"]["runAsNonRoot"] is True
    assert pr["metadata"]["labels"]["choregos/work-item"] == "billing-12"


async def test_demarrer_est_idempotent_par_nom_de_run() -> None:
    fil = Fil(
        {
            ("GET", f"{TEKTON_API}/namespaces/{NS}/pipelineruns/run-wi-1-t-implement-1"): (
                200,
                {"metadata": {}},
            )
        }
    )
    ref = await TektonExecutor(k8s(fil)).start(spec())
    assert ref.run_id == "wi-1-t-implement-1" and fil.envoyees("POST") == []


async def test_l_etat_lit_la_condition_et_les_resultats() -> None:
    chemin = f"{TEKTON_API}/namespaces/{NS}/pipelineruns/run-1"
    ok = {
        "status": {
            "conditions": [{"status": "True"}],
            "results": [{"name": "result-url", "value": "http://r"}],
            "startTime": "2026-09-25T07:00:00Z",
            "completionTime": "2026-09-25T07:10:00Z",
        }
    }
    annule = {"status": {"conditions": [{"status": "False", "reason": "Cancelled", "message": "stop"}]}}
    for charge, attendu in ((ok, "succeeded"), (annule, "cancelled"), ({"status": {}}, "running")):
        fil = Fil({("GET", chemin): (200, charge)})
        etat = await TektonExecutor(k8s(fil)).status(ExecRef(kind="tekton", name="run-1", namespace=NS))
        assert etat.state == attendu, charge
    assert (
        await TektonExecutor(k8s(Fil({("GET", chemin): (200, ok)}))).status(
            ExecRef(kind="tekton", name="run-1", namespace=NS)
        )
    ).result_url == "http://r"
    absent = await TektonExecutor(k8s(Fil())).status(ExecRef(kind="tekton", name="run-1", namespace=NS))
    assert absent.state == "unknown" and "introuvable" in absent.message


async def test_annuler_arrete_le_pipelinerun_puis_supprime_le_secret() -> None:
    fil = Fil(
        {
            ("PATCH", f"{TEKTON_API}/namespaces/{NS}/pipelineruns/run-1"): (200, {}),
            ("DELETE", f"{CORE_API}/namespaces/{NS}/secrets/run-1"): (200, {}),
        }
    )
    await TektonExecutor(k8s(fil)).cancel(ExecRef(kind="tekton", name="run-1", namespace=NS))
    assert fil.envoyees() == [
        ("PATCH", f"{TEKTON_API}/namespaces/{NS}/pipelineruns/run-1"),
        ("DELETE", f"{CORE_API}/namespaces/{NS}/secrets/run-1"),
    ]
    assert fil.corps(0) == {"spec": {"status": "Cancelled"}}


async def test_les_journaux_viennent_du_conteneur_step_run_de_chaque_pod() -> None:
    fil = Fil(
        {
            ("GET", f"{CORE_API}/namespaces/{NS}/pods"): (200, {"items": [{"metadata": {"name": "p1"}}]}),
            ("GET", f"{CORE_API}/namespaces/{NS}/pods/p1/log"): (200, "ligne 1\nligne 2"),
        }
    )
    lignes = [
        ligne
        async for ligne in TektonExecutor(k8s(fil)).logs(ExecRef(kind="tekton", name="run-1", namespace=NS))
    ]
    assert lignes == ["ligne 1", "ligne 2"]
    assert fil.requetes[0].url.params["labelSelector"] == "tekton.dev/pipelineRun=run-1"
    assert fil.requetes[1].url.params["container"] == "step-run"


async def test_la_ci_agrege_les_pipelineruns_d_un_sha() -> None:
    runs = {
        "items": [
            {"metadata": {"name": "ci-1"}, "status": {"conditions": [{"status": "True"}]}},
            {"metadata": {"name": "ci-2"}, "status": {"conditions": [{"status": "Unknown"}]}},
        ]
    }
    fil = Fil({("GET", f"{TEKTON_API}/namespaces/ci/pipelineruns"): (200, runs)})
    statut = await TektonCi(k8s(fil), namespace="ci").status_for("varga/billing", "abc")
    assert statut.state == "running" and [c.status for c in statut.runs] == ["completed", "in_progress"]
    assert fil.requetes[0].url.params["labelSelector"] == "choregos/sha=abc"
    vide = await TektonCi(
        k8s(Fil({("GET", f"{TEKTON_API}/namespaces/ci/pipelineruns"): (200, {"items": []})})), namespace="ci"
    ).status_for("r", "abc")
    assert vide.state == "unknown"
    echec = {"items": [{"metadata": {"name": "ci-1"}, "status": {"conditions": [{"status": "False"}]}}]}
    assert (
        await TektonCi(
            k8s(Fil({("GET", f"{TEKTON_API}/namespaces/ci/pipelineruns"): (200, echec)})), namespace="ci"
        ).status_for("r", "abc")
    ).state == "failure"


async def test_declencher_un_pipeline_passe_depot_et_revision() -> None:
    fil = Fil(
        {
            ("POST", f"{TEKTON_API}/namespaces/ci/pipelineruns"): (
                201,
                {"metadata": {"name": "choregos-ci-x7"}},
            )
        }
    )
    nom = await TektonCi(k8s(fil), namespace="ci").trigger("varga/billing", "abc", "")
    assert nom == "choregos-ci-x7"
    corps = fil.corps(-1)
    assert corps["metadata"]["generateName"] == "choregos-ci-"
    assert {p["name"]: p["value"] for p in corps["spec"]["params"]} == {
        "repo-url": "https://github.com/varga/billing.git",
        "revision": "abc",
    }


def test_un_cloudevent_tekton_devient_un_evenement_ci() -> None:
    corps = {
        "pipelineRun": {
            "metadata": {
                "name": "run-1",
                "labels": {
                    "choregos/project": "billing",
                    "choregos/work-item": "billing-12",
                    "choregos/sha": "abc",
                    "choregos/run-id": "r-1",
                },
            }
        }
    }
    import json

    (event,) = TektonCi(k8s(Fil())).parse_event(
        {"ce-type": "dev.tekton.event.pipelinerun.failed.v1", "ce-id": "ev-1"}, json.dumps(corps).encode()
    )
    assert event.type == InboundEventType.CI_FAILED and event.delivery_id == "ev-1"
    assert event.project_slug == "billing" and event.work_item_key == "billing-12"
    assert event.payload == {"pipeline_run": "run-1", "sha": "abc", "run_id": "r-1"}
