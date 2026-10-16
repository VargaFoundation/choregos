# SPDX-License-Identifier: Apache-2.0
"""Adaptateurs Choregos : tout ce qui parle au monde extérieur.

Un adaptateur implémente un `Protocol` de `base.py` et **n'a aucune logique métier** :
la décision est dans `packages/core`, l'orchestration dans `apps/orchestrator`.
"""

from __future__ import annotations

import os
from collections.abc import Callable
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
    operation_spec,
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
    "operation_spec",
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
from .familles import OPERATIONS_MARKETDATA as _OPERATIONS_MARKETDATA  # noqa: E402 - after the registry
from .identity.entra import OPERATIONS_ENTRA as _OPERATIONS_ENTRA  # noqa: E402 - après le registre
from .netops.cilium import CiliumNetwork as _CiliumNetwork  # noqa: E402 - after the registry
from .netops.nginx import NginxConfig as _NginxConfig  # noqa: E402 - after the registry
from .netops.pfsense import PfSenseFirewall as _PfSenseFirewall  # noqa: E402 - after the registry

#: La configuration d'un type `demo` : rien, ou l'adresse du processus qui sert les faux.
_CONFIGURATION_DE_DEMO = _objet(
    {
        "url": {
            "type": "string",
            "description": "the MCP endpoint of the single process serving the fakes "
            "(`choregos_adapters.fakes.serveur`); empty: in this process",
        }
    }
)

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
    ("runtime", "agent_sandbox"): ConnectorTypeSpec(
        "kubernetes-sigs/agent-sandbox (warm pool, opt-in)",
        ("runtime",),
        _objet(
            {
                "warm_pool": {"type": "string", "default": "choregos-runner"},
                "assignment_port": _ENTIER,
                "runtime_class": _TEXTE,
                "pool_image": _TEXTE,
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
    ("gateway", "openai-compatible"): ConnectorTypeSpec(
        "OpenAI-compatible gateway (discovery only: no virtual keys, no cap, no measured cost)",
        ("gateway",),
        _objet(
            {
                "base_url": _TEXTE,
                "headers": {"type": "object", "additionalProperties": {"type": "string"}},
                "timeout_s": _ENTIER,
            }
        ),
        ("api_key", "run_key"),
    ),
    # Knowledge base sources (S33-04, ADR 0051): one Entra application holding `Sites.Selected`,
    # granted `read` on the chosen sites only — never `Files.Read.All` nor `Sites.Read.All`.
    ("documents", "sharepoint"): ConnectorTypeSpec(
        "Microsoft SharePoint and OneDrive (Graph, Sites.Selected)",
        ("documents",),
        _objet(
            {
                "tenant_id": _TEXTE,
                "client_id": _TEXTE,
                "graph_url": {"type": "string", "default": "https://graph.microsoft.com/v1.0"},
                "login_url": {"type": "string", "default": "https://login.microsoftonline.com"},
            },
            ("tenant_id", "client_id"),
        ),
        ("client_secret",),
    ),
    ("documents", "confluence"): ConnectorTypeSpec(
        "Atlassian Confluence Cloud (read)",
        ("documents",),
        _objet(
            {
                "base_url": {
                    "type": "string",
                    "pattern": r"^https://[a-z0-9-]+\.atlassian\.net/?$",
                    "description": "the Atlassian Cloud site, https://<site>.atlassian.net",
                },
                "email": {"type": "string", "description": "the service account the API token belongs to"},
                "auth": {
                    "type": "string",
                    "enum": ["basic", "scoped"],
                    "default": "basic",
                    "description": "`scoped` for a scoped API token: it is called through api.atlassian.com",
                },
                "cloud_id": {"type": "string", "description": "scoped tokens only; `test` reports it"},
            },
            ("base_url", "email"),
        ),
        ("api_token",),
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
        _CONFIGURATION_DE_DEMO,
        secret_fields=("api_key",),
        operations=_FAMILLES["mdm"],
    ),
    ("shipping", "demo"): ConnectorTypeSpec(
        "demo carrier (in memory: demonstrations only, refused in staging and prod)",
        ("shipping",),
        _CONFIGURATION_DE_DEMO,
        secret_fields=("api_key",),
        operations=_FAMILLES["shipping"],
    ),
    ("access_control", "demo"): ConnectorTypeSpec(
        "demo badge readers (in memory: demonstrations only, refused in staging and prod)",
        ("access_control",),
        _CONFIGURATION_DE_DEMO,
        secret_fields=("api_key",),
        operations=_FAMILLES["access_control"],
    ),
    # The `marketdata` family (S38-03): prices from free sources, read by the platform for a run whose
    # sandbox has no egress. Keys stay API-side (`secret_refs`).
    ("marketdata", "stooq"): ConnectorTypeSpec(
        "Stooq (daily CSV, free)",
        ("marketdata",),
        _objet(
            {
                "suffix_map": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": 'ticker suffix -> Stooq market suffix, e.g. {"": ".us", ".L": ".uk"}',
                },
                "timeout_s": {"type": "number", "default": 20},
            }
        ),
        ("api_key",),
        operations=_OPERATIONS_MARKETDATA,
    ),
    ("marketdata", "yahoo"): ConnectorTypeSpec(
        "Yahoo Finance chart (unofficial: personal use only, may break)",
        ("marketdata",),
        _objet({"timeout_s": {"type": "number", "default": 20}}),
        operations=_OPERATIONS_MARKETDATA,
    ),
    ("marketdata", "alphavantage"): ConnectorTypeSpec(
        "Alpha Vantage (free key: about 25 requests a day, unadjusted daily bars)",
        ("marketdata",),
        _objet({"timeout_s": {"type": "number", "default": 20}}),
        ("api_key",),
        operations=_OPERATIONS_MARKETDATA,
    ),
    ("marketdata", "composite"): ConnectorTypeSpec(
        "market data from several sources, with fallback and a cross-check of the closes",
        ("marketdata",),
        _objet(
            {
                "sources": {
                    "type": "array",
                    "items": {"enum": ["stooq", "yahoo", "alphavantage"]},
                    "minItems": 1,
                    "description": "provider types, in the order they are tried",
                },
                "source_configs": {
                    "type": "object",
                    "additionalProperties": {"type": "object"},
                    "description": 'sub-configuration by provider type, e.g. {"stooq": {"suffix_map": {}}}',
                },
                "cross_check": {"type": "boolean", "default": True},
                "tolerance": {"type": "number", "default": 0.02, "minimum": 0},
            },
            ("sources",),
        ),
        ("stooq_api_key", "alphavantage_api_key"),
        operations=_OPERATIONS_MARKETDATA,
    ),
    # Le train d'un locataire de démonstration (S21-24) : un environnement en mémoire, ou servi par le
    # processus des faux. Aucune opération exposée : c'est le train qui l'appelle, jamais un agent.
    ("cd", "demo"): ConnectorTypeSpec(
        "demo deployment (in memory: demonstrations only, refused in staging and prod)",
        ("cd",),
        _CONFIGURATION_DE_DEMO,
        secret_fields=("api_key",),
    ),
    # Netops (ADR 0052, S33-08): our own estate, read through the platform.
    ("firewall", "pfsense"): ConnectorTypeSpec(
        "pfSense (REST API package, v2)",
        ("firewall",),
        _objet(
            {
                "base_url": {"type": "string", "description": "https://<pfsense>, without /api/v2"},
                "verify_tls": {
                    "type": "boolean",
                    "default": True,
                    "description": "false: dev only, logged each time; refused in staging and prod",
                },
                "ca_cert": {
                    "type": "string",
                    "description": "PEM of the CA that signed the device's certificate",
                },
            },
            ("base_url",),
        ),
        ("api_key",),
        operations=_PfSenseFirewall.operations,
    ),
    ("network_policy", "cilium"): ConnectorTypeSpec(
        "Cilium (network policies, Hubble flows)",
        ("network_policy", "flows"),
        _objet(
            {
                "kubernetes_url": {"type": "string", "description": "the cluster's API server, https"},
                "verify_tls": {"type": "boolean", "default": True},
                "ca_cert": {"type": "string", "description": "PEM of the cluster's CA"},
                "hubble_server": {"type": "string", "default": "hubble-relay.kube-system.svc:443"},
                "hubble_tls": {"type": "boolean", "default": True},
                "hubble_ca_cert": {"type": "string", "description": "PEM of Hubble Relay's CA"},
                "hubble_tls_server_name": _TEXTE,
                "hubble_client_cert": {"type": "string", "description": "PEM, when Relay requires mTLS"},
                "gitops_repo": {
                    "type": "string",
                    "description": "owner/repo where policy changes are proposed",
                },
                "gitops_base": {"type": "string", "default": "main"},
                "gitops_path": {
                    "type": "string",
                    "default": "cilium",
                    "description": "the policies' directory",
                },
            },
            ("kubernetes_url",),
        ),
        ("token", "hubble_client_key", "gitops_token"),
        operations=_CiliumNetwork.operations,
    ),
    ("web_server", "nginx"): ConnectorTypeSpec(
        "nginx (configuration from the GitOps repository)",
        ("web_server",),
        _objet(
            {
                "repo": {"type": "string", "description": "owner/repo of the GitOps repository"},
                "ref": {"type": "string", "default": "main"},
                "root": {
                    "type": "string",
                    "default": "nginx",
                    "description": "the configuration's directory",
                },
                "api_url": {"type": "string", "default": "https://api.github.com"},
            },
            ("repo",),
        ),
        ("token",),
        operations=_NginxConfig.operations,
    ),
    ("mcp", "mcp"): ConnectorTypeSpec(
        "MCP server (Streamable HTTP)",
        ("mcp",),
        _objet(
            {
                "url": {"type": "string", "description": "the server's MCP endpoint, https"},
                "auth_scheme": {
                    "type": "string",
                    "enum": ["bearer", "basic"],
                    "default": "bearer",
                    "description": "how the token is sent: Bearer, or Basic base64(username:token)",
                },
                "username": {"type": "string", "description": "the account, for `basic` (an email)"},
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
        cfg.get("url", "http://fake-mcp.test/mcp"),
        token=cfg.get("token", ""),
        auth_scheme=cfg.get("auth_scheme", "bearer"),
        username=cfg.get("username", ""),
        transport=FAUX_MCP.transport(),
        user_agent=str(cfg.get("user_agent", "")),
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
        if en_production and cfg.get("url"):
            # Servi par un seul processus (`choregos_adapters.fakes.serveur`) : là où l'API et
            # l'orchestrateur sont deux processus, un faux en mémoire leur montrerait deux états.
            from .fakes.rh import GuichetDistant
            from .mcp import ClientMcp

            return GuichetDistant(ClientMcp(str(cfg["url"]), token=str(cfg.get("api_key", ""))))
        if famille not in FAUX_METIER:
            FAUX_METIER[famille] = FAUX_PAR_FAMILLE[famille]()
        return Guichet(FAUX_METIER[famille], str(cfg.get("api_key", "")))

    return construire


#: The netops fakes (ADR 0052): one pfSense, one cluster, one recorded Hubble trace, one GitOps
#: repository — shared in the process, scripted by the tests and the demo.
FAKE_NETOPS: dict[str, Any] = {}


def _fake_netops(name: str) -> Any:
    if name not in FAKE_NETOPS:
        from .fakes import netops as fakes

        builders: dict[str, Callable[[], Any]] = {
            "pfsense": fakes.FakePfSense,
            "kubernetes": fakes.FakeCiliumKubernetes,
            "flows": fakes.recorded_flows,
            "repo": fakes.FakeRepo,
        }
        FAKE_NETOPS[name] = builders[name]()
    return FAKE_NETOPS[name]


def _fake_pfsense(cfg: dict[str, Any]) -> Any:
    from .netops import PfSenseFirewall

    return PfSenseFirewall(
        base_url=str(cfg.get("base_url") or "https://pfsense.fake"),
        api_key=str(cfg.get("api_key") or "fake-api-key"),
        transport=_fake_netops("pfsense").transport(),
    )


def _fake_cilium(cfg: dict[str, Any]) -> Any:
    from .netops import CiliumNetwork, ReadOnlyKubernetes

    kubernetes = ReadOnlyKubernetes(
        str(cfg.get("kubernetes_url") or "https://kubernetes.fake"),
        str(cfg.get("token") or "fake-reader-token"),
        transport=_fake_netops("kubernetes").transport(),
    )
    gitops = _fake_gitops(
        str(cfg.get("gitops_repo") or "acme/gitops"),
        str(cfg.get("gitops_base") or "main"),
        str(cfg.get("gitops_path") or "cilium"),
    )
    return CiliumNetwork(kubernetes, _fake_netops("flows"), gitops)


def _fake_nginx(cfg: dict[str, Any]) -> Any:
    from .netops import NginxConfig

    root = str(cfg.get("root") or "nginx")
    gitops = _fake_gitops(str(cfg.get("repo") or "acme/gitops"), str(cfg.get("ref") or "main"), root)
    return NginxConfig(_fake_netops("repo"), root=root, gitops=gitops)


def _fake_gitops(repo: str, base: str, root: str) -> Any:
    from .netops.gitops import GitOpsTarget

    if "scm" not in FAKE_NETOPS:
        from .fakes import FakeScm

        FAKE_NETOPS["scm"] = FakeScm()
    return GitOpsTarget(FAKE_NETOPS["scm"], repo, base, root)


def _nginx(cfg: dict[str, Any]) -> Any:
    from .netops import GitHubRepoReader, NginxConfig
    from .netops.gitops import GitOpsTarget
    from .scm.github import GitHubScm

    client = _github_client(cfg)
    repo, ref, root = str(cfg["repo"]), str(cfg.get("ref") or "main"), str(cfg.get("root") or "nginx")
    gitops = GitOpsTarget(GitHubScm(client, default_repo=repo), repo, ref, root)
    return NginxConfig(GitHubRepoReader(client, repo, ref), root=root, gitops=gitops)


def _cilium(cfg: dict[str, Any]) -> Any:
    """The policies' pull requests go through the forge with `gitops_token` (or the GitHub App of
    the deployment) — `token` is the cluster reader's, and never reaches the forge."""
    from .netops import CiliumNetwork
    from .scm.github import GitHubScm

    scm = None
    if cfg.get("gitops_repo"):
        client = _github_client({**cfg, "token": cfg.get("gitops_token", "")})
        scm = GitHubScm(client, default_repo=str(cfg["gitops_repo"]))
    return CiliumNetwork.from_config(cfg, scm=scm)


#: The fake of the `marketdata` family, shared in the process (scriptable by tests).
FAUX_MARKETDATA: Any = None


def _faux_marketdata(cfg: dict[str, Any]) -> Any:
    global FAUX_MARKETDATA  # noqa: PLW0603 - a shared fake, like the platform's other fakes
    if FAUX_MARKETDATA is None:
        from .fakes.marketdata import FakeMarketData

        FAUX_MARKETDATA = FakeMarketData()
    return FAUX_MARKETDATA


def _cd_de_demo(cfg: dict[str, Any]) -> Any:
    """La fabrique de `cd: demo` : refusée en staging et en prod ; servie quand elle porte une `url`."""
    from .fakes.cd_de_demo import CdDeDemo, FakeCdDeDemo
    from .fakes.rh import Guichet, GuichetDistant

    environnement = _env("CHOREGOS_ENV", "dev")
    if environnement in {"staging", "prod"}:
        raise ConfigurationError(
            f"connecteur `cd: demo` refusé en {environnement} : un environnement simulé — une "
            "démonstration, pas un déploiement."
        )
    if cfg.get("url"):
        from .mcp import ClientMcp

        return CdDeDemo(GuichetDistant(ClientMcp(str(cfg["url"]), token=str(cfg.get("api_key", "")))))
    if "cd" not in FAUX_METIER:
        FAUX_METIER["cd"] = FakeCdDeDemo()
    return CdDeDemo(Guichet(FAUX_METIER["cd"], str(cfg.get("api_key", ""))))


def _register_marketdata() -> None:
    """The `marketdata` family (S38-03): its free providers, their composite and its fake."""
    from .marketdata import PROVIDERS, CompositeProvider, MarketDataProvider

    def _fournisseur(type_name: str, cfg: dict[str, Any], cle: str = "") -> MarketDataProvider:
        options: dict[str, Any] = {"timeout_s": float(cfg.get("timeout_s", 20))}
        if type_name == "stooq" and cfg.get("suffix_map"):
            options["suffix_map"] = dict(cfg["suffix_map"])
        if type_name != "yahoo":
            options["api_key"] = str(cle or cfg.get("api_key", ""))
        return PROVIDERS[type_name](**options)

    def _composite(cfg: dict[str, Any]) -> CompositeProvider:
        noms = [str(n) for n in cfg.get("sources") or []]
        inconnus = [n for n in noms if n not in PROVIDERS]
        if not noms or inconnus:
            raise ConfigurationError(
                f"marketdata composite: `sources` must list provider types among {sorted(PROVIDERS)}"
                + (f" (unknown: {inconnus})" if inconnus else "")
            )
        configs = cfg.get("source_configs") or {}
        return CompositeProvider(
            [
                (n, _fournisseur(n, dict(configs.get(n) or {}), str(cfg.get(f"{n}_api_key", ""))))
                for n in noms
            ],
            cross_check=bool(cfg.get("cross_check", True)),
            tolerance=float(cfg.get("tolerance", 0.02)),
        )

    def _constructeur(nom: str) -> Callable[[dict[str, Any]], MarketDataProvider]:
        return lambda cfg: _fournisseur(nom, cfg)

    for nom in PROVIDERS:
        register("marketdata", nom, _SPECS_LIVREES[("marketdata", nom)])(_constructeur(nom))
    register("marketdata", "composite", _SPECS_LIVREES[("marketdata", "composite")])(_composite)
    register("marketdata", "fake")(_faux_marketdata)


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
    from .fakes.executor import simulated_executor

    register("tracker", "fake")(lambda cfg: FakeTracker(cfg.get("project_slug", "demo")))
    register("scm", "fake")(lambda cfg: FakeScm())
    register("ci", "fake")(lambda cfg: FakeCi())
    register("cd", "fake")(lambda cfg: FakeCd())
    # `{"simulate": true}`: the process-wide simulated agent of the local install (`compose.yaml`).
    register("runtime", "fake")(lambda cfg: simulated_executor() if cfg.get("simulate") else FakeExecutor())
    register("memory", "fake")(lambda cfg: FakeMemory())
    register("gateway", "fake")(lambda cfg: FakeGateway())
    register("notify", "fake")(lambda cfg: FakeNotifier())
    register("mcp", "fake")(_client_du_faux_mcp)
    register("identity", "fake")(_annuaire_du_faux_entra)
    from .fakes.documents import FakeDocumentsConnector

    register("documents", "fake")(FakeDocumentsConnector)
    for famille in _FAMILLES:
        register(famille, "fake")(_guichet_de_demo(famille, en_production=False))
    register("firewall", "fake")(_fake_pfsense)
    register("network_policy", "fake")(_fake_cilium)
    register("web_server", "fake")(_fake_nginx)

    # ───────────────── implémentations réelles (jour 1) ─────────────────
    from .cd.argocd import ArgoCdAdapter
    from .ci.tekton import TektonCi
    from .executor.aca import AcaExecutor, AzureArmClient
    from .executor.agent_sandbox import AgentSandboxExecutor
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
    register("runtime", "agent_sandbox", _SPECS_LIVREES[("runtime", "agent_sandbox")])(
        lambda cfg: AgentSandboxExecutor(
            KubernetesClient(**cfg.get("kubernetes", {})),
            warm_pool=cfg.get(
                "warm_pool", os.environ.get("CHOREGOS_SANDBOX_WARM_POOL", "") or "choregos-runner"
            ),
            assignment_port=int(
                cfg.get("assignment_port", os.environ.get("CHOREGOS_SANDBOX_ASSIGNMENT_PORT", "") or 8765)
            ),
            # The template's RuntimeClass, which the chart sets from `runner.runtimeClass`.
            runtime_class=cfg.get("runtime_class", os.environ.get("CHOREGOS_RUNNER_RUNTIME_CLASS", ""))
            or None,
            # The chart builds the warm pool's template and the orchestrator's `CHOREGOS_RUNNER_IMAGE`
            # from the same value: a run asking for another image cannot be honoured.
            pool_image=cfg.get("pool_image", os.environ.get("CHOREGOS_RUNNER_IMAGE", "")) or None,
            max_active=int(cfg.get("max_active", os.environ.get("CHOREGOS_RUNNER_MAX_ACTIVE", "") or 0)),
            env_from_secrets=cfg.get("env_from_secrets")
            or [s for s in os.environ.get("CHOREGOS_RUNNER_ENV_SECRETS", "").split(",") if s],
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
    from .gateway.openai_compatible import OpenAiCompatibleGateway

    register("gateway", "openai-compatible", _SPECS_LIVREES[("gateway", "openai-compatible")])(
        lambda cfg: OpenAiCompatibleGateway(
            cfg.get("base_url", _env("CHOREGOS_GATEWAY_URL", "")),
            api_key=cfg.get("api_key", _env("CHOREGOS_GATEWAY_MASTER_KEY", "")),
            run_key=cfg.get("run_key", ""),
            headers=cfg.get("headers", {}),
            timeout=float(cfg.get("timeout_s", 20)),
        )
    )
    from .documents.graph import GraphDocumentsConnector

    register("documents", "sharepoint", _SPECS_LIVREES[("documents", "sharepoint")])(GraphDocumentsConnector)
    from .documents.confluence import ConfluenceDocumentsConnector

    register("documents", "confluence", _SPECS_LIVREES[("documents", "confluence")])(
        ConfluenceDocumentsConnector
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
    register("cd", "demo", _SPECS_LIVREES[("cd", "demo")])(_cd_de_demo)
    from .netops import PfSenseFirewall

    register("firewall", "pfsense", _SPECS_LIVREES[("firewall", "pfsense")])(PfSenseFirewall.from_config)
    register("network_policy", "cilium", _SPECS_LIVREES[("network_policy", "cilium")])(_cilium)
    register("web_server", "nginx", _SPECS_LIVREES[("web_server", "nginx")])(_nginx)

    _register_marketdata()
    # Un vrai serveur MCP (ADR 0034) : ses opérations se DÉCOUVRENT, et naissent fermées.
    from .mcp import ClientMcp

    register("mcp", "mcp", _SPECS_LIVREES[("mcp", "mcp")])(
        lambda cfg: ClientMcp(
            cfg["url"],
            token=cfg.get("token", ""),
            auth_scheme=cfg.get("auth_scheme", "bearer"),
            username=cfg.get("username", ""),
            timeout_s=float(cfg.get("timeout_s", 30)),
            user_agent=str(cfg.get("user_agent", "")),
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
