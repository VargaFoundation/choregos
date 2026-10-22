# SPDX-License-Identifier: Apache-2.0
"""The demo organisation of the local install (`compose.yaml`, S28): one project, ready to run.

    python -m choregos_api.local_demo                          # seed (the `seed` service)
    python -m choregos_api.local_demo litellm-config /litellm  # config.yaml + provider

The one-shot `seed` service of the compose file runs it after the API is healthy, so that the
API's own bootstrap (`CHOREGOS_BOOTSTRAP_ORG`, `amorcage.py`) has already created the organisation
and its admin. It writes to the database like `demo/seed.py` does: provisioning a project means
Kubernetes namespaces and GitOps, which a laptop install does not have.

Re-running it changes nothing that exists, except what follows the install's MODE: the demo
project's runtime and gateway connectors, and its agent backend, switch between fakes and a real
model when a key is added to (or removed from) `.env` — unless they were changed in the console,
in which case they are left alone.

Fakes mode (no model key): the tracker is Choregos itself (`internal`), the agent is simulated
(`fake` runtime with `simulate`: every declared output filled, no model called), the rest are
in-memory fakes. The work item still goes through the real path: Temporal, the database, the
gates, the human decision.

Model mode (`ANTHROPIC_API_KEY` or `OPENAI_API_KEY` in `.env`): LiteLLM routes the `platform/*`
aliases to that provider (`litellm_config`), the agent runs in the runner image started by the
local Docker executor, and the platform mints a LiteLLM key per run, as in a cluster.
`LITELLM_REAL=0` keeps fakes even with a key; `LITELLM_REAL=1` refuses to start without one.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
from collections.abc import Mapping
from typing import Any, Literal

import yaml
from choregos_core import llm_guardrail
from sqlalchemy import select

from .config import Settings, get_settings
from .db.models import Connector, Organization, Project, WorkItem
from .db.session import session_scope
from .logging import configure_logging, get_logger

logger = get_logger("choregos.local_demo")

DEMO_PROJECT = "demo"

#: The demo project's workflow: a request comes in, an agent writes a plan, a person approves it.
#: No repository, no deployment: nothing a laptop does not have.
DEMO_WORKFLOW = """\
apiVersion: choregos/v1
kind: Workflow
metadata:
  name: local-demo
  version: 1
  description: A request comes in, an agent writes a plan, a person approves it.
actors:
  planner: { type: agent, role: plan, model: "profile:standard", max_turns: 20, max_minutes: 10 }
  approver: { type: human, group: approvers, sla_hours: 24 }
states:
  inbox: { display: New request, kind: wait }
  awaiting_approval: { display: Plan to approve, kind: wait }
  approved: { display: Approved, terminal: true }
  needs_human: { display: Needs a human, kind: wait }
  abandoned: { display: Abandoned, terminal: true }
transitions:
  - id: t-plan
    from: inbox
    to: awaiting_approval
    by: planner
    outputs: [plan_markdown]
    gates: [outputs_present]
    on_fail: { to: inbox, max_attempts: 2, escalate_to: needs_human }
  - id: t-approve
    from: awaiting_approval
    to: approved
    by: approver
    on_reject: inbox
    timeout_hours: 72
  - id: t-abandon
    from: needs_human
    to: abandoned
    by: approver
    timeout_hours: 168
defaults:
  from_any_agent_state:
    on_question: needs_human
    on_budget_exceeded: needs_human
    on_timeout: needs_human
  needs_human:
    on_answer: resume
    on_abandon: abandoned
"""

DEMO_TICKET_TITLE = "Plan the team offsite"
DEMO_TICKET_BODY = (
    "Two days for twelve people near Lyon, in May. The agent drafts a plan; you approve it or send "
    "it back from the work item's page."
)

Provider = Literal["anthropic", "openai"]

#: What each `platform/*` alias becomes at each provider. Anthropic ids from the Claude API
#: reference (2026-10); OpenAI ids are those of the chart's LiteLLM defaults.
PROVIDER_MODELS: dict[str, dict[str, str]] = {
    "anthropic": {
        "platform/standard": "anthropic/claude-sonnet-5-5",
        "platform/strong": "anthropic/claude-opus-5-5",
        "platform/cheap": "anthropic/claude-haiku-5-5",
    },
    "openai": {
        "platform/standard": "openai/gpt-5.5",
        "platform/strong": "openai/gpt-5.5",
        "platform/cheap": "openai/gpt-5.4-mini",
    },
}
PROVIDER_KEYS: dict[str, str] = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
#: What `platform/embed` is routed to, as in the chart's LiteLLM values. Anthropic serves no
#: embeddings, so the alias needs an OpenAI key whichever provider runs the agents.
EMBED_MODEL = "openai/text-embedding-3-small"
#: The agent backend for each provider: `opencode` for both, through LiteLLM's OpenAI-compatible
#: API. Not `claude-code` for Anthropic: in the published runner (0.17.12) its ACP adapter asked
#: LiteLLM for `claude-sonnet-4-5-20250929` whatever `ANTHROPIC_MODEL` said, and the run's virtual
#: key, minted for one model, was refused (seen on 2026-10-12; a finding, not fixed here).
PROVIDER_BACKENDS: dict[str, str] = {"anthropic": "opencode", "openai": "opencode"}
#: The compose network, named in `compose.yaml`: run containers join it to reach the API and LiteLLM.
LOCAL_NETWORK = "choregos"
MOCK_RESPONSE = "simulated answer: the local install has no model key"
#: The module name LiteLLM loads the LLM guard from, next to its config (ADR 0084).
GUARDRAIL_MODULE = "choregos_guardrail"
#: The connectors that follow the mode; every other one is created once and left alone.
MODE_KINDS = ("runtime", "gateway")


def model_provider(env: Mapping[str, str]) -> Provider | None:
    """The provider the install uses, from its environment; `None` is fakes mode.

    `LITELLM_REAL`: unset or `auto` follows the keys (Anthropic first), `0` forces fakes,
    `1` demands a key — so that a typo in `.env` fails loudly instead of running fakes in silence.
    """
    real = env.get("LITELLM_REAL", "").strip().lower() or "auto"
    if real in {"0", "false", "no"}:
        return None
    if env.get("ANTHROPIC_API_KEY", "").strip():
        return "anthropic"
    if env.get("OPENAI_API_KEY", "").strip():
        return "openai"
    if real in {"1", "true", "yes"}:
        raise ValueError("LITELLM_REAL=1 but neither ANTHROPIC_API_KEY nor OPENAI_API_KEY is set in .env")
    return None


def litellm_config(provider: Provider | None, *, embed: bool = False) -> dict[str, Any]:
    """LiteLLM's configuration: the aliases, mocked in fakes mode, routed to `provider` otherwise.

    `embed` also serves `platform/embed`, the alias Ecphoria embeds with (`--profile memory`),
    routed to OpenAI as the chart does (#438). The key itself is never written: LiteLLM reads it
    from its own environment (`os.environ/…`).
    """
    models: list[dict[str, Any]] = []
    for alias in ("platform/standard", "platform/strong", "platform/cheap"):
        if provider is None:
            params: dict[str, Any] = {
                "model": "openai/gpt-4o-mini",
                "api_key": "no-key-in-fakes-mode",
                "mock_response": MOCK_RESPONSE,
            }
        else:
            params = {
                "model": PROVIDER_MODELS[provider][alias],
                "api_key": f"os.environ/{PROVIDER_KEYS[provider]}",
            }
        models.append({"model_name": alias, "litellm_params": params})
        if provider is not None:
            # Also under the provider's own name: the demo project asks for it (`demo_models`).
            models.append({"model_name": params["model"], "litellm_params": dict(params)})
    if embed:
        models.append(
            {
                "model_name": "platform/embed",
                "litellm_params": {"model": EMBED_MODEL, "api_key": "os.environ/OPENAI_API_KEY"},
                "model_info": {"mode": "embedding"},
            }
        )
    return {
        "model_list": models,
        "litellm_settings": {"drop_params": True},
        "general_settings": {
            "master_key": "os.environ/LITELLM_MASTER_KEY",
            "database_url": "os.environ/DATABASE_URL",
        },
        # ADR 0084, as in the chart: the file sits next to this config (`write_litellm_config`).
        "guardrails": [
            {
                "guardrail_name": "choregos-secrets",
                "litellm_params": {
                    "guardrail": f"{GUARDRAIL_MODULE}.ChoregosGuardrail",
                    "mode": "pre_call",
                    "default_on": True,
                },
            }
        ],
    }


def demo_connectors(provider: Provider | None = None) -> dict[str, tuple[str, dict[str, Any]]]:
    """The demo project's connectors, by kind: `(type, config)`."""
    connectors: dict[str, tuple[str, dict[str, Any]]] = {
        "tracker": ("internal", {}),
        "scm": ("fake", {}),
        "ci": ("fake", {}),
        "cd": ("fake", {}),
        "runtime": ("fake", {"simulate": True}),
        "gateway": ("fake", {}),
        "memory": ("lexical", {}),
        "notify": ("fake", {}),
    }
    if provider is not None:
        connectors["runtime"] = ("local_docker", {"network": LOCAL_NETWORK})
        connectors["gateway"] = ("litellm", {})
    return connectors


def demo_models(provider: Provider | None) -> dict[str, str]:
    """The demo project's model profiles: the provider's model names, not the `platform/*` aliases.

    What the console shows is then what runs. And a backend that checks the model family
    (`claude-code`, if someone switches the project to it) can see it: the orchestrator does not
    look behind an alias (`ModelResolver` is built without the gateway's catalogue in
    `activities/stage.py`, so `provider_model` stays empty), and the runner refused
    `platform/standard` at the start of every `claude-code` run (seen on 2026-10-12). Fakes mode
    keeps the platform's aliases.
    """
    if provider is None:
        return {}
    names = PROVIDER_MODELS[provider]
    return {
        "standard": names["platform/standard"],
        "strong": names["platform/strong"],
        "cheap": names["platform/cheap"],
    }


def _seeded(kind: str) -> list[tuple[str, dict[str, Any]]]:
    """What the seed may have written for `kind`, in any mode: anything else is a person's choice."""
    return [demo_connectors(p)[kind] for p in (None, "anthropic", "openai")]


def demo_backend(provider: Provider | None) -> str:
    return PROVIDER_BACKENDS[provider] if provider else "claude-code"


async def seed_local_demo(
    settings: Settings | None = None, provider: Provider | None = None
) -> dict[str, list[str]]:
    """Create what is missing of the demo organisation and project, switch what follows the mode;
    return what was created and what switched."""
    from .rbac import SYSTEM
    from .schemas.tickets import WorkItemCreate
    from .services import creer_un_ticket, ensure_defaults, publier_workflow
    from .services.initiator import initiator

    settings = settings or get_settings()
    org_slug = settings.bootstrap_org or "demo"
    backend = demo_backend(provider)
    report: dict[str, list[str]] = {
        "organisations": [],
        "projects": [],
        "connectors": [],
        "switched": [],
        "work_items": [],
    }
    async with session_scope(orgs="*") as session:
        org = (
            await session.execute(select(Organization).where(Organization.slug == org_slug))
        ).scalar_one_or_none()
        if org is None:
            org = Organization(slug=org_slug, name=settings.bootstrap_org_name or org_slug)
            session.add(org)
            await session.flush()
            report["organisations"].append(org_slug)

        project = (
            await session.execute(
                select(Project).where(Project.org_id == org.id, Project.slug == DEMO_PROJECT)
            )
        ).scalar_one_or_none()
        if project is None:
            project = Project(
                org_id=org.id,
                slug=DEMO_PROJECT,
                name="Demo project",
                status="active",
                config={
                    "slug": DEMO_PROJECT,
                    "org": org_slug,
                    "display_name": "Demo project",
                    "agent": {"default_backend": backend},
                    "models": {"profiles": demo_models(provider)},
                },
            )
            session.add(project)
            await session.flush()
            # The demo workflow first, as the default; `ensure_defaults` then only adds the policy.
            await publier_workflow(session, SYSTEM, project, DEMO_WORKFLOW, devient_le_defaut=True)
            await ensure_defaults(session, project)
            report["projects"].append(DEMO_PROJECT)
        else:
            agent = dict((project.config or {}).get("agent") or {})
            current = agent.get("default_backend", "claude-code")
            if current != backend and current in {demo_backend(p) for p in (None, "anthropic", "openai")}:
                project.config = {**project.config, "agent": {**agent, "default_backend": backend}}
                report["switched"].append("agent")
            models = dict((project.config or {}).get("models") or {})
            profiles = models.get("profiles") or {}
            wanted = demo_models(provider)
            if profiles != wanted and profiles in [demo_models(p) for p in (None, "anthropic", "openai")]:
                project.config = {**project.config, "models": {**models, "profiles": wanted}}
                report["switched"].append("models")

        existing = {
            c.kind: c
            for c in (
                await session.execute(select(Connector).where(Connector.project_id == project.id))
            ).scalars()
        }
        for kind, (type_name, config) in demo_connectors(provider).items():
            row = existing.get(kind)
            if row is None:
                session.add(
                    Connector(project_id=project.id, kind=kind, type=type_name, config=config, status="ok")
                )
                report["connectors"].append(kind)
            elif (
                kind in MODE_KINDS
                and (row.type, row.config) != (type_name, config)
                and (row.type, row.config) in _seeded(kind)
            ):
                row.type, row.config = type_name, config
                report["switched"].append(kind)
        await session.flush()

        # One work item, so that the first sign-in lands on something that moves. Started at once
        # in fakes mode (free); with a model key it waits for a person to start it: an install
        # does not spend someone's money before they have opened the console.
        has_items = (
            await session.execute(select(WorkItem.id).where(WorkItem.project_id == project.id).limit(1))
        ).first() is not None
        if not has_items:
            item = await creer_un_ticket(
                session,
                SYSTEM,
                project,
                WorkItemCreate(title=DEMO_TICKET_TITLE, body=DEMO_TICKET_BODY, start=provider is None),
                canal="local-install",
                initiated_by=initiator("demo"),
            )
            report["work_items"].append(item.tracker_key)
    logger.info(
        "local demo seeded", organisation=org_slug, project=DEMO_PROJECT, mode=provider or "fakes", **report
    )
    return report


def write_litellm_config(directory: pathlib.Path, env: Mapping[str, str]) -> Provider | None:
    """`config.yaml` for LiteLLM and `provider` for the seed, in `directory` (a shared volume)."""
    provider = model_provider(env)
    # Real embeddings only when real models are on: `LITELLM_REAL=0` keeps everything offline.
    embed = provider is not None and bool(env.get("OPENAI_API_KEY"))
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.yaml").write_text(
        yaml.safe_dump(litellm_config(provider, embed=embed), sort_keys=False), encoding="utf-8"
    )
    (directory / "provider").write_text(provider or "fakes", encoding="utf-8")
    # LiteLLM's image has no Choregos package: the guard travels as a file, stdlib only.
    guard_source = pathlib.Path(llm_guardrail.__file__).read_text(encoding="utf-8")
    (directory / f"{GUARDRAIL_MODULE}.py").write_text(guard_source, encoding="utf-8")
    return provider


def read_provider(path: pathlib.Path) -> Provider | None:
    """What `write_litellm_config` decided; no file means fakes."""
    value = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    if value == "anthropic":
        return "anthropic"
    if value == "openai":
        return "openai"
    return None


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    if sys.argv[1:2] == ["litellm-config"]:
        provider = write_litellm_config(pathlib.Path(sys.argv[2]), os.environ)
        logger.info("litellm configured", mode=provider or "fakes")
        return
    provider_file = pathlib.Path(os.environ.get("CHOREGOS_LOCAL_PROVIDER_FILE", "/litellm/provider"))
    asyncio.run(seed_local_demo(settings, read_provider(provider_file)))


if __name__ == "__main__":
    main()
