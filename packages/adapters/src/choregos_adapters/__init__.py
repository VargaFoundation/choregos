# SPDX-License-Identifier: Apache-2.0
"""Adaptateurs Choregos : tout ce qui parle au monde extérieur.

Un adaptateur implémente un `Protocol` de `base.py` et **n'a aucune logique métier** :
la décision est dans `packages/core`, l'orchestration dans `apps/orchestrator`.
"""

from __future__ import annotations

import os
from typing import Any

from .base import (
    AgentBackend,
    CdAdapter,
    CiAdapter,
    Executor,
    GatewayAdapter,
    MemoryAdapter,
    Notifier,
    ScmAdapter,
    TrackerAdapter,
)
from .errors import ConfigurationError
from .registry import (
    POLITIQUES,
    AdapterSet,
    ConnectorTypeSpec,
    OperationSpec,
    available,
    build,
    configuration_resolue,
    connector_types,
    fakes_enabled,
    register,
    spec_of,
    type_par_defaut,
)


def _env(name: str, default: str = "") -> str:
    import os

    return os.environ.get(name, default)


__version__ = "0.1.0"

__all__ = [
    "POLITIQUES",
    "AdapterSet",
    "AgentBackend",
    "CdAdapter",
    "CiAdapter",
    "ConnectorTypeSpec",
    "Executor",
    "GatewayAdapter",
    "MemoryAdapter",
    "Notifier",
    "OperationSpec",
    "ScmAdapter",
    "TrackerAdapter",
    "available",
    "build",
    "charger_les_greffons",
    "configuration_resolue",
    "connector_types",
    "fakes_enabled",
    "register",
    "spec_of",
    "type_par_defaut",
]


def _github_client(config: dict[str, Any]) -> Any:
    """Construit le client GitHub depuis la configuration du connecteur ou l'environnement."""
    import os

    from .github import GitHubAppAuth, GitHubClient

    app_id = config.get("app_id") or os.environ.get("CHOREGOS_GITHUB_APP_ID", "")
    private_key = config.get("private_key") or os.environ.get("CHOREGOS_GITHUB_APP_PRIVATE_KEY", "")
    token = config.get("token") or os.environ.get("CHOREGOS_GITHUB_TOKEN", "")
    auth = GitHubAppAuth(app_id=app_id, private_key=private_key) if app_id and private_key else None
    return GitHubClient(
        auth=auth, token=token or None, base_url=config.get("api_url", "https://api.github.com")
    )


def _passerelle_directe(cfg: dict[str, Any]) -> Any:
    """La passerelle « directe » ne compte rien et ne plafonne rien : interdite en production.

    Elle rend la clé qu'on lui a donnée. Aucune clé virtuelle par run, donc aucun plafond dur,
    et le registre de coûts reste à zéro — le banc a tourné ainsi pendant une semaine et
    « prouvait » un plafond de dépense qui n'avait jamais mesuré une dépense (BLOCKERS, S12).
    Sur un environnement sérieux, ce n'est pas une configuration : c'est une comptabilité
    éteinte sans que personne l'ait décidé. On refuse donc de la construire, en nommant la
    sortie — comme `garde.yaml` refuse une dépendance embarquée en staging et en prod.
    """
    from .gateway.direct import DirectGateway  # import paresseux, comme les autres

    environnement = _env("CHOREGOS_ENV", "dev")
    if environnement in {"staging", "prod"}:
        raise ConfigurationError(
            f"connecteur `gateway: direct` refusé en {environnement} : cette passerelle ne "
            "mesure aucun coût et n'applique aucun plafond. Utiliser `litellm`, qui émet une "
            "clé virtuelle plafonnée par run."
        )
    return DirectGateway(
        key=cfg.get("key", _env("CHOREGOS_GATEWAY_DIRECT_KEY", "")),
        models=cfg.get("models", []),
    )


def _objet(proprietes: dict[str, Any], requis: tuple[str, ...] = ()) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": proprietes}
    if requis:
        schema["required"] = list(requis)
    return schema


_TEXTE: dict[str, Any] = {"type": "string"}
_ENTIER: dict[str, Any] = {"type": "integer"}

from .familles import FAMILLES as _FAMILLES  # noqa: E402 - après le registre
from .identity.entra import OPERATIONS_ENTRA as _OPERATIONS_ENTRA  # noqa: E402 - après le registre

#: Ce que chaque type livré déclare (ADR 0034) : son nom, ses capacités, le schéma de sa
#: configuration — celui dont la console tire le formulaire — et ses champs SECRETS, qui ne
#: s'écrivent qu'en référence. Ces schémas vivaient dans le routeur de l'API, en double du
#: registre et en désaccord avec lui (jira y était « indisponible » alors qu'il est enregistré).
_SPECS_LIVREES: dict[tuple[str, str], ConnectorTypeSpec] = {
    ("tracker", "github-issues"): ConnectorTypeSpec(
        "GitHub Issues + Projects v2",
        ("tracker",),
        _objet(
            {
                "repo": {"type": "string", "description": "owner/repo"},
                "project_number": _ENTIER,
                "org": _TEXTE,
                "installation_id": _ENTIER,
            },
            ("repo",),
        ),
        ("webhook_secret",),
    ),
    ("tracker", "internal"): ConnectorTypeSpec("internal (Choregos holds the work items)", ("tracker",)),
    ("tracker", "jira"): ConnectorTypeSpec(
        "Jira Cloud",
        ("tracker",),
        _objet(
            {
                "base_url": {"type": "string", "description": "https://<site>.atlassian.net"},
                "email": _TEXTE,
                "project_key": _TEXTE,
                "field_names": {"type": "object", "additionalProperties": {"type": "string"}},
            },
            ("base_url", "email", "project_key"),
        ),
        ("api_token", "webhook_secret"),
    ),
    ("tracker", "gitlab-issues"): ConnectorTypeSpec(
        "GitLab Issues",
        ("tracker",),
        _objet(
            {"base_url": {"type": "string", "default": "https://gitlab.com"}, "project": _TEXTE}, ("project",)
        ),
        ("token", "webhook_secret"),
    ),
    ("scm", "github"): ConnectorTypeSpec(
        "GitHub (App choregos-bot)",
        ("scm",),
        _objet(
            {
                "repo": _TEXTE,
                "installation_id": _ENTIER,
                "merge_queue": {"type": "boolean", "default": True},
                "merge_method": {
                    "type": "string",
                    "enum": ["MERGE", "SQUASH", "REBASE"],
                    "default": "SQUASH",
                },
            },
            ("repo",),
        ),
    ),
    ("ci", "tekton"): ConnectorTypeSpec(
        "Tekton Pipelines",
        ("ci",),
        _objet({"namespace": _TEXTE, "pipeline": {"type": "string", "default": "choregos-ci"}}),
    ),
    ("cd", "argocd"): ConnectorTypeSpec(
        "Argo CD + Rollouts",
        ("cd",),
        _objet(
            {
                "gitops_repo": _TEXTE,
                "base_url": _TEXTE,
                "app_pattern": {"type": "string", "default": "{app}-{env}"},
                "path_prefix": {"type": "string", "default": "apps"},
            },
            ("gitops_repo",),
        ),
        ("token",),
    ),
    ("runtime", "tekton"): ConnectorTypeSpec(
        "Tekton PipelineRun",
        ("runtime",),
        _objet({"pipeline": {"type": "string", "default": "choregos-agent"}, "service_account": _TEXTE}),
    ),
    ("runtime", "k8s_job"): ConnectorTypeSpec(
        "Kubernetes Job",
        ("runtime",),
        _objet(
            {
                "service_account": _TEXTE,
                "cpu_limit": _TEXTE,
                "memory_limit": _TEXTE,
                "max_active": _ENTIER,
                "env_from_secrets": {"type": "array", "items": _TEXTE},
            }
        ),
    ),
    ("runtime", "aca"): ConnectorTypeSpec(
        "Azure Container Apps jobs",
        ("runtime",),
        _objet(
            {
                "subscription_id": _TEXTE,
                "resource_group": _TEXTE,
                "tenant_id": _TEXTE,
                "client_id": _TEXTE,
                "environment_id": _TEXTE,
                "location": {"type": "string", "default": "westeurope"},
                "identity_id": _TEXTE,
                "registry_server": _TEXTE,
                "log_analytics_workspace_id": _TEXTE,
            },
            ("subscription_id", "resource_group", "environment_id"),
        ),
        ("client_secret", "token"),
    ),
    ("runtime", "local_docker"): ConnectorTypeSpec(
        "local Docker (development)", ("runtime",), _objet({"network": _TEXTE})
    ),
    ("memory", "ecphoria"): ConnectorTypeSpec(
        "Ecphoria (memory and knowledge base)",
        ("memory",),
        _objet({"base_url": _TEXTE, "tenant": _TEXTE, "read_timeout_ms": _ENTIER}),
        ("token",),
    ),
    ("memory", "lexical"): ConnectorTypeSpec("lexical (fallback, no service)", ("memory",)),
    ("memory", "pgvector"): ConnectorTypeSpec(
        "pgvector (deprecated alias of lexical)", ("memory",), deprecated=True
    ),
    ("gateway", "direct"): ConnectorTypeSpec(
        "direct (development only: no cost measured, no cap)",
        ("gateway",),
        _objet({"models": {"type": "array", "items": _TEXTE}}),
        ("key",),
    ),
    ("gateway", "litellm"): ConnectorTypeSpec(
        "LiteLLM",
        ("gateway",),
        _objet(
            {
                "base_url": _TEXTE,
                "team_id": _TEXTE,
                "internal_prices": {"type": "object", "additionalProperties": {"type": "string"}},
                "enterprise_tags": {"type": "boolean", "default": False},
            }
        ),
        ("master_key",),
    ),
    ("identity", "entra"): ConnectorTypeSpec(
        "Microsoft Entra ID (Graph)",
        ("identity",),
        _objet(
            {
                "tenant_id": _TEXTE,
                "client_id": _TEXTE,
                "administrative_unit_id": {
                    "type": "string",
                    "description": "every account the platform creates enters it; any other is refused",
                },
                "graph_url": {"type": "string", "default": "https://graph.microsoft.com/v1.0"},
                "login_url": {"type": "string", "default": "https://login.microsoftonline.com"},
            },
            ("tenant_id", "client_id"),
        ),
        ("client_secret",),
        operations=tuple(
            OperationSpec(nom, acces, description, schema)
            for nom, acces, description, schema in _OPERATIONS_ENTRA
        ),
    ),
    # Les familles métier (S20-04) n'ont pas encore de vrai type : `demo` les tient en mémoire, chaque
    # processus pour lui — une démonstration, refusée en staging et en prod.
    ("mdm", "demo"): ConnectorTypeSpec(
        "demo device management (in memory: demonstrations only, refused in staging and prod)",
        ("mdm",),
        secret_fields=("api_key",),
        operations=_FAMILLES["mdm"],
    ),
    ("shipping", "demo"): ConnectorTypeSpec(
        "demo carrier (in memory: demonstrations only, refused in staging and prod)",
        ("shipping",),
        secret_fields=("api_key",),
        operations=_FAMILLES["shipping"],
    ),
    ("access_control", "demo"): ConnectorTypeSpec(
        "demo badge readers (in memory: demonstrations only, refused in staging and prod)",
        ("access_control",),
        secret_fields=("api_key",),
        operations=_FAMILLES["access_control"],
    ),
    ("mcp", "mcp"): ConnectorTypeSpec(
        "MCP server (Streamable HTTP)",
        ("mcp",),
        _objet(
            {
                "url": {"type": "string", "description": "the server's MCP endpoint, https"},
                "timeout_s": {"type": "number", "default": 30},
            },
            ("url",),
        ),
        ("token",),
    ),
    ("notify", "slack"): ConnectorTypeSpec(
        "Slack",
        ("notify",),
        _objet({"channel": {"type": "string", "default": "#choregos"}, "public_url": _TEXTE}),
        ("webhook_url", "bot_token"),
    ),
}


#: Le serveur MCP des faux (`CHOREGOS_FAKES=1`) : un seul, que les tests et la démo scriptent.
FAUX_MCP: Any = None


def _client_du_faux_mcp(cfg: dict[str, Any]) -> Any:
    from .fakes.mcp import FakeMcpServer
    from .mcp import ClientMcp

    global FAUX_MCP  # noqa: PLW0603 - un faux partagé, comme les autres faux de la plateforme
    if FAUX_MCP is None:
        FAUX_MCP = FakeMcpServer()
    return ClientMcp(
        cfg.get("url", "http://fake-mcp.test/mcp"), token=cfg.get("token", ""), transport=FAUX_MCP.transport()
    )


#: Le Graph des faux : un seul, que les tests et la démo scriptent.
FAUX_ENTRA: Any = None


def _annuaire_du_faux_entra(cfg: dict[str, Any]) -> Any:
    from .fakes.entra import FakeEntra
    from .identity import EntraIdentity

    global FAUX_ENTRA  # noqa: PLW0603 - un faux partagé, comme les autres faux de la plateforme
    if FAUX_ENTRA is None:
        FAUX_ENTRA = FakeEntra()
    return EntraIdentity(
        tenant_id=cfg.get("tenant_id", "acme"),
        client_id=cfg.get("client_id", "choregos"),
        client_secret=cfg.get("client_secret", "faux"),
        administrative_unit_id=cfg.get("administrative_unit_id", FAUX_ENTRA.unite),
        transport=FAUX_ENTRA.transport(),
    )


#: Les faux des familles métier, un par famille : partagés dans le processus, comme les autres.
FAUX_METIER: dict[str, Any] = {}


def _guichet_de_demo(famille: str, *, en_production: bool) -> Any:
    """La fabrique d'un connecteur `<famille>: demo` (ou de son faux, sous `CHOREGOS_FAKES=1`)."""

    def construire(cfg: dict[str, Any]) -> Any:
        from .fakes.rh import FAUX_PAR_FAMILLE, Guichet

        environnement = _env("CHOREGOS_ENV", "dev")
        if en_production and environnement in {"staging", "prod"}:
            raise ConfigurationError(
                f"connecteur `{famille}: demo` refusé en {environnement} : un faux en mémoire, que "
                "chaque processus tient pour lui — une démonstration, pas un système de l'entreprise."
            )
        if famille not in FAUX_METIER:
            FAUX_METIER[famille] = FAUX_PAR_FAMILLE[famille]()
        return Guichet(FAUX_METIER[famille], str(cfg.get("api_key", "")))

    return construire


def _register_builtins() -> None:
    """Enregistre les implémentations livrées (import paresseux pour éviter les cycles)."""
    from .fakes import (
        FakeCd,
        FakeCi,
        FakeExecutor,
        FakeGateway,
        FakeMemory,
        FakeNotifier,
        FakeScm,
        FakeTracker,
    )

    register("tracker", "fake")(lambda cfg: FakeTracker(cfg.get("project_slug", "demo")))
    register("scm", "fake")(lambda cfg: FakeScm())
    register("ci", "fake")(lambda cfg: FakeCi())
    register("cd", "fake")(lambda cfg: FakeCd())
    register("runtime", "fake")(lambda cfg: FakeExecutor())
    register("memory", "fake")(lambda cfg: FakeMemory())
    register("gateway", "fake")(lambda cfg: FakeGateway())
    register("notify", "fake")(lambda cfg: FakeNotifier())
    register("mcp", "fake")(_client_du_faux_mcp)
    register("identity", "fake")(_annuaire_du_faux_entra)
    for famille in _FAMILLES:
        register(famille, "fake")(_guichet_de_demo(famille, en_production=False))

    # ───────────────── implémentations réelles (jour 1) ─────────────────
    from .cd.argocd import ArgoCdAdapter
    from .ci.tekton import TektonCi
    from .executor.aca import AcaExecutor, AzureArmClient
    from .executor.k8s_job import KubernetesJobExecutor
    from .executor.local_docker import LocalDockerExecutor
    from .executor.tekton import KubernetesClient, TektonExecutor
    from .gateway.litellm import LiteLlmGateway
    from .http import RestClient
    from .memory.ecphoria import EcphoriaMemory
    from .memory.lexicale import LexicalMemory
    from .notify.slack import SlackNotifier
    from .scm.github import GitHubScm
    from .tracker.github import GitHubTracker
    from .tracker.gitlab import GitLabTracker
    from .tracker.interne import InternalTracker
    from .tracker.jira import JiraTracker

    register("tracker", "github-issues", _SPECS_LIVREES[("tracker", "github-issues")])(
        lambda cfg: GitHubTracker(
            _github_client(cfg),
            cfg["repo"],
            project_number=cfg.get("project_number"),
            org=cfg.get("org"),
            webhook_secret=cfg.get("webhook_secret", ""),
        )
    )
    register("tracker", "internal", _SPECS_LIVREES[("tracker", "internal")])(lambda cfg: InternalTracker())
    register("tracker", "jira", _SPECS_LIVREES[("tracker", "jira")])(
        lambda cfg: JiraTracker(
            RestClient(
                cfg["base_url"],
                # Jira Cloud : e-mail + jeton d'API en Basic. Le jeton vient du coffre,
                # jamais du dépôt (AGENTS.md : aucun secret en dur).
                auth=(cfg["email"], cfg["api_token"]),
                service="jira",
            ),
            cfg["project_key"],
            webhook_secret=cfg.get("webhook_secret", ""),
            field_names=cfg.get("field_names"),
        )
    )
    register("tracker", "gitlab-issues", _SPECS_LIVREES[("tracker", "gitlab-issues")])(
        lambda cfg: GitLabTracker(
            RestClient(
                cfg.get("base_url", "https://gitlab.com"),
                headers={"PRIVATE-TOKEN": cfg["token"]},
                service="gitlab",
            ),
            cfg["project"],
            webhook_secret=cfg.get("webhook_secret", ""),
        )
    )
    register("scm", "github", _SPECS_LIVREES[("scm", "github")])(
        lambda cfg: GitHubScm(
            _github_client(cfg),
            default_repo=cfg.get("repo", ""),
            merge_method=cfg.get("merge_method", "SQUASH"),
            use_merge_queue=cfg.get("merge_queue", True),
        )
    )
    register("ci", "tekton", _SPECS_LIVREES[("ci", "tekton")])(
        lambda cfg: TektonCi(
            KubernetesClient(**cfg.get("kubernetes", {})),
            namespace=cfg.get("namespace", "default"),
            pipeline=cfg.get("pipeline", "choregos-ci"),
        )
    )
    register("cd", "argocd", _SPECS_LIVREES[("cd", "argocd")])(
        lambda cfg: ArgoCdAdapter(
            base_url=cfg.get("base_url", "http://argocd-server.argocd"),
            token=cfg.get("token", ""),
            gitops_repo=cfg.get("gitops_repo", ""),
            github=_github_client(cfg),
            app_pattern=cfg.get("app_pattern", "{app}-{env}"),
            path_prefix=cfg.get("path_prefix", "apps"),
        )
    )
    register("runtime", "tekton", _SPECS_LIVREES[("runtime", "tekton")])(
        lambda cfg: TektonExecutor(
            KubernetesClient(**cfg.get("kubernetes", {})),
            pipeline=cfg.get("pipeline", "choregos-agent"),
            service_account=cfg.get("service_account", "choregos-runner"),
        )
    )
    register("runtime", "k8s_job")(
        lambda cfg: KubernetesJobExecutor(
            KubernetesClient(**cfg.get("kubernetes", {})),
            service_account=cfg.get("service_account", "choregos-runner"),
            cpu_limit=cfg.get("cpu_limit", os.environ.get("CHOREGOS_RUNNER_CPU_LIMIT", "") or "2"),
            memory_limit=cfg.get("memory_limit", os.environ.get("CHOREGOS_RUNNER_MEMORY_LIMIT", "") or "6Gi"),
            env_from_secrets=cfg.get("env_from_secrets")
            or [s for s in os.environ.get("CHOREGOS_RUNNER_ENV_SECRETS", "").split(",") if s],
            # Combien de runs tournent en même temps dans le namespace. 0 = sans plafond.
            max_active=int(cfg.get("max_active", os.environ.get("CHOREGOS_RUNNER_MAX_ACTIVE", "") or 0)),
        )
    )
    register("runtime", "aca", _SPECS_LIVREES[("runtime", "aca")])(
        lambda cfg: AcaExecutor(
            AzureArmClient(
                subscription_id=cfg["subscription_id"],
                resource_group=cfg["resource_group"],
                tenant_id=cfg.get("tenant_id", ""),
                client_id=cfg.get("client_id", ""),
                client_secret=cfg.get("client_secret", ""),
                token=cfg.get("token", ""),
            ),
            environment_id=cfg["environment_id"],
            location=cfg.get("location", "westeurope"),
            identity_id=cfg.get("identity_id"),
            registry_server=cfg.get("registry_server"),
            log_analytics_workspace_id=cfg.get("log_analytics_workspace_id"),
        )
    )
    register("runtime", "local_docker", _SPECS_LIVREES[("runtime", "local_docker")])(
        lambda cfg: LocalDockerExecutor(network=cfg.get("network", "choregos_default"))
    )
    register("memory", "ecphoria", _SPECS_LIVREES[("memory", "ecphoria")])(
        lambda cfg: EcphoriaMemory(
            # Comme la passerelle : l'URL et le jeton du déploiement, sauf surcharge par projet.
            # `CHOREGOS_MEMORY_URL` était posé par le chart et ignoré ici.
            cfg.get("base_url", _env("CHOREGOS_MEMORY_URL", "http://ecphoria.choregos-memory:8432")),
            token=cfg.get("token", _env("CHOREGOS_MEMORY_TOKEN", "")),
            tenant=cfg.get("tenant"),
            read_timeout_ms=int(cfg.get("read_timeout_ms", 300)),
        )
    )
    # `pgvector` : alias déprécié de `lexical` — le nom promettait des vecteurs, le code fait
    # une similarité lexicale. Conservé pour les projets déjà configurés.
    register("memory", "lexical", _SPECS_LIVREES[("memory", "lexical")])(lambda cfg: LexicalMemory())
    register("memory", "pgvector", _SPECS_LIVREES[("memory", "pgvector")])(lambda cfg: LexicalMemory())
    register("gateway", "direct", _SPECS_LIVREES[("gateway", "direct")])(_passerelle_directe)
    register("gateway", "litellm", _SPECS_LIVREES[("gateway", "litellm")])(
        lambda cfg: LiteLlmGateway(
            cfg.get("base_url", _env("CHOREGOS_GATEWAY_URL", "http://litellm.choregos-gateway:4000")),
            cfg.get("master_key", _env("CHOREGOS_GATEWAY_MASTER_KEY", "")),
            team_id=cfg.get("team_id"),
            internal_prices=cfg.get("internal_prices", {}),
            enterprise_tags=cfg.get("enterprise_tags", False),
        )
    )
    from .identity import EntraIdentity

    register("identity", "entra", _SPECS_LIVREES[("identity", "entra")])(
        lambda cfg: EntraIdentity(
            tenant_id=cfg["tenant_id"],
            client_id=cfg["client_id"],
            client_secret=cfg.get("client_secret", ""),
            administrative_unit_id=cfg.get("administrative_unit_id"),
            graph_url=cfg.get("graph_url", "https://graph.microsoft.com/v1.0"),
            login_url=cfg.get("login_url", "https://login.microsoftonline.com"),
        )
    )
    for famille in _FAMILLES:
        register(famille, "demo", _SPECS_LIVREES[(famille, "demo")])(
            _guichet_de_demo(famille, en_production=True)
        )
    # Un vrai serveur MCP (ADR 0034) : ses opérations se DÉCOUVRENT, et naissent fermées.
    from .mcp import ClientMcp

    register("mcp", "mcp", _SPECS_LIVREES[("mcp", "mcp")])(
        lambda cfg: ClientMcp(
            cfg["url"], token=cfg.get("token", ""), timeout_s=float(cfg.get("timeout_s", 30))
        )
    )
    register("notify", "slack", _SPECS_LIVREES[("notify", "slack")])(
        lambda cfg: SlackNotifier(
            webhook_url=cfg.get("webhook_url", _env("CHOREGOS_SLACK_WEBHOOK", "")),
            bot_token=cfg.get("bot_token", _env("CHOREGOS_SLACK_TOKEN", "")),
            default_channel=cfg.get("channel", "#choregos"),
            public_url=cfg.get("public_url", _env("CHOREGOS_PUBLIC_URL", "")),
        )
    )


#: Le groupe de points d'entrée que la plateforme lit au démarrage. Un paquet installé à côté
#: — l'édition entreprise, un connecteur maison — s'y déclare et s'enregistre lui-même.
GROUPE_DE_GREFFONS = "choregos.plugins"


def charger_les_greffons(groupe: str = GROUPE_DE_GREFFONS) -> list[str]:
    """Charge les greffons déclarés hors de cet arbre, et rend leurs noms.

    Jusqu'au 2026-09-26 il n'existait AUCUNE couture : `register()` était public, mais rien
    n'importait jamais un module tiers, donc un paquet extérieur ne pouvait pas s'enregistrer
    sans qu'on patche `_register_builtins()`. Les playbooks étaient la seule capacité vraiment
    extensible depuis l'extérieur (`CHOREGOS_PLAYBOOKS_DIR`) ; c'est le modèle qu'on copie ici.

    Chaque point d'entrée désigne un appelable sans argument, qui fait ses `register(...)`.

    **Un greffon déclaré qui ne charge pas arrête le processus.** Même règle que `garde.yaml` et
    que le refus d'un réglage non résolu : quelqu'un l'a installé pour qu'il serve, et un greffon
    silencieusement absent laisse une plateforme qui paraît complète et ne l'est pas.
    """
    from importlib import metadata

    charges: list[str] = []
    for point in metadata.entry_points(group=groupe):
        try:
            point.load()()
        except Exception as erreur:
            raise RuntimeError(
                f"greffon « {point.name} » ({point.value}) : chargement impossible — {erreur}. "
                "Il est déclaré, donc il doit servir ; la plateforme ne démarre pas sans lui."
            ) from erreur
        charges.append(point.name)
    return charges


_register_builtins()
