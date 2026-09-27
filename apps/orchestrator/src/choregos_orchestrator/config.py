"""Configuration des workers Temporal."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

QUEUE_ORCHESTRATOR = "orchestrator"
QUEUE_EXECUTOR = "executor"
QUEUE_TRACKER = "tracker"
QUEUE_MEMORY = "memory"
ALL_QUEUES = (QUEUE_ORCHESTRATOR, QUEUE_EXECUTOR, QUEUE_TRACKER, QUEUE_MEMORY)


class OrchestratorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CHOREGOS_", env_file=".env", extra="ignore")

    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    fakes: bool = Field(default=False, validation_alias="CHOREGOS_FAKES")
    api_url: str = "http://localhost:8000"
    # L'URL que les AGENTS rappellent, depuis le cluster. Elle n'est pas l'URL publique :
    # un pod ne résout pas le nom d'un ingress, et le runner mourait sur « Name or service
    # not known » sans qu'aucun message ne désigne l'URL. Elle existait ici depuis le début
    # et n'était lue nulle part — les rappels prenaient `api_url`.
    internal_api_url: str = "http://localhost:8000"
    gateway_url: str = "http://localhost:4000"
    #: La mémoire (Ecphoria). Le chart la passe depuis toujours (`CHOREGOS_MEMORY_URL` de
    #: `commonEnv`) et ce réglage n'existait pas : `extra="ignore"` l'avalait en silence, et
    #: l'URL MCP de la mémoire était fabriquée par `gateway_url.replace("4000", "8432")`.
    #: Le remplacement ne tient que si la passerelle et la mémoire partagent un hôte. Sur le
    #: locataire dev, la passerelle est `http://litellm:4000` : la mémoire devenait
    #: `http://litellm:8432`, un port fermé sur le bon hôte pour le mauvais service. Mesuré le
    #: 2026-09-27 — aucune réponse en huit secondes, là où `http://ecphoria:8432/mcp` répond
    #: 401 en neuf millisecondes.
    memory_url: str = "http://localhost:8432"
    #: Le sidecar MCP du catalogue d'outils (`choregos-tools`), tel que l'agent le voit.
    #: **Il n'existe que pour l'exécuteur `tekton`** : c'est un `sidecar:` de la Task
    #: (`templates/github-tekton-argo-k8s/tekton/agent-stage.yaml`). L'exécuteur `k8s_job`
    #: n'en monte aucun — `grep 7777` dans `k8s_job.py` ne rend rien. L'URL était pourtant
    #: annoncée à TOUT agent : sur le locataire dev, en `k8s_job`, l'agent recevait donc un
    #: serveur d'outils inexistant. Vide = ne rien annoncer, et c'est ce que `url_du_sidecar`
    #: rend quand l'exécuteur ne le fournit pas.
    tools_mcp_url: str = "http://localhost:7777/mcp"
    runner_image: str = "ghcr.io/vargafoundation/choregos-runner:1.0.0"
    executor_kind: str = "tekton"
    # Variables passées à CHAQUE pod d'agent (JSON dans `CHOREGOS_RUNNER_ENV`). Ce qui est
    # secret n'a rien à faire ici : `CHOREGOS_RUNNER_ENV_SECRETS` liste des secrets, que
    # l'exécuteur monte par référence.
    runner_env: dict[str, str] = {}
    runner_namespace_pattern: str = "proj-{slug}-runners"
    # Le proxy par lequel un pod d'agent sort (`HTTP_PROXY`/`HTTPS_PROXY`), avec
    # `{namespace}` pour celui des runners : le provisioning y déploie un Squid dont
    # l'allowlist est celle de la politique du projet. Vide : aucun proxy — le banc local,
    # dont le namespace n'a ni NetworkPolicy ni proxy.
    runner_egress_proxy: str = ""
    # L'image de ce proxy ; vide : celle que `gitops.py` connaît. À épingler par digest là
    # où Kyverno l'exige.
    egress_image: str = ""
    # La `RuntimeClass` de CHAQUE pod d'agent quand la politique du projet n'en impose pas
    # (`sandbox.runtime: gvisor` l'emporte). Vide : la runtime par défaut du cluster. Un
    # cluster qui a gVisor ou Kata le dit ici une fois, plutôt que projet par projet.
    runner_runtime_class: str = ""

    @property
    def callback_url(self) -> str:
        """Ce que le runner reçoit dans `callbacks.api_url`, sans le suffixe d'API interne."""
        return (self.internal_api_url or self.api_url).rstrip("/").removesuffix("/api/v1/internal")

    @property
    def url_du_sidecar_d_outils(self) -> str:
        """L'URL du sidecar MCP, ou `""` quand l'exécuteur n'en monte pas.

        Annoncer à un agent un serveur d'outils qui n'existe pas n'est pas neutre : opencode
        tente de s'y connecter au démarrage de sa session, et rien dans le ticket ne dit que le
        catalogue était hors de portée. Mieux vaut n'annoncer aucun outil — c'est faux dans les
        deux cas, mais l'un des deux se voit.
        """
        if self.executor_kind not in EXECUTEURS_AVEC_SIDECAR_MCP:
            return ""
        return self.tools_mcp_url

    object_store_url: str = "s3://choregos"
    public_url: str = "http://localhost:3000"
    log_level: str = "INFO"
    log_json: bool = True
    fx_usd_eur: float = 0.92
    history_size_threshold: int = 20_000
    heartbeat_seconds: int = 60
    # Polling de secours du tracker (S3-06) : 0 désactive le rattrapage.
    reconcile_interval_seconds: int = 60


#: Les exécuteurs qui montent le sidecar MCP du catalogue. C'est une propriété de l'exécuteur,
#: pas un réglage : un déploiement ne peut pas décider qu'un `k8s_job` a un sidecar qu'il n'a pas.
EXECUTEURS_AVEC_SIDECAR_MCP = frozenset({"tekton"})


@lru_cache(maxsize=1)
def get_settings() -> OrchestratorSettings:
    return OrchestratorSettings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
