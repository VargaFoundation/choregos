"""Executor Azure Container Apps : un *job* ACA par run (§2.3, S13-05).

Même contrat que Tekton et les Jobs Kubernetes : un run = une exécution isolée, démarrée
de façon idempotente, interrogeable, annulable. Trois différences propres à ACA :

- la définition du job (`Microsoft.App/jobs`) est créée par un `PUT` **idempotent**, puis
  déclenchée par `start` — ce sont deux appels distincts ;
- le jeton du run ne part pas en variable d'environnement en clair : ACA a des *secrets*
  de job, référencés par `secretRef` ;
- les logs ne sont pas dans ARM mais dans Log Analytics. Sans espace de travail configuré,
  `logs()` dit où regarder plutôt que de rendre le silence.

Aucun credential cloud n'est détenu par le runner : c'est l'identité managée du job qui
tire l'image, et le jeton de run est scopé à ce run.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from choregos_contracts import ExecutorKind
from choregos_core.domain import ExecRef, ExecStatus, StageJobSpec

from ..errors import ConfigurationError, UpstreamError

ARM = "https://management.azure.com"
API_VERSION = "2024-03-01"
LOG_ANALYTICS = "https://api.loganalytics.io/v1"


class AzureArmClient:
    """Client ARM minimal : un jeton AAD mis en cache, et des appels REST."""

    def __init__(  # noqa: PLR0913 — la configuration ARM, tout en mots-clés avec défauts
        self,
        *,
        subscription_id: str = "",
        resource_group: str = "",
        tenant_id: str = "",
        client_id: str = "",
        client_secret: str = "",
        token: str = "",
        base_url: str = ARM,
        login_url: str = "https://login.microsoftonline.com",
        client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.subscription_id = subscription_id
        self.resource_group = resource_group
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url.rstrip("/")
        self.login_url = login_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._token = token
        self._expires_at = float("inf") if token else 0.0

    @property
    def scope(self) -> str:
        return f"/subscriptions/{self.subscription_id}/resourceGroups/{self.resource_group}"

    async def token(self) -> str:
        """Jeton client-credentials, renouvelé une minute avant l'expiration."""
        if self._token and time.time() < self._expires_at - 60:
            return self._token
        if not (self.tenant_id and self.client_id and self.client_secret):
            raise ConfigurationError(
                "identité Azure incomplète : `tenant_id`, `client_id` et `client_secret` "
                "(ou un `token` déjà obtenu) sont nécessaires"
            )
        response = await self._client.post(
            f"{self.login_url}/{self.tenant_id}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": f"{ARM}/.default",
            },
        )
        if response.status_code >= 400:
            raise UpstreamError("azure-aad", f"{response.status_code} : {response.text[:300]}")
        payload = response.json()
        self._token = str(payload["access_token"])
        self._expires_at = time.time() + float(payload.get("expires_in", 3600))
        return self._token

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Appel ARM. `404` rend `None` : c'est une absence, pas une panne."""
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        params = {"api-version": API_VERSION, **(kwargs.pop("params", None) or {})}
        headers = {
            "Authorization": f"Bearer {await self.token()}",
            "Content-Type": "application/json",
            **(kwargs.pop("headers", None) or {}),
        }
        response = await self._client.request(method, url, params=params, headers=headers, **kwargs)
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise UpstreamError(
                "azure-aca",
                f"{response.status_code} sur {method} {path} : {response.text[:300]}",
                status_code=response.status_code,
            )
        return response.json() if response.content else {}


class AcaExecutor:
    """Un job ACA par run, déclenché manuellement, nettoyé par le cycle de vie ACA."""

    kind = ExecutorKind.ACA
    capabilities: frozenset[str] = frozenset()

    def __init__(
        self,
        client: AzureArmClient,
        *,
        environment_id: str,
        location: str = "westeurope",
        identity_id: str | None = None,
        registry_server: str | None = None,
        log_analytics_workspace_id: str | None = None,
        cpu: float = 1.0,
        memory: str = "2Gi",
    ) -> None:
        if not environment_id:
            raise ConfigurationError("`environment_id` (Managed Environment ACA) est obligatoire")
        self.client = client
        self.environment_id = environment_id
        self.location = location
        self.identity_id = identity_id
        self.registry_server = registry_server
        self.log_analytics_workspace_id = log_analytics_workspace_id
        self.cpu = cpu
        self.memory = memory

    def _name(self, run_id: str) -> str:
        """ACA impose 32 caractères, minuscules, tirets."""
        return f"run-{run_id}".replace("_", "-").lower()[:32].rstrip("-")

    def _path(self, name: str) -> str:
        return f"{self.client.scope}/providers/Microsoft.App/jobs/{name}"

    # ───────────────────────── cycle de vie ─────────────────────────

    async def start(self, spec: StageJobSpec) -> ExecRef:
        """Crée le job s'il n'existe pas, puis le déclenche. Rejouable sans double exécution."""
        name = self._name(spec.run_id)
        existing = await self.client.request("GET", self._path(name))
        if existing is None:
            await self.client.request("PUT", self._path(name), json=self._definition(spec, name))
        # Un job par run : s'il a déjà une exécution, c'est celle de ce run. On ne
        # redéclenche pas — c'est ce qui rend `start` rejouable sans double facturation.
        if not await self._executions(name):
            await self.client.request("POST", f"{self._path(name)}/start")
        return ExecRef(
            kind=self.kind,
            name=name,
            namespace=self.client.resource_group,
            run_id=spec.run_id,
            url=f"{self.client.base_url}{self._path(name)}",
        )

    def _definition(self, spec: StageJobSpec, name: str) -> dict[str, Any]:
        container: dict[str, Any] = {
            "name": "runner",
            "image": spec.runner_image,
            "args": ["run"],
            "env": [
                {"name": "CHOREGOS_RUN_ID", "value": spec.run_id},
                {"name": "CHOREGOS_API_URL", "value": spec.api_url},
                # Le jeton passe par un secret de job : il n'apparaît pas dans la définition
                # rendue par ARM, donc pas dans un `az containerapp job show`.
                {"name": "CHOREGOS_RUN_TOKEN", "secretRef": "run-token"},
                *[{"name": key, "value": value} for key, value in spec.env.items()],
            ],
            "resources": self._resources(spec),
        }
        configuration: dict[str, Any] = {
            "triggerType": "Manual",
            "replicaTimeout": spec.timeout_minutes * 60,
            "replicaRetryLimit": 0,  # la reprise est décidée par l'orchestrateur
            "manualTriggerConfig": {"parallelism": 1, "replicaCompletionCount": 1},
            "secrets": [{"name": "run-token", "value": spec.run_token}],
        }
        if self.registry_server and self.identity_id:
            configuration["registries"] = [{"server": self.registry_server, "identity": self.identity_id}]
        definition: dict[str, Any] = {
            "location": self.location,
            "tags": _tags(
                {
                    "choregos/run-id": spec.run_id,
                    "choregos/project": spec.project_slug,
                    **spec.labels,
                }
            ),
            "properties": {
                "environmentId": self.environment_id,
                "configuration": configuration,
                "template": {"containers": [container]},
            },
        }
        if self.identity_id:
            definition["identity"] = {
                "type": "UserAssigned",
                "userAssignedIdentities": {self.identity_id: {}},
            }
        return definition

    def _resources(self, spec: StageJobSpec) -> dict[str, Any]:
        """Ressources de l'étape, avec les valeurs de l'exécuteur en repli.

        L'exécuteur k8s honore `spec.cpu`/`spec.memory` ; celui-ci les ignorait et imposait les
        siennes. Une étape qui demande 4 vCPU en obtenait 1 selon le backend, en silence — le même
        workflow n'avait donc pas le même comportement d'un exécuteur à l'autre.

        ACA veut un nombre pour le CPU et n'accepte que certaines combinaisons (mémoire = 2 × vCPU
        en Gi). Une valeur illisible retombe sur celle de l'exécuteur plutôt que de faire échouer le
        run ; une combinaison invalide est refusée par ARM, avec son message.
        """
        try:
            cpu = float(spec.cpu) if spec.cpu else self.cpu
        except ValueError:
            cpu = self.cpu
        return {"cpu": cpu, "memory": spec.memory or self.memory}

    async def _executions(self, name: str) -> list[dict[str, Any]]:
        payload = await self.client.request("GET", f"{self._path(name)}/executions")
        return list((payload or {}).get("value", []))

    async def status(self, ref: ExecRef) -> ExecStatus:
        """État de l'exécution, traduit depuis celui d'ACA.

        `ended_at` est **indicatif** : ARM rend un statut terminal sans `endTime` dans certains cas
        (observé sur un run tué par son `replicaTimeout`). L'orchestrateur horodate lui-même la fin
        d'un run, donc la durée rapportée ne dépend pas de ce champ — s'y fier ici donnerait des
        étapes de zéro seconde dans le commentaire du ticket, ce qui est pire qu'un champ vide.
        """
        executions = await self._executions(ref.name)
        if not executions:
            return ExecStatus(state="unknown", message="aucune exécution pour ce job")
        properties = executions[0].get("properties", {}) or {}
        raw = str(properties.get("status", "")).lower()
        state = {
            "succeeded": "succeeded",
            "failed": "failed",
            "running": "running",
            "processing": "running",
            "stopped": "cancelled",
            "degraded": "failed",
        }.get(raw, "pending")
        return ExecStatus(
            state=state,
            message=str(properties.get("detailedStatus", ""))[:500],
            started_at=_parse_time(properties.get("startTime")),
            ended_at=_parse_time(properties.get("endTime")),
        )

    async def cancel(self, ref: ExecRef) -> None:
        for execution in await self._executions(ref.name):
            name = execution.get("name")
            if name:
                await self.client.request("POST", f"{self._path(ref.name)}/executions/{name}/stop")

    async def logs(self, ref: ExecRef) -> AsyncIterator[str]:
        """Les logs d'ACA vivent dans Log Analytics, pas dans ARM."""
        if not self.log_analytics_workspace_id:
            yield (
                "logs indisponibles depuis ARM — configurez `log_analytics_workspace_id`, "
                f"ou interrogez ContainerAppConsoleLogs_CL où ContainerGroupName_s == '{ref.name}'"
            )
            return
        query = (
            "ContainerAppConsoleLogs_CL "
            f"| where ContainerGroupName_s startswith '{ref.name}' "
            "| project TimeGenerated, Log_s | order by TimeGenerated asc | take 1000"
        )
        payload = await self.client.request(
            "POST",
            f"{LOG_ANALYTICS}/workspaces/{self.log_analytics_workspace_id}/query",
            params={},
            json={"query": query},
        )
        for table in (payload or {}).get("tables", []):
            for row in table.get("rows", []):
                yield str(row[-1])


# Azure refuse `< > % & \\ ? /` dans un **nom** de tag — sauf pour les préfixes réservés
# `hidden` et `link`. Nos étiquettes sont écrites à la mode Kubernetes (`choregos/run-id`), qui est
# exactement la forme interdite : sans cette traduction, ARM répond 400 et **aucun job n'est jamais
# créé**. Les 18 tests contre transport simulé n'ont rien vu, parce qu'un transport simulé ne
# valide pas les noms de tags ; un abonnement réel l'a dit au premier appel (S13-05).
_TAG_RESERVED = "<>%&\\?/"


def _tag_name(name: str) -> str:
    """Nom de tag acceptable par ARM. `choregos/run-id` → `choregos_run-id`."""
    return "".join("_" if c in _TAG_RESERVED else c for c in name)


def _tags(tags: dict[str, str]) -> dict[str, str]:
    """Traduit les noms, garde les valeurs.

    Les valeurs, elles, acceptent tout : seul le *nom* est contraint. Une collision de noms après
    traduction (`a/b` et `a_b`) garde la dernière — assez improbable pour ne pas mériter un
    échec, et de toute façon visible dans le tag rendu.
    """
    return {_tag_name(key): value for key, value in tags.items()}


def _parse_time(value: Any) -> Any:
    from datetime import datetime

    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
