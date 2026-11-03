# SPDX-License-Identifier: Apache-2.0
"""The Azure Container Apps executor: one ACA *job* per run (§2.3, S13-05).

The same contract as Tekton and the Kubernetes Jobs: a run = an isolated execution, started
idempotently, queryable, cancellable. Three differences specific to ACA:

- the job definition (`Microsoft.App/jobs`) is created by an **idempotent** `PUT`, then triggered
  by `start`: these are two distinct calls;
- the run's token does not go out as a plain environment variable: ACA has job *secrets*,
  referenced by `secretRef`;
- the logs are not in ARM but in Log Analytics. Without a configured workspace, `logs()` says
  where to look rather than return silence.

No cloud credential is held by the runner: it is the job's managed identity that pulls the
image, and the run token is scoped to that run.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from choregos_contracts import ExecutorKind
from choregos_core.domain import RUN_GATEWAY_KEY_ENV, RUN_REMOTE_TOKEN_ENV, ExecRef, ExecStatus, StageJobSpec

from ..errors import ConfigurationError, UpstreamError

ARM = "https://management.azure.com"
API_VERSION = "2024-03-01"
LOG_ANALYTICS = "https://api.loganalytics.io/v1"


class AzureArmClient:
    """A minimal ARM client: a cached AAD token, and REST calls."""

    def __init__(  # noqa: PLR0913 - the ARM configuration, all keywords with defaults
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
        """A client-credentials token, renewed one minute before it expires."""
        if self._token and time.time() < self._expires_at - 60:
            return self._token
        if not (self.tenant_id and self.client_id and self.client_secret):
            raise ConfigurationError(
                "incomplete Azure identity: `tenant_id`, `client_id` and `client_secret` "
                "(or an already obtained `token`) are required"
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
        """An ARM call. `404` returns `None`: it is an absence, not a failure."""
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
    """One ACA job per run, triggered manually, cleaned up by the ACA lifecycle."""

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
        """ACA imposes 32 characters, lower case, hyphens."""
        return f"run-{run_id}".replace("_", "-").lower()[:32].rstrip("-")

    def _path(self, name: str) -> str:
        return f"{self.client.scope}/providers/Microsoft.App/jobs/{name}"

    # ───────────────────────── cycle de vie ─────────────────────────

    async def start(self, spec: StageJobSpec) -> ExecRef:
        """Creates the job if it does not exist, then triggers it. Replayable without a double execution."""
        if spec.agent_container is not None:
            raise ConfigurationError(
                "a hosted agent's container runs beside the runner in one pod (ADR 0080), which this "
                "executor's Container Apps job does not declare; use a Kubernetes executor for hosted "
                "agents"
            )
        name = self._name(spec.run_id)
        existing = await self.client.request("GET", self._path(name))
        if existing is None:
            await self.client.request("PUT", self._path(name), json=self._definition(spec, name))
        # One job per run: if it already has an execution, it is this run's. It is not triggered
        # again, which is what makes `start` replayable without double billing.
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
                # The token goes through a job secret: it does not appear in the definition
                # rendered by ARM, so not in an `az containerapp job show` either.
                {"name": "CHOREGOS_RUN_TOKEN", "secretRef": "run-token"},
                # The run's gateway key, a job secret too (#523): never in the rendered definition.
                *(
                    [{"name": RUN_GATEWAY_KEY_ENV, "secretRef": "run-gateway-key"}]
                    if spec.gateway_key
                    else []
                ),
                # The access token of a remote agent (ADR 0062), a job secret like the key above.
                *(
                    [{"name": RUN_REMOTE_TOKEN_ENV, "secretRef": "run-remote-token"}]
                    if spec.remote_token
                    else []
                ),
                *[{"name": key, "value": value} for key, value in spec.env.items()],
            ],
            "resources": self._resources(spec),
        }
        configuration: dict[str, Any] = {
            "triggerType": "Manual",
            "replicaTimeout": spec.timeout_minutes * 60,
            "replicaRetryLimit": 0,  # the retry is decided by the orchestrator
            "manualTriggerConfig": {"parallelism": 1, "replicaCompletionCount": 1},
            "secrets": [
                {"name": "run-token", "value": spec.run_token},
                *([{"name": "run-gateway-key", "value": spec.gateway_key}] if spec.gateway_key else []),
                *([{"name": "run-remote-token", "value": spec.remote_token}] if spec.remote_token else []),
            ],
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
        """The step's resources, with the executor's values as a fallback.

        The k8s executor honours `spec.cpu`/`spec.memory`; this one ignored them and imposed its
        own. A step that asked for 4 vCPU got 1 depending on the backend, silently: the same
        workflow therefore did not behave the same from one executor to another.

        ACA wants a number for the CPU and only accepts certain combinations (memory = 2 x vCPU in
        Gi). An unreadable value falls back to the executor's rather than fail the run; an invalid
        combination is refused by ARM, with its message.
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
        """The execution's state, translated from ACA's.

        `ended_at` is **indicative**: ARM returns a terminal status without `endTime` in some cases
        (observed on a run killed by its `replicaTimeout`). The orchestrator timestamps the end of a
        run itself, so the reported duration does not depend on this field: relying on it here would
        give zero-second steps in the ticket comment, which is worse than an empty field.
        """
        executions = await self._executions(ref.name)
        if not executions:
            return ExecStatus(state="unknown", message="no execution for this job")
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
        """ACA's logs live in Log Analytics, not in ARM."""
        if not self.log_analytics_workspace_id:
            yield (
                "logs unavailable from ARM: configure `log_analytics_workspace_id`, "
                f"or query ContainerAppConsoleLogs_CL where ContainerGroupName_s == '{ref.name}'"
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


# Azure refuses `< > % & \\ ? /` in a tag **name**, except for the reserved prefixes `hidden` and
# `link`. Our labels are written Kubernetes-style (`choregos/run-id`), which is exactly the
# forbidden shape: without this translation, ARM answers 400 and **no job is ever created**. The 18
# tests against a simulated transport saw nothing, because a simulated transport does not validate
# tag names; a real subscription said so on the first call (S13-05).
_TAG_RESERVED = "<>%&\\?/"


def _tag_name(name: str) -> str:
    """A tag name ARM accepts. `choregos/run-id` -> `choregos_run-id`."""
    return "".join("_" if c in _TAG_RESERVED else c for c in name)


def _tags(tags: dict[str, str]) -> dict[str, str]:
    """Translates the names, keeps the values.

    The values accept anything: only the *name* is constrained. A name collision after
    translation (`a/b` and `a_b`) keeps the last one: unlikely enough not to deserve a failure,
    and visible in the returned tag anyway.
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
