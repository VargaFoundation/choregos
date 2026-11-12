# SPDX-License-Identifier: Apache-2.0
"""What each shipped connector type declares (ADR 0034): the specs the registry serves.

A spec holds a type's display name, its capabilities, the schema of its configuration (the one the
console draws its form from), its SECRET fields (only ever written as a reference), and the checks
and egress hooks it declares. The factories that build the adapters are in `builtins.py`.
"""

from __future__ import annotations

import re
from typing import Any

from choregos_contracts.outpost import ENDPOINT_ALIAS_PATTERN, OUTPOST_SLUG_PATTERN

from .errors import AdapterError, ConfigurationError
from .families import FAMILIES as _FAMILIES
from .families import OPERATIONS_MARKETDATA as _OPERATIONS_MARKETDATA
from .identity.entra import OPERATIONS_ENTRA as _OPERATIONS_ENTRA
from .netops.cilium import CiliumNetwork as _CiliumNetwork
from .netops.nginx import NginxConfig as _NginxConfig
from .netops.pfsense import PfSenseFirewall as _PfSenseFirewall
from .registry import ConnectorTypeSpec, OperationSpec


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


#: The `mcp-outpost` connector type: a tool server that lives at an outpost (ADR 0149 §1).
MCP_OUTPOST_TYPE = "mcp-outpost"
#: The only keys a stored `mcp-outpost` configuration may hold; every other key is the platform's.
_OUTPOST_STORED_KEYS = ("outpost", "endpoint", "timeout_s")
_OUTPOST_TIMEOUT_S = (1, 120)
_OUTPOST_ONLY_PLATFORM = (
    "an `mcp-outpost` connector is reached through its outpost's rendezvous, which only the platform "
    "supplies: it cannot be built from its configuration alone"
)
#: The declared schema is the contract's `McpOutpostConnectorConfig`, closed: an outpost's name and an
#: endpoint alias, no URL and no secret field (ADR 0147 §7).
_OUTPOST_CONFIGURATION: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["outpost", "endpoint"],
    "properties": {
        "outpost": {
            "type": "string",
            "pattern": OUTPOST_SLUG_PATTERN,
            "description": "an outpost of the same organisation",
        },
        "endpoint": {
            "type": "string",
            "pattern": ENDPOINT_ALIAS_PATTERN,
            "description": "the alias of an endpoint in that outpost's policy file",
        },
        "timeout_s": {
            "type": "number",
            "minimum": _OUTPOST_TIMEOUT_S[0],
            "maximum": _OUTPOST_TIMEOUT_S[1],
            "default": 30,
            "description": "seconds before a call is abandoned, the wait for an instance to claim it "
            "included",
        },
    },
}


def _mcp_outpost_check(config: dict[str, Any]) -> None:
    """A stored configuration: an outpost's name, an endpoint alias and a timeout, nothing else. The
    address and the credential are the outpost's and exist in its policy file only (ADR 0147 §7)."""
    extra = sorted(str(key) for key in config if key not in _OUTPOST_STORED_KEYS)
    if extra:
        raise AdapterError(
            f"{', '.join(f'`{key}`' for key in extra)} has no place in an `mcp-outpost` connector: it "
            "stores only `outpost`, `endpoint` and `timeout_s`; the server's address and its credential "
            "are the outpost's and live in its policy file, and the platform supplies the rest"
        )
    outpost, endpoint = config.get("outpost"), config.get("endpoint")
    if not isinstance(outpost, str) or not re.fullmatch(OUTPOST_SLUG_PATTERN, outpost):
        raise AdapterError(
            "`outpost` is the name of an outpost of the organisation: lowercase letters, digits or '-', "
            "63 at most"
        )
    if not isinstance(endpoint, str) or not re.fullmatch(ENDPOINT_ALIAS_PATTERN, endpoint):
        raise AdapterError(
            "`endpoint` is the alias of an endpoint in the outpost's policy file, never an address: a "
            "lowercase letter, then lowercase letters, digits or '-', 63 at most"
        )
    timeout = config.get("timeout_s")
    low, high = _OUTPOST_TIMEOUT_S
    if timeout is not None and (
        isinstance(timeout, bool) or not isinstance(timeout, int | float) or not low <= timeout <= high
    ):
        raise AdapterError(f"`timeout_s` is a number of seconds between {low} and {high}")


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
                "match_labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "when several projects share this Jira project, the labels that send "
                    "an issue here; a project without them takes the issues no other claims",
                },
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
    ("scm", "gitlab"): ConnectorTypeSpec(
        "GitLab",
        ("scm",),
        _object_schema(
            {
                "base_url": {"type": "string", "default": "https://gitlab.com"},
                "project": _TEXT,
                "default_branch": {"type": "string", "default": "main"},
            },
            ("project",),
        ),
        ("token",),
    ),
    ("ci", "tekton"): ConnectorTypeSpec(
        "Tekton Pipelines",
        ("ci",),
        _object_schema({"namespace": _TEXT, "pipeline": {"type": "string", "default": "choregos-ci"}}),
    ),
    ("ci", "gitlab"): ConnectorTypeSpec(
        "GitLab CI",
        ("ci",),
        _object_schema(
            {"base_url": {"type": "string", "default": "https://gitlab.com"}, "project": _TEXT}, ("project",)
        ),
        ("token",),
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
    # granted `read` on the chosen sites only — never the application `Files.Read.All` nor
    # `Sites.Read.All`. With `username`, it signs in as a read-only service account (ADR 0132).
    ("documents", "sharepoint"): ConnectorTypeSpec(
        "Microsoft SharePoint and OneDrive (Graph)",
        ("documents",),
        _object_schema(
            {
                "tenant_id": _TEXT,
                "client_id": _TEXT,
                "username": {
                    "type": "string",
                    "description": "optional: a read-only service account signs in instead of the "
                    "application, with its `password`; leave empty to sign in as the application",
                },
                "graph_url": {"type": "string", "default": "https://graph.microsoft.com/v1.0"},
                "login_url": {"type": "string", "default": "https://login.microsoftonline.com"},
            },
            ("tenant_id", "client_id"),
        ),
        ("client_secret", "password"),
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
    # A tool server that lives where the platform cannot reach (ADR 0149): no URL, no secret field, no
    # host of its own. The platform opens no connection for it, so it declares no egress.
    ("mcp", MCP_OUTPOST_TYPE): ConnectorTypeSpec(
        "MCP server at an outpost",
        ("mcp",),
        _OUTPOST_CONFIGURATION,
        (),
        egress=lambda config: [],
        check_config=_mcp_outpost_check,
    ),
    ("notify", "slack"): ConnectorTypeSpec(
        "Slack",
        ("notify",),
        _object_schema({"channel": {"type": "string", "default": "#choregos"}, "public_url": _TEXT}),
        ("webhook_url", "bot_token"),
    ),
    # The URL is the credential: a sealed secret, never a configuration value. The host is not known
    # until it is resolved, so the organisation's list opens it when the first card is refused.
    ("notify", "teams"): ConnectorTypeSpec(
        "Microsoft Teams (incoming webhook or Workflows URL)",
        ("notify",),
        _object_schema({}),
        ("webhook_url",),
        egress=lambda cfg: [],
    ),
    # SMTP does not go through the httpx egress guard: the spec declares its egress, and the
    # operator's network policy must open it.
    ("notify", "email"): ConnectorTypeSpec(
        "E-mail (SMTP)",
        ("notify",),
        _object_schema(
            {
                "host": _TEXT,
                "port": {"type": "integer", "default": 587},
                "username": _TEXT,
                "from_address": _TEXT,
                "to": {"type": "array", "items": _TEXT},
                "starttls": {"type": "boolean", "default": True},
                "use_ssl": {"type": "boolean", "default": False},
            },
            ("host", "from_address"),
        ),
        ("password",),
        egress=lambda cfg: [(str(cfg["host"]), int(cfg.get("port", 587)))],
    ),
}
