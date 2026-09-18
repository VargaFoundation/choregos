"""Adaptateurs Choregos : tout ce qui parle au monde extérieur.

Un adaptateur implémente un `Protocol` de `base.py` et **n'a aucune logique métier** :
la décision est dans `packages/core`, l'orchestration dans `apps/orchestrator`.
"""

from __future__ import annotations

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
from .registry import AdapterSet, available, build, fakes_enabled, register


def _env(name: str, default: str = "") -> str:
    import os

    return os.environ.get(name, default)


__version__ = "0.1.0"

__all__ = [
    "AdapterSet",
    "AgentBackend",
    "CdAdapter",
    "CiAdapter",
    "Executor",
    "GatewayAdapter",
    "MemoryAdapter",
    "Notifier",
    "ScmAdapter",
    "TrackerAdapter",
    "available",
    "build",
    "fakes_enabled",
    "register",
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

    # ───────────────── implémentations réelles (jour 1) ─────────────────
    from .cd.argocd import ArgoCdAdapter
    from .ci.tekton import TektonCi
    from .executor.k8s_job import KubernetesJobExecutor
    from .executor.local_docker import LocalDockerExecutor
    from .executor.tekton import KubernetesClient, TektonExecutor
    from .gateway.litellm import LiteLlmGateway
    from .http import RestClient
    from .memory.ecphoria import EcphoriaMemory
    from .memory.pgvector import PgVectorMemory
    from .notify.slack import SlackNotifier
    from .scm.github import GitHubScm
    from .tracker.github import GitHubTracker
    from .tracker.gitlab import GitLabTracker
    from .tracker.jira import JiraTracker

    register("tracker", "github-issues")(
        lambda cfg: GitHubTracker(
            _github_client(cfg),
            cfg["repo"],
            project_number=cfg.get("project_number"),
            org=cfg.get("org"),
            webhook_secret=cfg.get("webhook_secret", ""),
        )
    )
    register("tracker", "jira")(
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
    register("tracker", "gitlab-issues")(
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
    register("scm", "github")(
        lambda cfg: GitHubScm(
            _github_client(cfg),
            default_repo=cfg.get("repo", ""),
            merge_method=cfg.get("merge_method", "SQUASH"),
            use_merge_queue=cfg.get("merge_queue", True),
        )
    )
    register("ci", "tekton")(
        lambda cfg: TektonCi(
            KubernetesClient(**cfg.get("kubernetes", {})),
            namespace=cfg.get("namespace", "default"),
            pipeline=cfg.get("pipeline", "choregos-ci"),
        )
    )
    register("cd", "argocd")(
        lambda cfg: ArgoCdAdapter(
            base_url=cfg.get("base_url", "http://argocd-server.argocd"),
            token=cfg.get("token", ""),
            gitops_repo=cfg.get("gitops_repo", ""),
            github=_github_client(cfg),
            app_pattern=cfg.get("app_pattern", "{app}-{env}"),
            path_prefix=cfg.get("path_prefix", "apps"),
        )
    )
    register("runtime", "tekton")(
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
        )
    )
    register("runtime", "local_docker")(
        lambda cfg: LocalDockerExecutor(network=cfg.get("network", "choregos_default"))
    )
    register("memory", "ecphoria")(
        lambda cfg: EcphoriaMemory(
            cfg.get("base_url", "http://ecphoria.choregos-memory:8432"),
            token=cfg.get("token", ""),
            tenant=cfg.get("tenant"),
            read_timeout_ms=int(cfg.get("read_timeout_ms", 300)),
        )
    )
    register("memory", "pgvector")(lambda cfg: PgVectorMemory())
    register("gateway", "litellm")(
        lambda cfg: LiteLlmGateway(
            cfg.get("base_url", _env("CHOREGOS_GATEWAY_URL", "http://litellm.choregos-gateway:4000")),
            cfg.get("master_key", _env("CHOREGOS_GATEWAY_MASTER_KEY", "")),
            team_id=cfg.get("team_id"),
            internal_prices=cfg.get("internal_prices", {}),
        )
    )
    register("notify", "slack")(
        lambda cfg: SlackNotifier(
            webhook_url=cfg.get("webhook_url", _env("CHOREGOS_SLACK_WEBHOOK", "")),
            bot_token=cfg.get("bot_token", _env("CHOREGOS_SLACK_TOKEN", "")),
            default_channel=cfg.get("channel", "#choregos"),
            public_url=cfg.get("public_url", _env("CHOREGOS_PUBLIC_URL", "")),
        )
    )


_register_builtins()
