# SPDX-License-Identifier: Apache-2.0
"""Choregos adapters: everything that talks to the outside world.

An adapter implements a `Protocol` of `base.py` and **has no business logic**: the decision is in
`packages/core`, the orchestration in `apps/orchestrator`.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Any

from choregos_core.compat import alias_names

from .base import (
    AgentBackend,
    CdAdapter,
    CiAdapter,
    DecisionClient,
    Executor,
    GatewayAdapter,
    MemoryAdapter,
    Notifier,
    ScmAdapter,
    TrackerAdapter,
)
from .errors import AdapterError, ConfigurationError
from .http.pool import strategy_of
from .registry import (
    POLICIES,
    AdapterSet,
    ConnectorTypeSpec,
    OperationSpec,
    account_of,
    available,
    build,
    connector_types,
    default_type,
    deployment_default_type,
    egress_hosts,
    fakes_enabled,
    offered_connector_types,
    operation_spec,
    register,
    resolved_configuration,
    spec_of,
    validate_config,
)


def _env(name: str, default: str = "") -> str:
    import os

    return os.environ.get(name, default)


__version__ = "0.1.0"

__all__ = [
    "POLICIES",
    "AdapterSet",
    "AgentBackend",
    "CdAdapter",
    "CiAdapter",
    "ConnectorTypeSpec",
    "DecisionClient",
    "Executor",
    "GatewayAdapter",
    "MemoryAdapter",
    "Notifier",
    "OperationSpec",
    "ScmAdapter",
    "TrackerAdapter",
    "account_of",
    "available",
    "build",
    "connector_types",
    "default_type",
    "deployment_default_type",
    "egress_hosts",
    "fakes_enabled",
    "load_extensions",
    "offered_connector_types",
    "operation_spec",
    "register",
    "resolved_configuration",
    "spec_of",
    "validate_config",
]


def _names(listed: str) -> list[str]:
    """A comma-separated list of names, as the chart joins them: blanks and empties dropped."""
    return [name.strip() for name in listed.split(",") if name.strip()]


def _jira_tracker(cfg: dict[str, Any]) -> Any:
    """Jira Cloud: the email and the API token as Basic, the token from the vault, never from the
    repository. A scoped token goes through the Atlassian gateway, a classic one to the site."""
    from .http import RestClient
    from .tracker.jira import JiraGatewayClient, JiraTracker

    auth = (str(cfg["email"]), str(cfg["api_token"]))
    client = (
        JiraGatewayClient(cfg["base_url"], cloud_id=cfg.get("cloud_id"), auth=auth, service="jira")
        if cfg.get("auth") == "scoped"
        else RestClient(cfg["base_url"], auth=auth, service="jira")
    )
    return JiraTracker(
        client,
        cfg["project_key"],
        webhook_secret=cfg.get("webhook_secret", ""),
        field_names=cfg.get("field_names"),
        candidates_jql=cfg.get("candidates_jql"),
    )


def _atlassian_egress(config: dict[str, Any]) -> list[tuple[str, int]]:
    """The site, plus `api.atlassian.com` when a scoped token is called through it (Jira, Confluence)."""
    from choregos_core.egress import hosts_of_config

    hosts = hosts_of_config(config)
    if config.get("auth") == "scoped" and ("api.atlassian.com", 443) not in hosts:
        hosts.append(("api.atlassian.com", 443))
    return hosts


def _firecrawl_egress(config: dict[str, Any]) -> list[tuple[str, int]]:
    """The self-hosted instance's host and port, and nothing else (S40-01k): the organisation's network
    ceiling is checked against what the connector really calls. No `base_url`: no host, and the
    adapter refuses to be built."""
    from choregos_core.egress import host_port_of

    found = host_port_of(config.get("base_url"))
    return [found] if found else []


#: The `mcp-gateway` connector type: a server the organisation's MCP gateway names (ADR 0105).
MCP_GATEWAY_TYPE = "mcp-gateway"
GATEWAY_SERVER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
#: The only keys a stored `mcp-gateway` configuration may hold; every other key is the platform's.
_GATEWAY_STORED_KEYS = ("server", "timeout_s")
_GATEWAY_ONLY_PLATFORM = (
    "an `mcp-gateway` connector is reached through its organisation's MCP gateway, which only the "
    "platform supplies: it cannot be built from its configuration alone"
)


def _gateway_server_check(server: Any) -> None:
    if not isinstance(server, str) or not re.fullmatch(GATEWAY_SERVER_PATTERN, server):
        raise AdapterError(
            "`server` is the server's name at the organisation's MCP gateway: letters, digits, "
            "'.', '_' or '-', 128 at most"
        )


def _mcp_gateway_check(config: dict[str, Any]) -> None:
    """A stored configuration: the server's name and a timeout, nothing else."""
    _gateway_server_check(config.get("server"))
    extra = sorted(str(key) for key in config if key not in _GATEWAY_STORED_KEYS)
    if extra:
        raise AdapterError(
            f"{', '.join(f'`{key}`' for key in extra)} has no place in an `mcp-gateway` connector: "
            "it stores only `server` and `timeout_s`; the organisation's MCP gateway holds the "
            "server's address and credential, and the platform supplies the rest"
        )


def _firecrawl_check(config: dict[str, Any]) -> None:
    from .web_search.firecrawl import checked_base_url

    checked_base_url(config.get("base_url"))


def _web_documents_check(config: dict[str, Any]) -> None:
    from .documents.base import SourceRefusedError
    from .documents.web import check_connector_config

    try:
        check_connector_config(config)
    except SourceRefusedError as refused:
        raise ConfigurationError(str(refused)) from refused


def _github_client(config: dict[str, Any]) -> Any:
    """Builds the GitHub client from the connector's configuration or the environment."""
    import os

    from .github import GitHubAppAuth, GitHubClient

    app_id = config.get("app_id") or os.environ.get("CHOREGOS_GITHUB_APP_ID", "")
    private_key = config.get("private_key") or os.environ.get("CHOREGOS_GITHUB_APP_PRIVATE_KEY", "")
    token = config.get("token") or os.environ.get("CHOREGOS_GITHUB_TOKEN", "")
    # `installation_id` is never typed (ADR 0073): the API injects the one this organisation claimed.
    # Without it (a single-organisation instance, a repository outside the claims) the installation
    # is discovered from the repository, as before.
    pinned = config.get("installation_id")
    owner = str(config.get("repo") or "").split("/", 1)[0] or None
    auth = (
        GitHubAppAuth(
            app_id=app_id,
            private_key=private_key,
            pinned_installation=int(pinned) if pinned else None,
            pinned_account=owner if pinned else None,
        )
        if app_id and private_key
        else None
    )
    return GitHubClient(
        auth=auth, token=token or None, base_url=config.get("api_url", "https://api.github.com")
    )


def _direct_gateway(cfg: dict[str, Any]) -> Any:
    """The "direct" gateway counts nothing and caps nothing: forbidden in production.

    It returns the key it was given. No virtual key per run, so no hard ceiling, and the cost
    ledger stays at zero: the bench ran like this for a week and "proved" a spend ceiling that had
    never measured any spend (BLOCKERS, S12). On a serious environment, this is not a
    configuration: it is accounting switched off without anyone having decided it. So building it
    is refused, naming the way out, as `garde.yaml` refuses an embedded dependency in staging and
    prod.
    """
    from .gateway.direct import DirectGateway  # a lazy import, like the others'

    environment = _env("CHOREGOS_ENV", "dev")
    if environment in {"staging", "prod"}:
        raise ConfigurationError(
            f"connector `gateway: direct` refused in {environment}: this gateway measures no "
            "cost and applies no ceiling. Use `litellm`, which issues a virtual key capped per run."
        )
    return DirectGateway(
        key=cfg.get("key", _env("CHOREGOS_GATEWAY_DIRECT_KEY", "")),
        models=cfg.get("models", []),
    )


def _object_schema(proprietes: dict[str, Any], requis: tuple[str, ...] = ()) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": proprietes}
    if requis:
        schema["required"] = list(requis)
    return schema


_TEXT: dict[str, Any] = {"type": "string"}
_INTEGER: dict[str, Any] = {"type": "integer"}

#: What every `web_search` type configures beside its key (ADR 0083).
#: The setting of a type that pools a secret field (ADR 0089): how its keys are used in turn.
_KEY_ROTATION: dict[str, Any] = {
    "key_rotation": {
        "enum": ["failover", "round_robin"],
        "default": "failover",
        "description": "with several keys (`api_key.2` …): `failover` uses the first that answers, "
        "`round_robin` spreads the calls",
    }
}
_WEB_SEARCH_CONFIG: dict[str, Any] = {
    "timeout_s": {"type": "number", "default": 20},
    "price_usd_per_query": {
        "type": "number",
        "minimum": 0,
        "default": 0,
        "description": "what one query costs under the organisation's plan, written to the ledger",
    },
    "requests_per_minute": {
        "type": "integer",
        "minimum": 1,
        "default": 30,
        "description": "the organisation's queries to this backend per minute; beyond, a 429",
    },
    "data_handling": {
        "type": "object",
        "additionalProperties": False,
        "description": "a stricter declaration than the backend's, from the organisation's contract "
        "(`terms_ref` required)",
        "properties": {
            "query_retention_days": {"type": "integer", "minimum": 0},
            "query_reuse": {"type": "boolean"},
            "region": _TEXT,
            "terms_ref": _TEXT,
        },
    },
}

from .families import FAMILIES as _FAMILIES  # noqa: E402 - after the registry
from .families import OPERATIONS_MARKETDATA as _OPERATIONS_MARKETDATA  # noqa: E402 - after the registry
from .identity.entra import OPERATIONS_ENTRA as _OPERATIONS_ENTRA  # noqa: E402 - after the registry
from .netops.cilium import CiliumNetwork as _CiliumNetwork  # noqa: E402 - after the registry
from .netops.nginx import NginxConfig as _NginxConfig  # noqa: E402 - after the registry
from .netops.pfsense import PfSenseFirewall as _PfSenseFirewall  # noqa: E402 - after the registry

_PLUGIN_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def _check_plugin_tracker(cfg: dict[str, Any]) -> None:
    """A project's `tracker/plugin` names a plugin and its server, nothing else: where the server
    answers and its token are the platform's, injected when the adapter is built, never typed."""
    extra = sorted(set(cfg) - {"plugin", "server"})
    if extra:
        raise ConfigurationError(f"a plugin tracker is configured by `plugin` and `server` only, not {extra}")
    for field in ("plugin", "server"):
        if not _PLUGIN_NAME.match(str(cfg.get(field) or "")):
            raise ConfigurationError(f"a plugin tracker needs `{field}`, a plugin or server name")


#: The configuration of a `demo` type: nothing, or the address of the process that serves the fakes.
_DEMO_CONFIGURATION = _object_schema(
    {
        "url": {
            "type": "string",
            "description": "the MCP endpoint of the single process serving the fakes "
            "(`choregos_adapters.fakes.server`); empty: in this process",
        }
    }
)

#: What each shipped type declares (ADR 0034): its name, its capabilities, the schema of its
#: configuration (the one the console draws its form from) and its SECRET fields, which are only
#: written as a reference. These schemas used to live in the API's router, duplicating the
#: registry and disagreeing with it (jira was "unavailable" there while it is registered).
_SHIPPED_SPECS: dict[tuple[str, str], ConnectorTypeSpec] = {
    ("tracker", "github-issues"): ConnectorTypeSpec(
        "GitHub Issues + Projects v2",
        ("tracker",),
        _object_schema(
            {
                "repo": {
                    "type": "string",
                    "format": "github-repo",
                    "description": "owner/repo, picked among the repositories the App can see",
                },
                "project_number": _INTEGER,
                "org": _TEXT,
            },
            ("repo",),
        ),
        ("webhook_secret",),
    ),
    ("tracker", "internal"): ConnectorTypeSpec("internal (Choregos holds the work items)", ("tracker",)),
    ("tracker", "jira"): ConnectorTypeSpec(
        "Jira Cloud",
        ("tracker",),
        _object_schema(
            {
                "base_url": {
                    "type": "string",
                    "description": "the site, https://<site>.atlassian.net, even with a scoped token",
                },
                "email": {"type": "string", "description": "the account the API token belongs to"},
                "project_key": _TEXT,
                "auth": {
                    "type": "string",
                    "enum": ["basic", "scoped"],
                    "default": "basic",
                    "description": "`scoped` for a scoped API token (a service account's): it is called "
                    "through api.atlassian.com",
                },
                "cloud_id": {
                    "type": "string",
                    "description": "scoped tokens only, read from the site if empty",
                },
                "candidates_jql": {
                    "type": "string",
                    "description": "JQL of the issues to take on without a webhook (polled every minute, "
                    "within the project); empty: the issues labelled agent-ready",
                },
                "field_names": {"type": "object", "additionalProperties": {"type": "string"}},
            },
            ("base_url", "email", "project_key"),
        ),
        ("api_token", "webhook_secret"),
        egress=_atlassian_egress,
    ),
    ("tracker", "plugin"): ConnectorTypeSpec(
        "a plugin's server (tracker profile, S43-07)",
        ("tracker",),
        _object_schema(
            {
                "plugin": {"type": "string", "description": "the installed plugin"},
                "server": {"type": "string", "description": "its server that `implements: [tracker]`"},
            },
            ("plugin", "server"),
        ),
        check_config=_check_plugin_tracker,
    ),
    ("tracker", "gitlab-issues"): ConnectorTypeSpec(
        "GitLab Issues",
        ("tracker",),
        _object_schema(
            {"base_url": {"type": "string", "default": "https://gitlab.com"}, "project": _TEXT}, ("project",)
        ),
        ("token", "webhook_secret"),
    ),
    ("scm", "github"): ConnectorTypeSpec(
        "GitHub (App choregos-bot)",
        ("scm",),
        _object_schema(
            {
                "repo": {
                    "type": "string",
                    "format": "github-repo",
                    "description": "owner/repo, picked among the repositories the App can see",
                },
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
        _object_schema({"namespace": _TEXT, "pipeline": {"type": "string", "default": "choregos-ci"}}),
    ),
    ("cd", "argocd"): ConnectorTypeSpec(
        "Argo CD + Rollouts",
        ("cd",),
        _object_schema(
            {
                "gitops_repo": _TEXT,
                "base_url": _TEXT,
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
        _object_schema(
            {"pipeline": {"type": "string", "default": "choregos-agent"}, "service_account": _TEXT}
        ),
    ),
    ("runtime", "k8s_job"): ConnectorTypeSpec(
        "Kubernetes Job",
        ("runtime",),
        _object_schema(
            {
                "service_account": _TEXT,
                "cpu_limit": _TEXT,
                "memory_limit": _TEXT,
                "max_active": _INTEGER,
                "env_from_secrets": {"type": "array", "items": _TEXT},
            }
        ),
    ),
    ("runtime", "agent_sandbox"): ConnectorTypeSpec(
        "kubernetes-sigs/agent-sandbox (warm pool, opt-in)",
        ("runtime",),
        _object_schema(
            {
                "warm_pool": {"type": "string", "default": "choregos-runner"},
                "assignment_port": _INTEGER,
                "runtime_class": _TEXT,
                "pool_image": _TEXT,
                "ttl_seconds": _INTEGER,
                "max_active": _INTEGER,
                "env_from_secrets": {"type": "array", "items": _TEXT},
            }
        ),
    ),
    ("runtime", "aca"): ConnectorTypeSpec(
        "Azure Container Apps jobs",
        ("runtime",),
        _object_schema(
            {
                "subscription_id": _TEXT,
                "resource_group": _TEXT,
                "tenant_id": _TEXT,
                "client_id": _TEXT,
                "environment_id": _TEXT,
                "location": {"type": "string", "default": "westeurope"},
                "identity_id": _TEXT,
                "registry_server": _TEXT,
                "log_analytics_workspace_id": _TEXT,
            },
            ("subscription_id", "resource_group", "environment_id"),
        ),
        ("client_secret", "token"),
    ),
    ("runtime", "local_docker"): ConnectorTypeSpec(
        "local Docker (development)", ("runtime",), _object_schema({"network": _TEXT})
    ),
    ("memory", "ecphoria"): ConnectorTypeSpec(
        "Ecphoria (memory and knowledge base)",
        ("memory",),
        _object_schema({"base_url": _TEXT, "tenant": _TEXT, "read_timeout_ms": _INTEGER}),
        ("token",),
    ),
    ("memory", "lexical"): ConnectorTypeSpec("lexical (fallback, no service)", ("memory",)),
    ("memory", "pgvector"): ConnectorTypeSpec(
        "pgvector (deprecated alias of lexical)", ("memory",), deprecated=True
    ),
    ("gateway", "direct"): ConnectorTypeSpec(
        "direct (development only: no cost measured, no cap)",
        ("gateway",),
        _object_schema({"models": {"type": "array", "items": _TEXT}}),
        ("key",),
    ),
    ("gateway", "litellm"): ConnectorTypeSpec(
        "LiteLLM",
        ("gateway",),
        _object_schema(
            {
                "base_url": _TEXT,
                "team_id": _TEXT,
                "internal_prices": {"type": "object", "additionalProperties": {"type": "string"}},
                "enterprise_tags": {"type": "boolean", "default": False},
            }
        ),
        ("master_key",),
    ),
    ("gateway", "openai-compatible"): ConnectorTypeSpec(
        "OpenAI-compatible gateway (discovery only: no virtual keys, no cap, no measured cost)",
        ("gateway",),
        _object_schema(
            {
                "base_url": _TEXT,
                "headers": {"type": "object", "additionalProperties": {"type": "string"}},
                "timeout_s": _INTEGER,
            }
        ),
        ("api_key", "run_key"),
    ),
    # Knowledge base sources (S33-04, ADR 0051): one Entra application holding `Sites.Selected`,
    # granted `read` on the chosen sites only — never `Files.Read.All` nor `Sites.Read.All`.
    ("documents", "sharepoint"): ConnectorTypeSpec(
        "Microsoft SharePoint and OneDrive (Graph, Sites.Selected)",
        ("documents",),
        _object_schema(
            {
                "tenant_id": _TEXT,
                "client_id": _TEXT,
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
        _object_schema(
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
        egress=_atlassian_egress,
    ),
    # A private git repository as a knowledge source (S39-18b): a token, or a GitHub App's short-lived
    # installation token. The credential is pinned to `host`.
    ("documents", "git"): ConnectorTypeSpec(
        "Git over https (token or GitHub App)",
        ("documents",),
        _object_schema(
            {
                "host": {"type": "string", "default": "github.com"},
                "auth": {"type": "string", "enum": ["token", "github_app"], "default": "token"},
                "app_id": _TEXT,
                "installation_id": {"type": "integer"},
                "api_url": {"type": "string", "default": "https://api.github.com"},
            }
        ),
        ("token", "private_key"),
    ),
    # A web site behind a login as a knowledge source (S47-48): a bearer token, HTTP Basic or one named
    # header, pinned to `host`.
    ("documents", "web"): ConnectorTypeSpec(
        "Web site behind a login (bearer, basic or header)",
        ("documents",),
        _object_schema(
            {
                "host": {
                    "type": "string",
                    "description": "the site's host; the credential is sent nowhere else",
                },
                "auth": {"type": "string", "enum": ["bearer", "basic", "header"], "default": "bearer"},
                "username": {"type": "string", "description": "auth basic: the user name"},
                "header_name": {
                    "type": "string",
                    "description": "auth header: the header the secret goes in (X-API-Key, Cookie)",
                },
                "headers": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "plain headers sent with every request, e.g. Accept-Language",
                },
            },
            ("host",),
        ),
        ("secret",),
        egress=lambda config: [(str(config.get("host") or ""), 443)] if config.get("host") else [],
        check_config=_web_documents_check,
    ),
    ("identity", "entra"): ConnectorTypeSpec(
        "Microsoft Entra ID (Graph)",
        ("identity",),
        _object_schema(
            {
                "tenant_id": _TEXT,
                "client_id": _TEXT,
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
            OperationSpec(name, access, description, schema)
            for name, access, description, schema in _OPERATIONS_ENTRA
        ),
    ),
    # The business families (S20-04) have no real type yet: `demo` holds them in memory, each
    # process for itself: a demonstration, refused in staging and prod.
    ("mdm", "demo"): ConnectorTypeSpec(
        "demo device management (in memory: demonstrations only, refused in staging and prod)",
        ("mdm",),
        _DEMO_CONFIGURATION,
        secret_fields=("api_key",),
        operations=_FAMILIES["mdm"],
        dev_only=True,
    ),
    ("shipping", "demo"): ConnectorTypeSpec(
        "demo carrier (in memory: demonstrations only, refused in staging and prod)",
        ("shipping",),
        _DEMO_CONFIGURATION,
        secret_fields=("api_key",),
        operations=_FAMILIES["shipping"],
        dev_only=True,
    ),
    ("access_control", "demo"): ConnectorTypeSpec(
        "demo badge readers (in memory: demonstrations only, refused in staging and prod)",
        ("access_control",),
        _DEMO_CONFIGURATION,
        secret_fields=("api_key",),
        operations=_FAMILIES["access_control"],
        dev_only=True,
    ),
    # The `marketdata` family (S38-03): prices from free sources, read by the platform for a run whose
    # sandbox has no egress. Keys stay API-side (`secret_refs`).
    ("marketdata", "stooq"): ConnectorTypeSpec(
        "Stooq (daily CSV, free)",
        ("marketdata",),
        _object_schema(
            {
                "suffix_map": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": 'ticker suffix -> Stooq market suffix, e.g. {"": ".us", ".L": ".uk"}',
                },
                "timeout_s": {"type": "number", "default": 20},
                **_KEY_ROTATION,
            }
        ),
        ("api_key",),
        operations=_OPERATIONS_MARKETDATA,
        pooled_fields=("api_key",),
    ),
    ("marketdata", "yahoo"): ConnectorTypeSpec(
        "Yahoo Finance chart (unofficial: personal use only, may break)",
        ("marketdata",),
        _object_schema({"timeout_s": {"type": "number", "default": 20}}),
        operations=_OPERATIONS_MARKETDATA,
    ),
    ("marketdata", "alphavantage"): ConnectorTypeSpec(
        "Alpha Vantage (free key: about 25 requests a day, unadjusted daily bars)",
        ("marketdata",),
        _object_schema(
            {
                "timeout_s": {"type": "number", "default": 20},
                "outputsize": {
                    "enum": ["auto", "compact"],
                    "default": "auto",
                    "description": "`compact`: always the last 100 trading days (free key); cross-check only",
                },
                **_KEY_ROTATION,
            }
        ),
        ("api_key",),
        operations=_OPERATIONS_MARKETDATA,
        pooled_fields=("api_key",),
    ),
    ("marketdata", "tiingo"): ConnectorTypeSpec(
        "Tiingo (free key: 30+ years of daily history, internal and personal use only)",
        ("marketdata",),
        _object_schema({"timeout_s": {"type": "number", "default": 20}}),
        ("api_key",),
        operations=_OPERATIONS_MARKETDATA,
    ),
    ("marketdata", "composite"): ConnectorTypeSpec(
        "market data from several sources, with fallback and a cross-check of the closes",
        ("marketdata",),
        _object_schema(
            {
                "sources": {
                    "type": "array",
                    "items": {"enum": ["stooq", "yahoo", "alphavantage", "tiingo"]},
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
                **_KEY_ROTATION,
            },
            ("sources",),
        ),
        ("stooq_api_key", "alphavantage_api_key", "tiingo_api_key"),
        operations=_OPERATIONS_MARKETDATA,
        pooled_fields=("stooq_api_key", "alphavantage_api_key"),
    ),
    # The `web_search` family (S40-01g, ADR 0083): called by the API's built-in `web_search`, never by a
    # runner. Each type declares what it does with the queries (`choregos_adapters.web_search`); the
    # organisation may only make that stricter, naming its terms.
    ("web_search", "brave"): ConnectorTypeSpec(
        "Brave Search API",
        ("web_search",),
        _object_schema({**_WEB_SEARCH_CONFIG, **_KEY_ROTATION}),
        ("api_key",),
        egress=lambda _cfg: [("api.search.brave.com", 443)],
        pooled_fields=("api_key",),
    ),
    ("web_search", "tavily"): ConnectorTypeSpec(
        "Tavily search (links and snippets only)",
        ("web_search",),
        _object_schema({**_WEB_SEARCH_CONFIG, **_KEY_ROTATION}),
        ("api_key",),
        egress=lambda _cfg: [("api.tavily.com", 443)],
        pooled_fields=("api_key",),
    ),
    ("web_search", "firecrawl"): ConnectorTypeSpec(
        "Firecrawl search, self-hosted only (no scraping)",
        ("web_search",),
        _object_schema(
            {
                "base_url": {
                    "type": "string",
                    "description": "the address of your self-hosted Firecrawl instance, required; "
                    "Firecrawl's cloud (firecrawl.dev) is refused",
                },
                **_WEB_SEARCH_CONFIG,
            },
            ("base_url",),
        ),
        ("api_key",),
        egress=_firecrawl_egress,
        check_config=_firecrawl_check,
    ),
    # A demonstration tenant's train (S21-24): an in-memory environment, or served by the fakes'
    # process. No operation exposed: it is the train that calls it, never an agent.
    ("cd", "demo"): ConnectorTypeSpec(
        "demo deployment (in memory: demonstrations only, refused in staging and prod)",
        ("cd",),
        _DEMO_CONFIGURATION,
        secret_fields=("api_key",),
        dev_only=True,
    ),
    # Netops (ADR 0052, S33-08): our own estate, read through the platform.
    ("firewall", "pfsense"): ConnectorTypeSpec(
        "pfSense (REST API package, v2)",
        ("firewall",),
        _object_schema(
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
        _object_schema(
            {
                "kubernetes_url": {"type": "string", "description": "the cluster's API server, https"},
                "verify_tls": {"type": "boolean", "default": True},
                "ca_cert": {"type": "string", "description": "PEM of the cluster's CA"},
                "hubble_server": {"type": "string", "default": "hubble-relay.kube-system.svc:443"},
                "hubble_tls": {"type": "boolean", "default": True},
                "hubble_ca_cert": {"type": "string", "description": "PEM of Hubble Relay's CA"},
                "hubble_tls_server_name": _TEXT,
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
        _object_schema(
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
        _object_schema(
            {
                "url": {"type": "string", "description": "the server's MCP endpoint, https"},
                "auth_scheme": {
                    "type": "string",
                    "enum": ["bearer", "basic"],
                    "default": "bearer",
                    "description": "how the token is sent: Bearer, or Basic base64(username:token)",
                },
                "username": {"type": "string", "description": "the account, for `basic` (an email)"},
                "auth_header": {
                    "type": "string",
                    "default": "Authorization",
                    "description": "the header the token travels in: `Authorization` sends a Bearer, "
                    "another name (`X-API-Key`) sends the raw token",
                },
                "timeout_s": {"type": "number", "default": 30},
            },
            ("url",),
        ),
        ("token",),
    ),
    ("mcp", "mcp-gateway"): ConnectorTypeSpec(
        "MCP server through the organisation's MCP gateway",
        ("mcp",),
        _object_schema(
            {
                "server": {
                    "type": "string",
                    "pattern": GATEWAY_SERVER_PATTERN,
                    "description": "the server's name at the organisation's MCP gateway",
                },
                "timeout_s": {
                    "type": "number",
                    "minimum": 1,
                    "maximum": 120,
                    "description": "seconds before a call is abandoned; absent: the gateway's timeout",
                },
            },
            ("server",),
        ),
        (),
        egress=lambda config: [],
        check_config=_mcp_gateway_check,
    ),
    ("notify", "slack"): ConnectorTypeSpec(
        "Slack",
        ("notify",),
        _object_schema({"channel": {"type": "string", "default": "#choregos"}, "public_url": _TEXT}),
        ("webhook_url", "bot_token"),
    ),
}


#: The fake MCP server of CHOREGOS_FAKES=1: one, scripted by the tests and the demo.
FAKE_MCP: Any = None


def _current_fake_mcp() -> Any:
    from .fakes.mcp import FakeMcpServer

    global FAKE_MCP  # noqa: PLW0603 - a shared fake, like the platform's other fakes
    if FAKE_MCP is None:
        FAKE_MCP = FakeMcpServer()
    return FAKE_MCP


#: The fake MCP gateway of CHOREGOS_FAKES=1: one, whose fallback is always the CURRENT `FAKE_MCP`.
FAKE_MCP_GATEWAY: Any = None


def fake_mcp_gateway() -> Any:
    from .fakes.mcp_gateway import FakeMcpGateway

    global FAKE_MCP_GATEWAY  # noqa: PLW0603 - a shared fake, like the platform's other fakes
    if FAKE_MCP_GATEWAY is None:
        # The function itself, not its result: tests replace `FAKE_MCP`, and the gateway follows.
        FAKE_MCP_GATEWAY = FakeMcpGateway(auth="none", fallback=_current_fake_mcp)
    return FAKE_MCP_GATEWAY


def _gateway_client(cfg: dict[str, Any], *, transport: Any) -> Any:
    from .mcp import McpClient

    return McpClient(
        str(cfg["gateway_url"]),
        headers=dict(cfg.get("gateway_headers") or {}),
        headers_provider=cfg.get("headers_provider"),
        timeout_s=float(cfg.get("timeout_s", 30)),
        transport=transport,
        user_agent=str(cfg.get("user_agent", "")),
    )


def _fake_mcp_client(cfg: dict[str, Any]) -> Any:
    from .mcp import McpClient

    if cfg.get("route") == "gateway":
        _gateway_server_check(cfg.get("server"))
        if not cfg.get("gateway_url"):
            raise AdapterError(_GATEWAY_ONLY_PLATFORM)
        return _gateway_client(cfg, transport=fake_mcp_gateway().transport())
    if "server" in cfg:
        raise AdapterError(_GATEWAY_ONLY_PLATFORM)
    return McpClient(
        cfg.get("url", "http://fake-mcp.test/mcp"),
        token=cfg.get("token", ""),
        auth_scheme=cfg.get("auth_scheme", "bearer"),
        username=cfg.get("username", ""),
        auth_header=cfg.get("auth_header", "Authorization"),
        transport=_current_fake_mcp().transport(),
        user_agent=str(cfg.get("user_agent", "")),
    )


def _mcp_client(cfg: dict[str, Any]) -> Any:
    from .mcp import McpClient

    return McpClient(
        cfg["url"],
        token=cfg.get("token", ""),
        auth_scheme=cfg.get("auth_scheme", "bearer"),
        username=cfg.get("username", ""),
        auth_header=cfg.get("auth_header", "Authorization"),
        timeout_s=float(cfg.get("timeout_s", 30)),
        user_agent=str(cfg.get("user_agent", "")),
    )


def _mcp_gateway_client(cfg: dict[str, Any]) -> Any:
    _gateway_server_check(cfg.get("server"))
    if cfg.get("route") != "gateway" or not cfg.get("gateway_url"):
        raise AdapterError(_GATEWAY_ONLY_PLATFORM)
    return _gateway_client(cfg, transport=cfg.get("transport"))


#: The fakes' Graph: a single one, scripted by the tests and the demo.
FAKE_ENTRA: Any = None


def _fake_entra_directory(cfg: dict[str, Any]) -> Any:
    from .fakes.entra import FakeEntra
    from .identity import EntraIdentity

    global FAKE_ENTRA  # noqa: PLW0603 - a shared fake, like the platform's other fakes
    if FAKE_ENTRA is None:
        FAKE_ENTRA = FakeEntra()
    return EntraIdentity(
        tenant_id=cfg.get("tenant_id", "acme"),
        client_id=cfg.get("client_id", "choregos"),
        client_secret=cfg.get("client_secret", "faux"),
        administrative_unit_id=cfg.get("administrative_unit_id", FAKE_ENTRA.unit),
        transport=FAKE_ENTRA.transport(),
    )


#: The fakes of the business families, one per family: shared in the process, like the others.
FAUX_METIER: dict[str, Any] = {}  # lang: fr-ok (not renamed by S45-10: the business fakes keep their name)


def _demo_desk(family: str, *, refuse_in_production: bool) -> Any:
    """The factory of a `<family>: demo` connector (or of its fake, under `CHOREGOS_FAKES=1`)."""

    def construct(cfg: dict[str, Any]) -> Any:
        from .fakes.hr import FAKES_BY_FAMILY, Desk

        environment = _env("CHOREGOS_ENV", "dev")
        if refuse_in_production and environment in {"staging", "prod"}:
            raise ConfigurationError(
                f"connector `{family}: demo` refused in {environment}: an in-memory fake, which "
                "each process holds for itself: a demonstration, not a company system."
            )
        if refuse_in_production and cfg.get("url"):
            # Served by a single process (`choregos_adapters.fakes.server`): where the API and
            # the orchestrator are two processes, an in-memory fake would show them two states.
            from .fakes.hr import RemoteDesk
            from .mcp import McpClient

            return RemoteDesk(McpClient(str(cfg["url"]), token=str(cfg.get("api_key", ""))))
        if family not in FAUX_METIER:
            FAUX_METIER[family] = FAKES_BY_FAMILY[family]()
        return Desk(FAUX_METIER[family], str(cfg.get("api_key", "")))

    return construct


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
FAKE_MARKETDATA: Any = None


def _fake_marketdata(cfg: dict[str, Any]) -> Any:
    global FAKE_MARKETDATA  # noqa: PLW0603 - a shared fake, like the platform's other fakes
    if FAKE_MARKETDATA is None:
        from .fakes.marketdata import FakeMarketData

        FAKE_MARKETDATA = FakeMarketData()
    return FAKE_MARKETDATA


#: The fake of the `web_search` family, shared in the process (scriptable by tests).
FAKE_WEB_SEARCH: Any = None


def _fake_web_search(cfg: dict[str, Any]) -> Any:
    global FAKE_WEB_SEARCH  # noqa: PLW0603 - a shared fake, like the platform's other fakes
    if FAKE_WEB_SEARCH is None:
        from .fakes.web_search import FakeSearchBackend

        FAKE_WEB_SEARCH = FakeSearchBackend()
    return FAKE_WEB_SEARCH


def _demo_cd(cfg: dict[str, Any]) -> Any:
    """The factory of `cd: demo`: refused in staging and prod; served when it carries a `url`."""
    from .fakes.demo_cd import DemoCd, FakeDemoCd
    from .fakes.hr import Desk, RemoteDesk

    environment = _env("CHOREGOS_ENV", "dev")
    if environment in {"staging", "prod"}:
        raise ConfigurationError(
            f"connector `cd: demo` refused in {environment}: a simulated environment, a "
            "demonstration, not a deployment."
        )
    if cfg.get("url"):
        from .mcp import McpClient

        return DemoCd(RemoteDesk(McpClient(str(cfg["url"]), token=str(cfg.get("api_key", "")))))
    if "cd" not in FAUX_METIER:
        FAUX_METIER["cd"] = FakeDemoCd()
    return DemoCd(Desk(FAUX_METIER["cd"], str(cfg.get("api_key", ""))))


def _register_marketdata() -> None:
    """The `marketdata` family (S38-03): its free providers, their composite and its fake."""
    from .marketdata import PROVIDERS, CompositeProvider, MarketDataProvider

    def _provider(type_name: str, cfg: dict[str, Any], key: str | list[str] = "") -> MarketDataProvider:
        options: dict[str, Any] = {"timeout_s": float(cfg.get("timeout_s", 20))}
        if type_name == "stooq" and cfg.get("suffix_map"):
            options["suffix_map"] = dict(cfg["suffix_map"])
        if type_name == "alphavantage" and cfg.get("outputsize"):
            options["outputsize"] = str(cfg["outputsize"])
        if type_name != "yahoo":
            # A string, or a list when the field holds several keys (ADR 0089).
            options["api_key"] = key or cfg.get("api_key") or ""
            options["key_rotation"] = strategy_of(cfg.get("key_rotation"))
        return PROVIDERS[type_name](**options)

    def _composite(cfg: dict[str, Any]) -> CompositeProvider:
        names = [str(n) for n in cfg.get("sources") or []]
        unknown = [n for n in names if n not in PROVIDERS]
        if not names or unknown:
            raise ConfigurationError(
                f"marketdata composite: `sources` must list provider types among {sorted(PROVIDERS)}"
                + (f" (unknown: {unknown})" if unknown else "")
            )
        configs = cfg.get("source_configs") or {}
        return CompositeProvider(
            [
                (
                    n,
                    _provider(
                        n,
                        {"key_rotation": cfg.get("key_rotation"), **dict(configs.get(n) or {})},
                        cfg.get(f"{n}_api_key") or "",
                    ),
                )
                for n in names
            ],
            cross_check=bool(cfg.get("cross_check", True)),
            tolerance=float(cfg.get("tolerance", 0.02)),
        )

    def _constructor(name: str) -> Callable[[dict[str, Any]], MarketDataProvider]:
        return lambda cfg: _provider(name, cfg)

    for name in PROVIDERS:
        register("marketdata", name, _SHIPPED_SPECS[("marketdata", name)])(_constructor(name))
    register("marketdata", "composite", _SHIPPED_SPECS[("marketdata", "composite")])(_composite)
    register("marketdata", "fake")(_fake_marketdata)


def _register_web_search() -> None:
    """The `web_search` family (S40-01g): its backends and its fake."""
    from .web_search import BACKENDS, SearchBackend

    def _constructor(name: str) -> Callable[[dict[str, Any]], SearchBackend]:
        def construct(cfg: dict[str, Any]) -> SearchBackend:
            options: dict[str, Any] = {
                # A string, or a list when the field holds several keys (ADR 0089).
                "api_key": cfg.get("api_key") or "",
                "key_rotation": strategy_of(cfg.get("key_rotation")),
                "timeout_s": float(cfg.get("timeout_s", 20)),
            }
            if name == "firecrawl":
                # No default: an absent base_url is refused by the adapter, never replaced by the cloud.
                options["base_url"] = str(cfg.get("base_url") or "")
            return BACKENDS[name](**options)

        return construct

    for name in BACKENDS:
        register("web_search", name, _SHIPPED_SPECS[("web_search", name)])(_constructor(name))
    register("web_search", "fake")(_fake_web_search)


def _plugin_tracker(cfg: dict[str, Any]) -> Any:
    """`tracker/plugin`: built from what the platform injects (`url`, `token`, `audit`, read from the
    organisation's connector of the server), never from typed values (S43-07)."""
    from .plugin_servers import PluginServerClient
    from .tracker.plugin import PluginTracker

    _check_plugin_tracker({k: v for k, v in cfg.items() if k in {"plugin", "server"}})
    if not cfg.get("url") or not cfg.get("token"):
        raise ConfigurationError(
            f"the plugin tracker `{cfg.get('plugin')}/{cfg.get('server')}` is built by the platform "
            "from the organisation's connector of that server, which it could not read"
        )
    client = PluginServerClient(str(cfg["url"]), token=str(cfg["token"]), transport=cfg.get("transport"))
    return PluginTracker(client, str(cfg["plugin"]), str(cfg["server"]), audit=cfg.get("audit"))


def _register_builtins() -> None:
    """Registers the shipped implementations (a lazy import to avoid cycles)."""
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
    register("mcp", "fake")(_fake_mcp_client)
    register("identity", "fake")(_fake_entra_directory)
    from .fakes.documents import FakeDocumentsConnector

    register("documents", "fake")(FakeDocumentsConnector)
    for family in _FAMILIES:
        register(family, "fake")(_demo_desk(family, refuse_in_production=False))
    register("firewall", "fake")(_fake_pfsense)
    register("network_policy", "fake")(_fake_cilium)
    register("web_server", "fake")(_fake_nginx)

    # ───────────────── real implementations (day 1) ─────────────────
    from .cd.argocd import ArgoCdAdapter
    from .ci.tekton import TektonCi
    from .executor.aca import AcaExecutor, AzureArmClient
    from .executor.agent_sandbox import DEFAULT_TTL_SECONDS, AgentSandboxExecutor
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

    register("tracker", "github-issues", _SHIPPED_SPECS[("tracker", "github-issues")])(
        lambda cfg: GitHubTracker(
            _github_client(cfg),
            cfg["repo"],
            project_number=cfg.get("project_number"),
            org=cfg.get("org"),
            webhook_secret=cfg.get("webhook_secret", ""),
        )
    )
    register("tracker", "internal", _SHIPPED_SPECS[("tracker", "internal")])(lambda cfg: InternalTracker())
    register("tracker", "jira", _SHIPPED_SPECS[("tracker", "jira")])(_jira_tracker)
    register("tracker", "plugin", _SHIPPED_SPECS[("tracker", "plugin")])(_plugin_tracker)
    register("tracker", "gitlab-issues", _SHIPPED_SPECS[("tracker", "gitlab-issues")])(
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
    register("scm", "github", _SHIPPED_SPECS[("scm", "github")])(
        lambda cfg: GitHubScm(
            _github_client(cfg),
            default_repo=cfg.get("repo", ""),
            merge_method=cfg.get("merge_method", "SQUASH"),
            use_merge_queue=cfg.get("merge_queue", True),
        )
    )
    register("ci", "tekton", _SHIPPED_SPECS[("ci", "tekton")])(
        lambda cfg: TektonCi(
            KubernetesClient(**cfg.get("kubernetes", {})),
            namespace=cfg.get("namespace", "default"),
            pipeline=cfg.get("pipeline", "choregos-ci"),
        )
    )
    register("cd", "argocd", _SHIPPED_SPECS[("cd", "argocd")])(
        lambda cfg: ArgoCdAdapter(
            base_url=cfg.get("base_url", "http://argocd-server.argocd"),
            token=cfg.get("token", ""),
            gitops_repo=cfg.get("gitops_repo", ""),
            github=_github_client(cfg),
            app_pattern=cfg.get("app_pattern", "{app}-{env}"),
            path_prefix=cfg.get("path_prefix", "apps"),
        )
    )
    register("runtime", "tekton", _SHIPPED_SPECS[("runtime", "tekton")])(
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
            # How many runs run at the same time in the namespace. 0 = no ceiling.
            max_active=int(cfg.get("max_active", os.environ.get("CHOREGOS_RUNNER_MAX_ACTIVE", "") or 0)),
            # The chart's `global.imagePullSecrets` (S51-09): a step's own image may be private.
            image_pull_secrets=cfg.get("image_pull_secrets")
            or _names(os.environ.get("CHOREGOS_RUNNER_IMAGE_PULL_SECRETS", "")),
        )
    )
    register("runtime", "agent_sandbox", _SHIPPED_SPECS[("runtime", "agent_sandbox")])(
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
            # How long a finished sandbox keeps its volume (`runner.sandbox.ttlSecondsAfterFinished`).
            ttl_seconds=int(
                cfg.get(
                    "ttl_seconds", os.environ.get("CHOREGOS_SANDBOX_TTL_SECONDS", "") or DEFAULT_TTL_SECONDS
                )
            ),
        )
    )
    register("runtime", "aca", _SHIPPED_SPECS[("runtime", "aca")])(
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
    register("runtime", "local_docker", _SHIPPED_SPECS[("runtime", "local_docker")])(
        lambda cfg: LocalDockerExecutor(network=cfg.get("network", "choregos_default"))
    )
    register("memory", "ecphoria", _SHIPPED_SPECS[("memory", "ecphoria")])(
        lambda cfg: EcphoriaMemory(
            # Like the gateway: the deployment's URL and token, unless overridden per project.
            # `CHOREGOS_MEMORY_URL` was set by the chart and ignored here.
            cfg.get("base_url", _env("CHOREGOS_MEMORY_URL", "http://ecphoria.choregos-memory:8432")),
            token=cfg.get("token", _env("CHOREGOS_MEMORY_TOKEN", "")),
            tenant=cfg.get("tenant"),
            read_timeout_ms=int(cfg.get("read_timeout_ms", 300)),
        )
    )
    # `pgvector`: a deprecated alias of `lexical`: the name promised vectors, the code does a
    # lexical similarity. Kept for the projects already configured.
    register("memory", "lexical", _SHIPPED_SPECS[("memory", "lexical")])(lambda cfg: LexicalMemory())
    register("memory", "pgvector", _SHIPPED_SPECS[("memory", "pgvector")])(lambda cfg: LexicalMemory())
    register("gateway", "direct", _SHIPPED_SPECS[("gateway", "direct")])(_direct_gateway)
    register("gateway", "litellm", _SHIPPED_SPECS[("gateway", "litellm")])(
        lambda cfg: LiteLlmGateway(
            cfg.get("base_url", _env("CHOREGOS_GATEWAY_URL", "http://litellm.choregos-gateway:4000")),
            cfg.get("master_key", _env("CHOREGOS_GATEWAY_MASTER_KEY", "")),
            team_id=cfg.get("team_id"),
            internal_prices=cfg.get("internal_prices", {}),
            enterprise_tags=cfg.get("enterprise_tags", False),
        )
    )
    from .gateway.openai_compatible import OpenAiCompatibleGateway

    register("gateway", "openai-compatible", _SHIPPED_SPECS[("gateway", "openai-compatible")])(
        lambda cfg: OpenAiCompatibleGateway(
            cfg.get("base_url", _env("CHOREGOS_GATEWAY_URL", "")),
            api_key=cfg.get("api_key", _env("CHOREGOS_GATEWAY_MASTER_KEY", "")),
            run_key=cfg.get("run_key", ""),
            headers=cfg.get("headers", {}),
            timeout=float(cfg.get("timeout_s", 20)),
        )
    )
    from .documents.graph import GraphDocumentsConnector

    register("documents", "sharepoint", _SHIPPED_SPECS[("documents", "sharepoint")])(GraphDocumentsConnector)
    from .documents.confluence import ConfluenceDocumentsConnector

    register("documents", "confluence", _SHIPPED_SPECS[("documents", "confluence")])(
        ConfluenceDocumentsConnector
    )
    from .documents.git import GitDocumentsConnector

    register("documents", "git", _SHIPPED_SPECS[("documents", "git")])(GitDocumentsConnector)
    from .documents.web import WebDocumentsConnector

    register("documents", "web", _SHIPPED_SPECS[("documents", "web")])(WebDocumentsConnector)
    from .identity import EntraIdentity

    register("identity", "entra", _SHIPPED_SPECS[("identity", "entra")])(
        lambda cfg: EntraIdentity(
            tenant_id=cfg["tenant_id"],
            client_id=cfg["client_id"],
            client_secret=cfg.get("client_secret", ""),
            administrative_unit_id=cfg.get("administrative_unit_id"),
            graph_url=cfg.get("graph_url", "https://graph.microsoft.com/v1.0"),
            login_url=cfg.get("login_url", "https://login.microsoftonline.com"),
        )
    )
    for family in _FAMILIES:
        register(family, "demo", _SHIPPED_SPECS[(family, "demo")])(
            _demo_desk(family, refuse_in_production=True)
        )
    register("cd", "demo", _SHIPPED_SPECS[("cd", "demo")])(_demo_cd)
    from .netops import PfSenseFirewall

    register("firewall", "pfsense", _SHIPPED_SPECS[("firewall", "pfsense")])(PfSenseFirewall.from_config)
    register("network_policy", "cilium", _SHIPPED_SPECS[("network_policy", "cilium")])(_cilium)
    register("web_server", "nginx", _SHIPPED_SPECS[("web_server", "nginx")])(_nginx)

    _register_marketdata()
    _register_web_search()
    # A real MCP server (ADR 0034): its operations are DISCOVERED, and are born closed.
    register("mcp", "mcp", _SHIPPED_SPECS[("mcp", "mcp")])(_mcp_client)
    register("mcp", MCP_GATEWAY_TYPE, _SHIPPED_SPECS[("mcp", MCP_GATEWAY_TYPE)])(_mcp_gateway_client)
    register("notify", "slack", _SHIPPED_SPECS[("notify", "slack")])(
        lambda cfg: SlackNotifier(
            webhook_url=cfg.get("webhook_url", _env("CHOREGOS_SLACK_WEBHOOK", "")),
            bot_token=cfg.get("bot_token", _env("CHOREGOS_SLACK_TOKEN", "")),
            default_channel=cfg.get("channel", "#choregos"),
            public_url=cfg.get("public_url", _env("CHOREGOS_PUBLIC_URL", "")),
        )
    )


#: The entry-point group the platform reads at startup. A package installed alongside (the
#: enterprise edition, an in-house connector) declares itself there and registers itself.
EXTENSION_GROUP = "choregos.plugins"


def load_extensions(group: str = EXTENSION_GROUP) -> list[str]:
    """Loads the extensions declared outside this tree, and returns their names.

    Until 2026-09-26 there was NO seam: `register()` was public, but nothing ever imported a
    third-party module, so an outside package could not register itself unless
    `_register_builtins()` was patched. The playbooks were the only capability truly extensible
    from the outside (`CHOREGOS_PLAYBOOKS_DIR`); that is the model copied here.

    Each entry point designates a callable with no argument, which makes its `register(...)` calls.

    **A declared extension that does not load stops the process.** The same rule as `garde.yaml`
    and as the refusal of an unresolved setting: someone installed it so that it would serve, and
    a silently absent extension leaves a platform that looks complete and is not.
    """
    from importlib import metadata

    loaded: list[str] = []
    for point in metadata.entry_points(group=group):
        try:
            point.load()()
        except Exception as error:
            raise RuntimeError(
                f'extension "{point.name}" ({point.value}): cannot load: {error}. '
                "It is declared, so it must serve; the platform does not start without it."
            ) from error
        loaded.append(point.name)
    return loaded


_register_builtins()

# Former names (ADR 0098 §3 (b), (c)). A name that tests patch goes through `alias_names`, which
# forwards reading, patching and undoing to the English one; the typed aliases are for mypy.
FAUX_ENTRA = FAKE_ENTRA  # lang: fr-ok alias:internal
FAUX_MARKETDATA = FAKE_MARKETDATA  # lang: fr-ok alias:internal
FAUX_WEB_SEARCH = FAKE_WEB_SEARCH  # lang: fr-ok alias:internal
charger_les_greffons = load_extensions  # lang: fr-ok alias:internal
GROUPE_DE_GREFFONS = EXTENSION_GROUP  # lang: fr-ok alias:internal
POLITIQUES = POLICIES  # lang: fr-ok alias:internal
configuration_resolue = resolved_configuration  # lang: fr-ok alias:internal
type_par_defaut = default_type  # lang: fr-ok alias:internal
_SPECS_LIVREES = _SHIPPED_SPECS  # lang: fr-ok alias:internal (tests/procedures reads it)
alias_names(
    __name__,
    {
        "FAUX_ENTRA": "FAKE_ENTRA",
        "FAUX_MARKETDATA": "FAKE_MARKETDATA",
        "FAUX_WEB_SEARCH": "FAKE_WEB_SEARCH",
        "charger_les_greffons": "load_extensions",
        "GROUPE_DE_GREFFONS": "EXTENSION_GROUP",
    },
)
