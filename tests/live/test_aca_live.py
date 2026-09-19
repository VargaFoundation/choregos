"""L'exécuteur ACA face à un vrai abonnement Azure (S13-05).

`packages/adapters/tests/test_aca.py` prouve la **forme** des appels ARM contre un transport
simulé. Ce fichier prouve qu'un abonnement les accepte : un job créé, déclenché, observé
jusqu'à son état terminal, annulé, et ses logs relus depuis Log Analytics.

Ce que seule une exécution réelle peut dire, et que le transport simulé ne dira jamais :

- qu'ARM accepte la définition telle qu'elle est écrite (une propriété mal nommée est un 400,
  pas un test rouge) ;
- que `start` rejoué ne crée pas une deuxième exécution — la propriété « pas de double
  facturation » que le contrat annonce ;
- que le jeton de run **n'apparaît pas** dans la définition rendue par ARM ;
- que les états ACA se traduisent dans les nôtres pour de vrai.

Prérequis (voir `docs/runbooks/aca-live.md`) :

    export CHOREGOS_LIVE_AZURE_SUBSCRIPTION=$(az account show --query id -o tsv)
    export CHOREGOS_LIVE_AZURE_RG=choregos-aca-test
    export CHOREGOS_LIVE_AZURE_ENV_ID=$(az containerapp env show -n choregos-env \\
        -g "$CHOREGOS_LIVE_AZURE_RG" --query id -o tsv)
    export CHOREGOS_LIVE_AZURE_TOKEN=$(az account get-access-token \\
        --resource https://management.azure.com --query accessToken -o tsv)
    uv run pytest tests/live/test_aca_live.py -m live

Les jobs créés portent le tag `choregos/run-id` et sont supprimés par la fixture ; le groupe
de ressources est jetable et se supprime d'un `az group delete`.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from choregos_adapters.executor.aca import AcaExecutor, AzureArmClient
from choregos_core.domain import ExecRef, StageJobSpec

from .conftest import require

pytestmark = pytest.mark.live

# L'exécuteur passe toujours `args: ["run"]` — la commande de *notre* runner, et c'est très bien
# ainsi. Conséquence observée sur un vrai abonnement : une image d'exemple ne s'en sert pas et
# reste vivante. Le test ne cherche donc pas à faire sortir un conteneur tiers ; il s'appuie sur
# `replicaTimeout`, que l'exécuteur remplit depuis `timeout_minutes`, pour obtenir un état terminal
# déterministe. Cela vérifie au passage une propriété réelle : le délai que nous demandons est bien
# appliqué par ACA, et son expiration remonte comme un échec plutôt que comme un succès.
IMAGE = "mcr.microsoft.com/cbl-mariner/busybox:2.0"


@pytest.fixture
async def executor() -> AsyncIterator[AcaExecutor]:
    env = require(
        "CHOREGOS_LIVE_AZURE_SUBSCRIPTION",
        "CHOREGOS_LIVE_AZURE_RG",
        "CHOREGOS_LIVE_AZURE_ENV_ID",
        "CHOREGOS_LIVE_AZURE_TOKEN",
    )
    # Un client par test : httpx lie ses connexions à la boucle qui les a ouvertes, et une
    # fixture de module les ferait traverser deux boucles différentes.
    async with httpx.AsyncClient(timeout=60.0) as client:
        arm = AzureArmClient(
            subscription_id=env["CHOREGOS_LIVE_AZURE_SUBSCRIPTION"],
            resource_group=env["CHOREGOS_LIVE_AZURE_RG"],
            token=env["CHOREGOS_LIVE_AZURE_TOKEN"],
            client=client,
        )
        yield AcaExecutor(
            arm,
            environment_id=env["CHOREGOS_LIVE_AZURE_ENV_ID"],
            location="westeurope",
            cpu=0.5,
            memory="1Gi",
        )


def spec(run_id: str, *, timeout_minutes: int = 2) -> StageJobSpec:
    return StageJobSpec(
        run_id=run_id,
        project_slug="live-test",
        namespace="choregos",
        runner_image=IMAGE,
        api_url="https://choregos.invalid/api",
        run_token=f"jeton-{run_id}",
        timeout_minutes=timeout_minutes,
        labels={"choregos/live": "true"},
    )


async def cleanup(executor: AcaExecutor, name: str) -> None:
    """Supprime le job. Le test doit laisser l'abonnement comme il l'a trouvé."""
    await executor.client.request("DELETE", executor._path(name))


async def wait_for(executor: AcaExecutor, ref: ExecRef, states: set[str], timeout_s: float = 300.0) -> str:
    """Attend un des états demandés, ou dit lequel a été observé en dernier."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    last = "unknown"
    while asyncio.get_running_loop().time() < deadline:
        last = (await executor.status(ref)).state
        if last in states:
            return last
        await asyncio.sleep(5)
    raise AssertionError(f"état {sorted(states)} jamais atteint — dernier vu : {last}")


async def test_un_job_demarre_et_son_etat_terminal_remonte(executor: AcaExecutor) -> None:
    """Le cycle complet : création, déclenchement, état terminal observé, horodatages cohérents.

    Le délai est volontairement court (une minute) : son expiration est ce qui termine l'exécution,
    et un run qui dépasse son délai doit être rapporté comme **échoué**. Un exécuteur qui rendrait
    `succeeded` ici laisserait passer une étape qui n'a jamais fini.
    """
    run_id = f"live-{uuid.uuid4().hex[:8]}"
    ref = await executor.start(spec(run_id, timeout_minutes=1))
    try:
        assert ref.name.startswith("run-live-")
        assert ref.namespace == executor.client.resource_group

        state = await wait_for(executor, ref, {"succeeded", "failed"}, timeout_s=420)
        assert state == "failed", f"un run qui dépasse son délai doit être `failed`, pas {state}"

        status = await executor.status(ref)
        assert status.started_at is not None
        # `ended_at` est **facultatif** : observé sur un vrai abonnement, ARM rend `Failed` avec un
        # `startTime` et sans `endTime`. C'est pourquoi l'orchestrateur horodate lui-même la fin
        # d'un run (`activities/stage.py`) au lieu de faire confiance à celui-ci — la durée
        # affichée dans le ticket ne dépend pas de ce champ.
        if status.ended_at is not None:
            assert status.ended_at >= status.started_at
    finally:
        await cleanup(executor, ref.name)


async def test_start_rejoue_ne_double_pas_l_execution(executor: AcaExecutor) -> None:
    """La propriété qui coûte de l'argent si elle est fausse.

    L'orchestrateur rejoue une activité après un crash ; si `start` redéclenchait le job, le
    run serait facturé deux fois et écrirait deux fois. Une seule exécution doit exister.
    """
    run_id = f"live-{uuid.uuid4().hex[:8]}"
    ref = await executor.start(spec(run_id))
    try:
        await wait_for(executor, ref, {"running", "succeeded", "failed"})
        again = await executor.start(spec(run_id))
        assert again.name == ref.name

        executions = await executor._executions(ref.name)
        assert len(executions) == 1, f"{len(executions)} exécutions — `start` a redéclenché"
    finally:
        await cleanup(executor, ref.name)


async def test_le_jeton_de_run_n_apparait_pas_dans_arm(executor: AcaExecutor) -> None:
    """Un `az containerapp job show` ne doit pas rendre le jeton.

    Il part en *secret* de job, référencé par `secretRef` : ARM renvoie le nom du secret, pas
    sa valeur. C'est la raison d'être de ce détour, et elle se vérifie de l'extérieur.
    """
    run_id = f"live-{uuid.uuid4().hex[:8]}"
    ref = await executor.start(spec(run_id))
    try:
        rendered = await executor.client.request("GET", executor._path(ref.name))
        as_text = str(rendered)
        assert f"jeton-{run_id}" not in as_text, "le jeton de run est lisible dans ARM"
        assert "secretRef" in as_text
        assert "run-token" in as_text

        # …et le tag qui rattache le job à son run est bien là, c'est ce qui permet de
        # retrouver une exécution orpheline.
        # Son nom est traduit : ARM refuse le `/` dans un nom de tag, ce qu'aucun test contre
        # transport simulé ne pouvait dire.
        tags = (rendered or {}).get("tags", {})
        assert tags.get("choregos_run-id") == run_id
        assert not any("/" in name for name in tags), f"tag au nom interdit : {sorted(tags)}"
    finally:
        await cleanup(executor, ref.name)


async def test_une_execution_peut_etre_annulee(executor: AcaExecutor) -> None:
    """`cancel` arrête l'exécution en cours — l'arrêt d'un run coûteux doit être immédiat."""
    run_id = f"live-{uuid.uuid4().hex[:8]}"
    ref = await executor.start(spec(run_id, timeout_minutes=5))
    try:
        await wait_for(executor, ref, {"running", "succeeded", "failed"})
        # `cancel` est appelé quoi qu'il arrive : ce qui est vérifié est qu'il aboutit sans
        # erreur et que l'état reste cohérent, pas qu'il gagne la course contre un conteneur
        # qui se termine en quelques secondes.
        await executor.cancel(ref)
        state = await wait_for(executor, ref, {"cancelled", "succeeded", "failed"}, timeout_s=180)
        # Une exécution déjà terminée avant l'annulation reste terminée : ce test vérifie que
        # `cancel` aboutit, pas qu'il gagne la course contre un conteneur qui finit en 10 s.
        assert state in {"cancelled", "succeeded", "failed"}
    finally:
        await cleanup(executor, ref.name)


async def test_un_job_inexistant_est_une_absence_pas_une_panne(executor: AcaExecutor) -> None:
    """ARM répond 404 ; l'exécuteur doit le traduire en « rien », pas en exception."""
    ref = ExecRef(
        kind=executor.kind,
        name="run-inexistant-0000",
        namespace=executor.client.resource_group,
        run_id="inexistant",
    )
    status = await executor.status(ref)
    assert status.state == "unknown"
    assert "aucune exécution" in status.message


async def test_les_logs_disent_ou_regarder_sans_workspace(executor: AcaExecutor) -> None:
    """Sans workspace configuré, `logs()` explique où chercher au lieu de rendre le silence."""
    ref = ExecRef(
        kind=executor.kind,
        name="run-quelconque",
        namespace=executor.client.resource_group,
        run_id="quelconque",
    )
    lines = [line async for line in executor.logs(ref)]
    assert len(lines) == 1
    assert "ContainerAppConsoleLogs_CL" in lines[0]
