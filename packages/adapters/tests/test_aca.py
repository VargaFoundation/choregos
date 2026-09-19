"""Executor Azure Container Apps : idempotence, secret de run, statut, annulation, logs.

Comme pour Jira et GitLab, les tests parlent au protocole ARM documenté via un transport
simulé : ils prouvent la forme des appels, pas qu'un abonnement Azure les accepte.
"""

from __future__ import annotations

import json as jsonlib
from typing import Any

import httpx
import pytest
from choregos_adapters.errors import ConfigurationError, UpstreamError
from choregos_adapters.executor.aca import AcaExecutor, AzureArmClient
from choregos_contracts import ExecutorKind
from choregos_core.domain import ExecRef, StageJobSpec

SUB = "00000000-0000-0000-0000-000000000000"
RG = "rg-choregos"
ENV = f"/subscriptions/{SUB}/resourceGroups/{RG}/providers/Microsoft.App/managedEnvironments/env"
JOBS = f"/subscriptions/{SUB}/resourceGroups/{RG}/providers/Microsoft.App/jobs"


def executor(handler: Any, **kwargs: Any) -> AcaExecutor:
    client = AzureArmClient(
        subscription_id=SUB,
        resource_group=RG,
        token="jeton-arm",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return AcaExecutor(client, environment_id=ENV, **kwargs)


def spec(run_id: str = "run-42") -> StageJobSpec:
    return StageJobSpec(
        run_id=run_id,
        project_slug="billing-api",
        namespace=RG,
        runner_image="ghcr.io/vargafoundation/choregos-runner@sha256:abc",
        api_url="https://api.choregos.dev/api/v1/internal",
        run_token="jeton-du-run",
        timeout_minutes=90,
        env={"CHOREGOS_BACKEND": "openhands"},
        labels={"choregos/env": "prod"},
    )


def test_un_environnement_absent_est_refuse() -> None:
    with pytest.raises(ConfigurationError):
        AcaExecutor(AzureArmClient(token="x"), environment_id="")


async def test_le_job_est_cree_puis_declenche() -> None:
    appels: list[tuple[str, str]] = []
    definition: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        appels.append((request.method, request.url.path))
        assert request.headers["authorization"] == "Bearer jeton-arm"
        assert request.url.params["api-version"] == "2024-03-01"
        if request.method == "GET" and request.url.path.endswith("/executions"):
            return httpx.Response(200, json={"value": []})
        if request.method == "GET":
            return httpx.Response(404, json={"error": "NotFound"})
        if request.method == "PUT":
            definition.update(jsonlib.loads(request.content))
            return httpx.Response(201, json=definition)
        return httpx.Response(200, json={"name": "run-42-xyz"})

    ref = await executor(handler).start(spec())

    assert ref.kind is ExecutorKind.ACA
    assert ref.name == "run-run-42"
    assert ("PUT", f"{JOBS}/run-run-42") in appels
    assert ("POST", f"{JOBS}/run-run-42/start") in appels

    properties = definition["properties"]
    assert properties["environmentId"] == ENV
    assert properties["configuration"]["triggerType"] == "Manual"
    assert properties["configuration"]["replicaTimeout"] == 90 * 60
    assert properties["configuration"]["replicaRetryLimit"] == 0, "la reprise est à l'orchestrateur"


async def test_le_jeton_du_run_passe_par_un_secret() -> None:
    definition: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/executions"):
            return httpx.Response(200, json={"value": []})
        if request.method == "GET":
            return httpx.Response(404, json={})
        if request.method == "PUT":
            definition.update(jsonlib.loads(request.content))
        return httpx.Response(200, json={})

    await executor(handler).start(spec())

    container = definition["properties"]["template"]["containers"][0]
    token_env = next(e for e in container["env"] if e["name"] == "CHOREGOS_RUN_TOKEN")
    assert token_env == {"name": "CHOREGOS_RUN_TOKEN", "secretRef": "run-token"}
    assert not any("value" in e and e["value"] == "jeton-du-run" for e in container["env"])
    secrets = definition["properties"]["configuration"]["secrets"]
    assert secrets == [{"name": "run-token", "value": "jeton-du-run"}]


async def test_redemarrer_un_run_ne_le_joue_pas_deux_fois() -> None:
    """Rejouer l'activité `start_run` ne doit pas déclencher une seconde exécution."""
    appels: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        appels.append((request.method, request.url.path))
        if request.url.path.endswith("/executions"):
            return httpx.Response(200, json={"value": [{"name": "run-42-xyz", "properties": {}}]})
        return httpx.Response(200, json={"name": "run-run-42"})

    await executor(handler).start(spec())

    assert not any(path.endswith("/start") for _, path in appels), "aucun second déclenchement"


async def test_l_identite_et_le_registre_ne_sont_poses_que_si_configures() -> None:
    definitions: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/executions"):
            return httpx.Response(200, json={"value": []})
        if request.method == "GET":
            return httpx.Response(404, json={})
        if request.method == "PUT":
            definitions.append(jsonlib.loads(request.content))
        return httpx.Response(200, json={})

    await executor(handler).start(spec("run-1"))
    identity = "/subscriptions/x/resourcegroups/rg/providers/Microsoft.ManagedIdentity/u/id"
    await executor(handler, identity_id=identity, registry_server="acme.azurecr.io").start(spec("run-2"))

    assert "identity" not in definitions[0]
    assert definitions[1]["identity"]["type"] == "UserAssigned"
    assert definitions[1]["properties"]["configuration"]["registries"][0]["identity"] == identity


@pytest.mark.parametrize(
    ("azure", "attendu"),
    [
        ("Succeeded", "succeeded"),
        ("Failed", "failed"),
        ("Running", "running"),
        ("Processing", "running"),
        ("Stopped", "cancelled"),
        ("Inconnu", "pending"),
    ],
)
async def test_traduction_des_statuts(azure: str, attendu: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "name": "run-42-xyz",
                        "properties": {
                            "status": azure,
                            "startTime": "2026-09-19T08:00:00Z",
                            "endTime": "2026-09-19T08:12:00Z",
                        },
                    }
                ]
            },
        )

    ref = ExecRef(kind=ExecutorKind.ACA, name="run-run-42", namespace=RG, run_id="run-42")
    status = await executor(handler).status(ref)

    assert status.state == attendu
    assert status.started_at is not None and status.ended_at is not None


async def test_un_job_sans_execution_est_inconnu_pas_en_echec() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": []})

    ref = ExecRef(kind=ExecutorKind.ACA, name="run-run-42", namespace=RG, run_id="run-42")
    status = await executor(handler).status(ref)
    assert status.state == "unknown"


async def test_l_annulation_arrete_l_execution_en_cours() -> None:
    appels: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        appels.append((request.method, request.url.path))
        if request.url.path.endswith("/executions"):
            return httpx.Response(200, json={"value": [{"name": "run-42-xyz", "properties": {}}]})
        return httpx.Response(200, json={})

    ref = ExecRef(kind=ExecutorKind.ACA, name="run-run-42", namespace=RG, run_id="run-42")
    await executor(handler).cancel(ref)

    assert ("POST", f"{JOBS}/run-run-42/executions/run-42-xyz/stop") in appels


async def test_sans_log_analytics_les_logs_disent_ou_regarder() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    ref = ExecRef(kind=ExecutorKind.ACA, name="run-run-42", namespace=RG, run_id="run-42")
    lignes = [line async for line in executor(handler).logs(ref)]

    assert len(lignes) == 1
    assert "ContainerAppConsoleLogs_CL" in lignes[0], "la piste est donnée, pas le silence"


async def test_avec_log_analytics_les_lignes_arrivent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "api.loganalytics.io" in str(request.url)
        assert "ContainerAppConsoleLogs_CL" in jsonlib.loads(request.content)["query"]
        return httpx.Response(
            200,
            json={
                "tables": [{"rows": [["2026-09-19T08:00:00Z", "runner: démarrage"], ["…", "runner: fini"]]}]
            },
        )

    ref = ExecRef(kind=ExecutorKind.ACA, name="run-run-42", namespace=RG, run_id="run-42")
    lignes = [line async for line in executor(handler, log_analytics_workspace_id="ws-1").logs(ref)]

    assert lignes == ["runner: démarrage", "runner: fini"]


async def test_une_erreur_arm_est_nommee() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="AuthorizationFailed")

    ref = ExecRef(kind=ExecutorKind.ACA, name="run-run-42", namespace=RG, run_id="run-42")
    with pytest.raises(UpstreamError) as error:
        await executor(handler).status(ref)
    assert "azure-aca" in str(error.value)
    assert error.value.status_code == 403


async def test_le_jeton_aad_est_demande_puis_mis_en_cache() -> None:
    appels: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        appels.append(str(request.url))
        if "oauth2" in str(request.url):
            return httpx.Response(200, json={"access_token": "jeton-frais", "expires_in": 3600})
        return httpx.Response(200, json={"value": []})

    client = AzureArmClient(
        subscription_id=SUB,
        resource_group=RG,
        tenant_id="t",
        client_id="c",
        client_secret="s",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    aca = AcaExecutor(client, environment_id=ENV)
    ref = ExecRef(kind=ExecutorKind.ACA, name="run-run-42", namespace=RG, run_id="run-42")
    await aca.status(ref)
    await aca.status(ref)

    assert sum(1 for url in appels if "oauth2" in url) == 1, "le jeton n'est demandé qu'une fois"


async def test_une_identite_incomplete_est_refusee_clairement() -> None:
    client = AzureArmClient(subscription_id=SUB, resource_group=RG)
    with pytest.raises(ConfigurationError) as error:
        await client.token()
    assert "tenant_id" in str(error.value)


async def test_aucun_nom_de_tag_ne_porte_de_caractere_refuse_par_arm() -> None:
    """La régression qui a coûté S13-05 : ARM refuse `< > % & \\ ? /` dans un *nom* de tag.

    Nos étiquettes sont écrites à la mode Kubernetes (`choregos/run-id`) — exactement la forme
    interdite. Le transport simulé ne valide pas les noms, donc les dix-huit tests d'à côté
    passaient pendant qu'aucun job n'aurait pu être créé. Ce test refait la validation ici.
    """
    envoye: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/executions"):
            return httpx.Response(200, json={"value": []})
        if request.method == "GET":
            return httpx.Response(404)
        if request.method == "PUT":
            envoye.update(jsonlib.loads(request.content))
        return httpx.Response(200, json={})

    aca = executor(handler)
    job = spec()
    # Des étiquettes d'appelant dans le style k8s : elles aussi doivent être traduites.
    job.labels = {
        "app.kubernetes.io/name": "choregos",
        "choregos/stage": "implement",
        "sans-souci": "ok",
    }
    await aca.start(job)

    interdits = set("<>%&\\?/")
    for nom in envoye.get("tags", {}):
        assert not (set(nom) & interdits), f"nom de tag refusé par ARM : {nom!r}"

    tags = envoye["tags"]
    # La traduction garde le sens : un `/` devient `_`, le reste est intact.
    assert tags["choregos_run-id"] == job.run_id
    assert tags["choregos_project"] == job.project_slug
    assert tags["app.kubernetes.io_name"] == "choregos"
    assert tags["choregos_stage"] == "implement"
    assert tags["sans-souci"] == "ok"


async def test_les_ressources_de_l_etape_sont_honorees() -> None:
    """Le même workflow doit demander les mêmes ressources quel que soit l'exécuteur.

    `k8s_job` honore `spec.cpu`/`spec.memory` ; ACA les ignorait et imposait les siennes, donc une
    étape qui demandait 2 vCPU en obtenait 1 selon le backend, sans rien dire.
    """
    envoye: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/executions"):
            return httpx.Response(200, json={"value": []})
        if request.method == "GET":
            return httpx.Response(404)
        if request.method == "PUT":
            envoye.update(jsonlib.loads(request.content))
        return httpx.Response(200, json={})

    aca = executor(handler, cpu=0.5, memory="1Gi")
    job = spec()
    job.cpu = "2"
    job.memory = "4Gi"
    await aca.start(job)
    resources = envoye["properties"]["template"]["containers"][0]["resources"]
    assert resources == {"cpu": 2.0, "memory": "4Gi"}

    # Une valeur illisible retombe sur celle de l'exécuteur : un run ne doit pas échouer pour ça.
    envoye.clear()
    aca2 = executor(handler, cpu=0.5, memory="1Gi")
    bancal = spec("run-43")
    bancal.cpu = "beaucoup"
    await aca2.start(bancal)
    assert envoye["properties"]["template"]["containers"][0]["resources"]["cpu"] == 0.5
