# SPDX-License-Identifier: Apache-2.0
"""The shipped adapters: their factories, the process-wide fakes and the builtin registrations.

`_register_builtins()` registers every shipped (kind, type) pair, in a fixed order, with the spec
`specs.py` declares for it. The mutable fake singletons (`FAKE_MCP`, `FAKE_NETOPS`, ...) live on the
package root, where tests rebind them: the factories here always read and write them through the
package, never through a copy.
"""

from __future__ import annotations

import os
import ssl
from collections.abc import Callable
from typing import Any

import tranibor_adapters as _package

from .errors import AdapterError, ConfigurationError
from .families import FAMILIES as _FAMILIES
from .http.pool import strategy_of
from .registry import fakes_enabled, register
from .specs import (
    _GATEWAY_ONLY_PLATFORM,
    _OUTPOST_ONLY_PLATFORM,
    _OUTPOST_STORED_KEYS,
    _SHIPPED_SPECS,
    MCP_GATEWAY_TYPE,
    MCP_OUTPOST_TYPE,
    OAUTH_CLIENT_CREDENTIALS,
    _check_plugin_tracker,
    _gateway_server_check,
    _mcp_check,
    _mcp_outpost_check,
)


def _truthy(value: object) -> bool:
    return value is True or str(value).strip().lower() in {"1", "true", "yes", "on"}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _names(listed: str) -> list[str]:
    """A comma-separated list of names, as the chart joins them: blanks and empties dropped."""
    return [name.strip() for name in listed.split(",") if name.strip()]


def _jira_tracker(cfg: dict[str, Any]) -> Any:
    """Jira. Cloud (default): the email and the API token as Basic, the token from the vault, never
    from the repository; a scoped token goes through the Atlassian gateway, a classic one to the
    site. `flavor: datacenter` (Data Center / Server 9.x, 10.x): a personal access token as Bearer,
    REST API v2, an optional `ca_bundle` for an internal CA."""
    from .http import RestClient
    from .tracker.jira import JiraGatewayClient, JiraTracker

    flavor = str(cfg.get("flavor") or "cloud")
    if flavor not in {"cloud", "datacenter"}:
        raise ConfigurationError(f"unknown Jira flavor `{flavor}`: `cloud` or `datacenter`")
    if flavor == "datacenter":
        ca_bundle = str(cfg.get("ca_bundle") or "").strip() or None
        if ca_bundle is not None and not os.path.isfile(ca_bundle):
            raise ConfigurationError(f"`ca_bundle` is not a readable file: {ca_bundle}")
        try:
            client: RestClient = RestClient(
                cfg["base_url"],
                headers={"Authorization": f"Bearer {cfg['api_token']}"},
                service="jira",
                ca_bundle=ca_bundle,
            )
        except ssl.SSLError as error:  # a file that is not a PEM bundle
            raise ConfigurationError(
                f"`ca_bundle` {ca_bundle} holds no usable certificate: {error}"
            ) from error
    else:
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
        flavor="datacenter" if flavor == "datacenter" else "cloud",
    )


def _azure_boards_tracker(cfg: dict[str, Any]) -> Any:
    """Azure DevOps Boards: a personal access token as Basic, or an Entra service principal's token, the
    secrets from the vault, never from the repository."""
    from .specs import _check_azure_boards
    from .tracker.azure_boards import AzureBoardsTracker, AzureDevOpsClient, EntraClientCredentials

    _check_azure_boards(cfg)
    entra = (
        EntraClientCredentials(
            str(cfg["tenant_id"]),
            str(cfg["client_id"]),
            str(cfg.get("client_secret") or ""),
            login_url=str(cfg.get("login_url") or "https://login.microsoftonline.com"),
        )
        if cfg.get("auth") == "entra"
        else None
    )
    client = AzureDevOpsClient(
        cfg["base_url"], pat=None if entra else str(cfg.get("pat") or ""), entra=entra, service="azure-boards"
    )
    return AzureBoardsTracker(
        client,
        cfg["project"],
        area_path=cfg.get("area_path"),
        webhook_secret=cfg.get("webhook_secret", ""),
        field_names=cfg.get("field_names"),
        candidates_wiql=cfg.get("candidates_wiql"),
    )


def _azure_repos_scm(cfg: dict[str, Any]) -> Any:
    """Azure DevOps Repos: a PAT or an Entra service principal, secrets from the vault. The run token is a
    separate, narrower PAT: a run never receives the connector's own credential."""
    from .azure_devops import azure_client, check_azure_repos
    from .scm.azure_repos import AzureReposScm

    check_azure_repos(cfg)
    return AzureReposScm(
        azure_client(cfg, service="azure-repos"),
        cfg["project"],
        cfg["repository"],
        default_branch=cfg.get("default_branch", "main"),
        merge_strategy=cfg.get("merge_strategy", "squash"),
        delete_source_branch=bool(cfg.get("delete_source_branch", False)),
        run_token=str(cfg.get("run_token") or ""),
        partially_succeeded=cfg.get("partially_succeeded", "failure"),
    )


def _azure_pipelines_ci(cfg: dict[str, Any]) -> Any:
    from .azure_devops import azure_client, check_azure_pipelines
    from .ci.azure_pipelines import AzurePipelinesCi

    check_azure_pipelines(cfg)
    return AzurePipelinesCi(
        azure_client(cfg, service="azure-pipelines"),
        cfg["project"],
        repository=str(cfg.get("repository") or ""),
        pipeline=str(cfg.get("pipeline") or ""),
        partially_succeeded=cfg.get("partially_succeeded", "failure"),
    )


def _github_client(config: dict[str, Any]) -> Any:
    """Builds the GitHub client from the connector's configuration or the deployment's App.

    The deployment's App is its settings (`TRANIBOR_GITHUB_APP_ID` / `_PRIVATE_KEY`), else the App a
    platform administrator registered from a manifest, which the API's process provider opens from the
    database (ADR 0167): the private key never travels in a connector's configuration."""
    import os

    from .github import GitHubAppAuth, GitHubClient, app_credentials

    deployment_id, deployment_key = app_credentials()
    app_id = config.get("app_id") or deployment_id
    private_key = config.get("private_key") or deployment_key
    token = config.get("token") or os.environ.get("TRANIBOR_GITHUB_TOKEN", "")
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
    is refused, naming the way out, as `guard.yaml` refuses an embedded dependency in staging and
    prod.
    """
    from .gateway.direct import DirectGateway  # a lazy import, like the others'

    environment = _env("TRANIBOR_ENV", "dev")
    if environment in {"staging", "prod"}:
        raise ConfigurationError(
            f"connector `gateway: direct` refused in {environment}: this gateway measures no "
            "cost and applies no ceiling. Use `litellm`, which issues a virtual key capped per run."
        )
    return DirectGateway(
        key=cfg.get("key", _env("TRANIBOR_GATEWAY_DIRECT_KEY", "")),
        models=cfg.get("models", []),
    )


def _current_fake_mcp() -> Any:
    """The fake MCP server of TRANIBOR_FAKES=1: one, scripted by the tests and the demo."""
    from .fakes.mcp import FakeMcpServer

    if _package.FAKE_MCP is None:
        _package.FAKE_MCP = FakeMcpServer()
    return _package.FAKE_MCP


def fake_mcp_gateway() -> Any:
    """The fake MCP gateway of TRANIBOR_FAKES=1: one, whose fallback is always the CURRENT `FAKE_MCP`."""
    from .fakes.mcp_gateway import FakeMcpGateway

    if _package.FAKE_MCP_GATEWAY is None:
        # The function itself, not its result: tests replace `FAKE_MCP`, and the gateway follows.
        _package.FAKE_MCP_GATEWAY = FakeMcpGateway(auth="none", fallback=_current_fake_mcp)
    return _package.FAKE_MCP_GATEWAY


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
    url = cfg.get("url", "http://fake-mcp.test/mcp")
    return McpClient(
        url,
        **_mcp_auth(cfg, url, token_transport=_fake_oauth_transport),
        transport=_current_fake_mcp().transport(),
        user_agent=str(cfg.get("user_agent", "")),
    )


def _fake_oauth_transport() -> Any:
    """Under TRANIBOR_FAKES=1, an `oauth_client_credentials` connector asks the fake OAuth provider."""
    from .fakes.oauth_provider import current_fake_oauth_provider

    return current_fake_oauth_provider().transport()


def _mcp_auth(
    cfg: dict[str, Any], url: str, *, token_transport: Callable[[], Any] | None = None
) -> dict[str, Any]:
    """How a direct `mcp` connector signs its requests: its token as Bearer, Basic or a named header, or,
    with `oauth_client_credentials`, a token the platform fetches from `token_url` with the client's id
    and sealed secret, cached until shortly before it expires and renewed after a 401. The static
    `token` is not sent then: the connector has one credential, the client's."""
    if cfg.get("auth_scheme") != OAUTH_CLIENT_CREDENTIALS:
        return {
            "token": cfg.get("token", ""),
            "auth_scheme": cfg.get("auth_scheme", "bearer"),
            "username": cfg.get("username", ""),
            "auth_header": cfg.get("auth_header", "Authorization"),
        }
    from .mcp.client_credentials import CONNECTOR_TOKENS, ClientCredentials

    _mcp_check(cfg)
    secret = cfg.get("client_secret")
    if not isinstance(secret, str) or not secret:
        raise AdapterError(
            "an `oauth_client_credentials` MCP connector needs its `client_secret`: an administrator "
            "types it on the connector, where it is sealed"
        )
    credentials = ClientCredentials(
        token_url=str(cfg["token_url"]),
        client_id=str(cfg["client_id"]),
        client_secret=secret,
        scope=cfg.get("scope") or None,
        audience=cfg.get("audience") or None,
    )
    provider = CONNECTOR_TOKENS.provider(
        credentials, transport=token_transport, timeout_s=float(cfg.get("timeout_s", 30)), partition=url
    )
    return {"headers_provider": provider}


def _mcp_client(cfg: dict[str, Any]) -> Any:
    from .mcp import McpClient

    return McpClient(
        cfg["url"],
        **_mcp_auth(cfg, cfg["url"]),
        timeout_s=float(cfg.get("timeout_s", 30)),
        user_agent=str(cfg.get("user_agent", "")),
    )


def _teams_notifier(cfg: dict[str, Any]) -> Any:
    """The Teams webhook notifier. Under `TRANIBOR_FAKES=1` it is the real notifier (card, guarded transport,
    egress allowlist) over the process-wide `FakeTeamsWebhook`, never a network call."""
    from .notify.teams import TeamsNotifier

    url = cfg.get("webhook_url")
    if not isinstance(url, str) or not url:
        raise AdapterError("a Teams notifier needs its `webhook_url` (a sealed secret of the installation)")
    if fakes_enabled():
        from .fakes.teams import FakeTeamsWebhook

        if _package.FAKE_TEAMS is None:
            _package.FAKE_TEAMS = FakeTeamsWebhook()
        return TeamsNotifier(webhook_url=url, transport=_package.FAKE_TEAMS.transport())
    return TeamsNotifier(webhook_url=url)


def _mcp_gateway_client(cfg: dict[str, Any]) -> Any:
    _gateway_server_check(cfg.get("server"))
    if cfg.get("route") != "gateway" or not cfg.get("gateway_url"):
        raise AdapterError(_GATEWAY_ONLY_PLATFORM)
    return _gateway_client(cfg, transport=cfg.get("transport"))


def fake_outpost() -> Any:
    """The fake outpost of TRANIBOR_FAKES=1: one, whose every alias is served by the CURRENT `FAKE_MCP`."""
    from .fakes.outpost import FakeOutpost

    if _package.FAKE_OUTPOST is None:
        # The function itself, not its result: tests replace `FAKE_MCP`, and the outpost follows.
        _package.FAKE_OUTPOST = FakeOutpost(fallback=_current_fake_mcp)
    return _package.FAKE_OUTPOST


def _mcp_outpost_client(cfg: dict[str, Any]) -> Any:
    """The tool client of a connector at an outpost, over the rendezvous the platform injects. There
    is none to build it with otherwise, under fakes or not: the platform registers the factory that
    supplies its own (`tranibor_api.adapter_wiring`)."""
    from .outpost import client_of

    _mcp_outpost_check({key: cfg[key] for key in _OUTPOST_STORED_KEYS if key in cfg})
    rendezvous = cfg.get("rendezvous")
    if cfg.get("route") != "outpost" or rendezvous is None:
        raise AdapterError(_OUTPOST_ONLY_PLATFORM)
    return client_of(cfg, rendezvous)


def _fake_entra_directory(cfg: dict[str, Any]) -> Any:
    from .fakes.entra import FakeEntra
    from .identity import EntraIdentity

    if _package.FAKE_ENTRA is None:
        _package.FAKE_ENTRA = FakeEntra()
    fake = _package.FAKE_ENTRA
    return EntraIdentity(
        tenant_id=cfg.get("tenant_id", "acme"),
        client_id=cfg.get("client_id", "choregos"),
        client_secret=cfg.get("client_secret", "faux"),
        administrative_unit_id=cfg.get("administrative_unit_id", fake.unit),
        transport=fake.transport(),
    )


def _demo_desk(family: str, *, refuse_in_production: bool) -> Any:
    """The factory of a `<family>: demo` connector (or of its fake, under `TRANIBOR_FAKES=1`)."""

    def construct(cfg: dict[str, Any]) -> Any:
        from .fakes.hr import FAKES_BY_FAMILY, Desk

        environment = _env("TRANIBOR_ENV", "dev")
        if refuse_in_production and environment in {"staging", "prod"}:
            raise ConfigurationError(
                f"connector `{family}: demo` refused in {environment}: an in-memory fake, which "
                "each process holds for itself: a demonstration, not a company system."
            )
        if refuse_in_production and cfg.get("url"):
            # Served by a single process (`tranibor_adapters.fakes.server`): where the API and
            # the orchestrator are two processes, an in-memory fake would show them two states.
            from .fakes.hr import RemoteDesk
            from .mcp import McpClient

            return RemoteDesk(McpClient(str(cfg["url"]), token=str(cfg.get("api_key", ""))))
        desks = _package.FAUX_METIER  # lang: fr-ok (the business fakes keep their name)
        if family not in desks:
            desks[family] = FAKES_BY_FAMILY[family]()
        return Desk(desks[family], str(cfg.get("api_key", "")))

    return construct


def _fake_netops(name: str) -> Any:
    """The netops fakes (ADR 0052): one pfSense, one cluster, one recorded Hubble trace, one GitOps
    repository, shared in the process (`FAKE_NETOPS`), scripted by the tests and the demo."""
    if name not in _package.FAKE_NETOPS:
        from .fakes import netops as fakes

        builders: dict[str, Callable[[], Any]] = {
            "pfsense": fakes.FakePfSense,
            "kubernetes": fakes.FakeCiliumKubernetes,
            "flows": fakes.recorded_flows,
            "repo": fakes.FakeRepo,
        }
        _package.FAKE_NETOPS[name] = builders[name]()
    return _package.FAKE_NETOPS[name]


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

    if "scm" not in _package.FAKE_NETOPS:
        from .fakes import FakeScm

        _package.FAKE_NETOPS["scm"] = FakeScm()
    return GitOpsTarget(_package.FAKE_NETOPS["scm"], repo, base, root)


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


def _fake_web_search(cfg: dict[str, Any]) -> Any:
    """The fake of the `web_search` family, shared in the process (scriptable by tests)."""
    if _package.FAKE_WEB_SEARCH is None:
        from .fakes.web_search import FakeSearchBackend

        _package.FAKE_WEB_SEARCH = FakeSearchBackend()
    return _package.FAKE_WEB_SEARCH


def _demo_cd(cfg: dict[str, Any]) -> Any:
    """The factory of `cd: demo`: refused in staging and prod; served when it carries a `url`."""
    from .fakes.demo_cd import DemoCd, FakeDemoCd
    from .fakes.hr import Desk, RemoteDesk

    environment = _env("TRANIBOR_ENV", "dev")
    if environment in {"staging", "prod"}:
        raise ConfigurationError(
            f"connector `cd: demo` refused in {environment}: a simulated environment, a "
            "demonstration, not a deployment."
        )
    if cfg.get("url"):
        from .mcp import McpClient

        return DemoCd(RemoteDesk(McpClient(str(cfg["url"]), token=str(cfg.get("api_key", "")))))
    desks = _package.FAUX_METIER  # lang: fr-ok (the business fakes keep their name)
    if "cd" not in desks:
        desks["cd"] = FakeDemoCd()
    return DemoCd(Desk(desks["cd"], str(cfg.get("api_key", ""))))


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


def _register_fakes() -> None:
    """The fake of every kind (`TRANIBOR_FAKES=1`)."""
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


def _register_forges() -> None:
    """Trackers, source control, CI and CD."""
    from .cd.argocd import ArgoCdAdapter
    from .ci.github_actions import GitHubActionsCi
    from .ci.gitlab import GitLabCi
    from .ci.jenkins import JenkinsCi
    from .ci.tekton import TektonCi
    from .executor.tekton import KubernetesClient
    from .http import RestClient
    from .scm.github import GitHubScm
    from .scm.gitlab import GitLabScm
    from .tracker.github import GitHubTracker
    from .tracker.gitlab import GitLabTracker
    from .tracker.gitlab_instance import build_client as build_gitlab_client
    from .tracker.internal import InternalTracker

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
    register("tracker", "azure-boards", _SHIPPED_SPECS[("tracker", "azure-boards")])(_azure_boards_tracker)
    register("tracker", "plugin", _SHIPPED_SPECS[("tracker", "plugin")])(_plugin_tracker)
    register("tracker", "gitlab-issues", _SHIPPED_SPECS[("tracker", "gitlab-issues")])(
        lambda cfg: GitLabTracker(
            build_gitlab_client(cfg),
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
    register("scm", "gitlab", _SHIPPED_SPECS[("scm", "gitlab")])(
        lambda cfg: GitLabScm(
            build_gitlab_client(cfg),
            cfg["project"],
            default_branch=cfg.get("default_branch", "main"),
        )
    )
    register("scm", "azure-repos", _SHIPPED_SPECS[("scm", "azure-repos")])(_azure_repos_scm)
    register("ci", "azure-pipelines", _SHIPPED_SPECS[("ci", "azure-pipelines")])(_azure_pipelines_ci)
    register("ci", "tekton", _SHIPPED_SPECS[("ci", "tekton")])(
        lambda cfg: TektonCi(
            KubernetesClient(**cfg.get("kubernetes", {})),
            namespace=cfg.get("namespace", "default"),
            pipeline=cfg.get("pipeline", "choregos-ci"),
        )
    )
    register("ci", "gitlab", _SHIPPED_SPECS[("ci", "gitlab")])(
        lambda cfg: GitLabCi(
            build_gitlab_client(cfg),
            cfg["project"],
        )
    )
    register("ci", "jenkins", _SHIPPED_SPECS[("ci", "jenkins")])(
        lambda cfg: JenkinsCi(
            RestClient(
                cfg["base_url"],
                auth=(cfg["user"], cfg["token"]),
                service="jenkins",
            ),
            cfg["job"],
            multibranch=bool(cfg.get("multibranch", False)),
            parameters=cfg.get("parameters"),
            ref_parameter=cfg.get("ref_parameter", "BRANCH"),
            crumb=bool(cfg.get("crumb", True)),
        )
    )
    register("ci", "github-actions", _SHIPPED_SPECS[("ci", "github-actions")])(
        lambda cfg: GitHubActionsCi(
            _github_client(cfg),
            cfg["repo"],
            workflow=cfg.get("workflow", ""),
            inputs=cfg.get("inputs"),
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


def _own_resources(raw: str, key: str) -> dict[str, str] | None:
    """`requests` or `limits` of `{"requests": {...}, "limits": {...}}` as JSON (ADR 0146 §12), the
    runner container's own. Empty or absent: None, the executor's defaults."""
    if not raw.strip():
        return None
    import json

    values = json.loads(raw).get(key) or {}
    return {str(k): str(v) for k, v in values.items()} or None


def _register_runtimes() -> None:
    """The executors of the agents' steps."""
    from tranibor_core.agent_isolation import deployment_agent_isolation
    from tranibor_core.egress_gate import gate_from_env

    from .executor.aca import AcaExecutor, AzureArmClient
    from .executor.agent_sandbox import DEFAULT_TTL_SECONDS, AgentSandboxExecutor
    from .executor.k8s_job import KubernetesJobExecutor
    from .executor.local_docker import LocalDockerExecutor
    from .executor.tekton import KubernetesClient, TektonExecutor

    register("runtime", "tekton", _SHIPPED_SPECS[("runtime", "tekton")])(
        lambda cfg: TektonExecutor(
            KubernetesClient(**cfg.get("kubernetes", {})),
            pipeline=cfg.get("pipeline", "choregos-agent"),
        )
    )
    register("runtime", "k8s_job")(
        lambda cfg: KubernetesJobExecutor(
            KubernetesClient(**cfg.get("kubernetes", {})),
            cpu_limit=cfg.get("cpu_limit", os.environ.get("TRANIBOR_RUNNER_CPU_LIMIT", "") or "2"),
            memory_limit=cfg.get("memory_limit", os.environ.get("TRANIBOR_RUNNER_MEMORY_LIMIT", "") or "6Gi"),
            # The deployment's, never a connector's (S65-03): the chart's `runner.envFromSecrets`.
            env_from_secrets=_names(os.environ.get("TRANIBOR_RUNNER_ENV_SECRETS", "")),
            # How many runs run at the same time in the namespace. 0 = no ceiling.
            max_active=int(cfg.get("max_active", os.environ.get("TRANIBOR_RUNNER_MAX_ACTIVE", "") or 0)),
            # The chart's `global.imagePullSecrets` (S51-09): a step's own image may be private.
            image_pull_secrets=_names(os.environ.get("TRANIBOR_RUNNER_IMAGE_PULL_SECRETS", "")),
            # The egress gate (S80-07f), the chart's `runner.egressGate`: the deployment's, never a
            # connector's, so a project cannot turn it off.
            egress_gate=gate_from_env(os.environ),
            # The runner container's own requests and limits beside the agent's (ADR 0146 §12), the
            # chart's `runner.ownResources`; the measured defaults when unset.
            runner_requests=_own_resources(os.environ.get("TRANIBOR_RUNNER_OWN_RESOURCES", ""), "requests"),
            runner_limits=_own_resources(os.environ.get("TRANIBOR_RUNNER_OWN_RESOURCES", ""), "limits"),
        )
    )
    register("runtime", "agent_sandbox", _SHIPPED_SPECS[("runtime", "agent_sandbox")])(
        lambda cfg: AgentSandboxExecutor(
            KubernetesClient(**cfg.get("kubernetes", {})),
            warm_pool=cfg.get(
                "warm_pool", os.environ.get("TRANIBOR_SANDBOX_WARM_POOL", "") or "choregos-runner"
            ),
            assignment_port=int(
                cfg.get("assignment_port", os.environ.get("TRANIBOR_SANDBOX_ASSIGNMENT_PORT", "") or 8765)
            ),
            # The template's RuntimeClass, which the chart sets from `runner.runtimeClass`.
            runtime_class=cfg.get("runtime_class", os.environ.get("TRANIBOR_RUNNER_RUNTIME_CLASS", ""))
            or None,
            # The chart builds the warm pool's template and the orchestrator's `TRANIBOR_RUNNER_IMAGE`
            # from the same value: a run asking for another image cannot be honoured.
            pool_image=cfg.get("pool_image", os.environ.get("TRANIBOR_RUNNER_IMAGE", "")) or None,
            max_active=int(cfg.get("max_active", os.environ.get("TRANIBOR_RUNNER_MAX_ACTIVE", "") or 0)),
            # The deployment's, never a connector's (S65-03): the chart's `runner.envFromSecrets`.
            env_from_secrets=_names(os.environ.get("TRANIBOR_RUNNER_ENV_SECRETS", "")),
            # How long a finished sandbox keeps its volume (`runner.sandbox.ttlSecondsAfterFinished`).
            ttl_seconds=int(
                cfg.get(
                    "ttl_seconds", os.environ.get("TRANIBOR_SANDBOX_TTL_SECONDS", "") or DEFAULT_TTL_SECONDS
                )
            ),
            # The deployment's, never a connector's (ADR 0146): the chart renders the template's agent
            # container from the same `global.runner.agentIsolation`.
            agent_isolation=deployment_agent_isolation(os.environ),
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


def _register_memory_and_gateways() -> None:
    """The memory and the model gateways."""
    from .gateway.litellm import LiteLlmGateway
    from .gateway.openai_compatible import OpenAiCompatibleGateway
    from .memory.ecphoria import EcphoriaMemory
    from .memory.lexical import LexicalMemory

    register("memory", "ecphoria", _SHIPPED_SPECS[("memory", "ecphoria")])(
        lambda cfg: EcphoriaMemory(
            # Like the gateway: the deployment's URL and token, unless overridden per project.
            # `TRANIBOR_MEMORY_URL` was set by the chart and ignored here.
            cfg.get("base_url", _env("TRANIBOR_MEMORY_URL", "http://ecphoria.choregos-memory:8432")),
            token=cfg.get("token", _env("TRANIBOR_MEMORY_TOKEN", "")),
            tenant=cfg.get("tenant"),
            read_timeout_ms=int(cfg.get("read_timeout_ms", 300)),
            # Off until the deployment runs an Ecphoria that confines the by-id tools to a tenant.
            scoped_by_id=_truthy(cfg.get("scoped_by_id", _env("CHOREGOS_MEMORY_SCOPED_BY_ID", ""))),
        )
    )
    # `pgvector`: a deprecated alias of `lexical`: the name promised vectors, the code does a
    # lexical similarity. Kept for the projects already configured.
    register("memory", "lexical", _SHIPPED_SPECS[("memory", "lexical")])(lambda cfg: LexicalMemory())
    register("memory", "pgvector", _SHIPPED_SPECS[("memory", "pgvector")])(lambda cfg: LexicalMemory())
    register("gateway", "direct", _SHIPPED_SPECS[("gateway", "direct")])(_direct_gateway)
    register("gateway", "litellm", _SHIPPED_SPECS[("gateway", "litellm")])(
        lambda cfg: LiteLlmGateway(
            cfg.get("base_url", _env("TRANIBOR_GATEWAY_URL", "http://litellm.choregos-gateway:4000")),
            cfg.get("master_key", _env("TRANIBOR_GATEWAY_MASTER_KEY", "")),
            team_id=cfg.get("team_id"),
            internal_prices=cfg.get("internal_prices", {}),
            enterprise_tags=cfg.get("enterprise_tags", False),
        )
    )
    register("gateway", "openai-compatible", _SHIPPED_SPECS[("gateway", "openai-compatible")])(
        lambda cfg: OpenAiCompatibleGateway(
            cfg.get("base_url", _env("TRANIBOR_GATEWAY_URL", "")),
            api_key=cfg.get("api_key", _env("TRANIBOR_GATEWAY_MASTER_KEY", "")),
            run_key=cfg.get("run_key", ""),
            headers=cfg.get("headers", {}),
            timeout=float(cfg.get("timeout_s", 20)),
        )
    )


def _register_documents_and_identity() -> None:
    """The document sources, the identity directory and the business families' demos."""
    from .documents.confluence import ConfluenceDocumentsConnector
    from .documents.git import GitDocumentsConnector
    from .documents.graph import GraphDocumentsConnector
    from .documents.web import WebDocumentsConnector
    from .identity import EntraIdentity
    from .netops import PfSenseFirewall

    register("documents", "sharepoint", _SHIPPED_SPECS[("documents", "sharepoint")])(GraphDocumentsConnector)
    register("documents", "confluence", _SHIPPED_SPECS[("documents", "confluence")])(
        ConfluenceDocumentsConnector
    )
    register("documents", "git", _SHIPPED_SPECS[("documents", "git")])(GitDocumentsConnector)
    register("documents", "web", _SHIPPED_SPECS[("documents", "web")])(WebDocumentsConnector)
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
    register("firewall", "pfsense", _SHIPPED_SPECS[("firewall", "pfsense")])(PfSenseFirewall.from_config)
    register("network_policy", "cilium", _SHIPPED_SPECS[("network_policy", "cilium")])(_cilium)
    register("web_server", "nginx", _SHIPPED_SPECS[("web_server", "nginx")])(_nginx)
    _register_forge_accounts()


def _register_forge_accounts() -> None:
    """A forge's OAuth application (ADR 0161): the factory builds the adapter that proves an account."""
    from .forge_accounts import KIND, PROVIDERS, connector_type, forge_accounts

    for provider in PROVIDERS:
        type_name = connector_type(provider)
        register(KIND, type_name, _SHIPPED_SPECS[(KIND, type_name)])(
            lambda cfg, provider=provider: forge_accounts(provider, cfg)  # type: ignore[misc]
        )


def _register_mcp_and_notifiers() -> None:
    """The MCP servers and the notifiers."""
    from .notify.email import EmailNotifier
    from .notify.slack import SlackNotifier

    # A real MCP server (ADR 0034): its operations are DISCOVERED, and are born closed.
    register("mcp", "mcp", _SHIPPED_SPECS[("mcp", "mcp")])(_mcp_client)
    register("mcp", MCP_GATEWAY_TYPE, _SHIPPED_SPECS[("mcp", MCP_GATEWAY_TYPE)])(_mcp_gateway_client)
    # A tool server at an outpost (ADR 0149): the platform replaces this factory with the one that
    # supplies its rendezvous.
    register("mcp", MCP_OUTPOST_TYPE, _SHIPPED_SPECS[("mcp", MCP_OUTPOST_TYPE)])(_mcp_outpost_client)
    register("notify", "slack", _SHIPPED_SPECS[("notify", "slack")])(
        lambda cfg: SlackNotifier(
            webhook_url=cfg.get("webhook_url", _env("TRANIBOR_SLACK_WEBHOOK", "")),
            bot_token=cfg.get("bot_token", _env("TRANIBOR_SLACK_TOKEN", "")),
            default_channel=cfg.get("channel", "#choregos"),
            public_url=cfg.get("public_url", _env("TRANIBOR_PUBLIC_URL", "")),
        )
    )
    register("notify", "teams", _SHIPPED_SPECS[("notify", "teams")])(_teams_notifier)
    register("notify", "email", _SHIPPED_SPECS[("notify", "email")])(
        lambda cfg: EmailNotifier(
            host=str(cfg["host"]),
            port=int(cfg.get("port", 587)),
            username=str(cfg.get("username", "")),
            password=str(cfg.get("password", "")),
            from_address=str(cfg["from_address"]),
            to=[str(address) for address in cfg.get("to") or []],
            starttls=bool(cfg.get("starttls", True)),
            use_ssl=bool(cfg.get("use_ssl", False)),
        )
    )


def _register_builtins() -> None:
    """Registers the shipped implementations (a lazy import to avoid cycles), kind by kind, in a
    fixed order: the registry keeps it."""
    _register_fakes()
    _register_forges()
    _register_runtimes()
    _register_memory_and_gateways()
    _register_documents_and_identity()
    _register_web_search()
    _register_mcp_and_notifiers()
