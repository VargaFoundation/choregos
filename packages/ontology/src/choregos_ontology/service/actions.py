# SPDX-License-Identifier: Apache-2.0
"""The ontology's action engine (trial items 4 to 6): propose, approve, execute, prove — on the
CORE's governed actions (ADR 0035, S20-08).

The trial did everything in the request that decided. A proposal is now a core action (origin
`ontology`): it is decided by the core (`decider`) — human session, rank, separation of duties, recent
authentication when the rule has a `stepUp` —, and the `ActionWorkflow` plays it in Temporal, effect by
effect under its key; what was done is compensated if what follows fails.

- **Propose** (an agent, through the `action_<name>` tool; a person, through the route or the MCP gate):
  targets read, preconditions evaluated target by target, paths of `gitops.pull_request` checked
  against `paths_allowed`, policy applied. An invalid proposal is not recorded; a valid one waits for a
  decision, or is born approved by the policy (`approve: auto`) and leaves for Temporal at once.
- **Execute**: each effect of the ontology is a core effect, `ontology.effet`; `gitops.pull_request`
  goes through the core's SCM adapter, which holds the token — the agent never sees it. Branch
  `choregos/<proposal id>`.
- **Prove**: each piece of evidence is an effect, `ontology.preuve`. `object.reread` and
  `connector.query` are judged at once; `collector.rerun` makes the action WAIT (`awaiting_evidence`)
  for the collector's next report, which hands it back (`on_report`): an absent layer, an
  `unreachable` line, a partial report or a timeout fail it, never "key absent".
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from choregos_api.db.base import utcnow, uuid7
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from choregos_ontology import cel
from choregos_ontology.observations import Observation
from choregos_ontology.service.store import ManagedObject, OntologyVersion

PENDING = "pending_approval"
APPROVED = "approved"
REJECTED = "rejected"
RUNNING = "running"
AWAITING_EVIDENCE = "awaiting_evidence"
SUCCEEDED = "succeeded"
FAILED = "failed"
TERMINAL = frozenset({REJECTED, SUCCEEDED, FAILED})
#: The origin of a core action born from the ontology, and the two effects the plugin declares.
#: Their VALUES are persisted on every action row: they stay as they are.
ORIGIN = "ontology"
EFFECT = "ontology.effet"  # lang: fr-ok - persisted effect kind
EVIDENCE = "ontology.preuve"  # lang: fr-ok - persisted effect kind
#: The key of a core action's `params` where the ontology keeps what it knows of the action (persisted).
PARAMS_KEY = "ontologie"  # lang: fr-ok - persisted key

#: In the trial, every run proposes under the agent identity `platform` (agent identities,
#: R-SOC-MCP-03, do not exist yet).
AGENT_IDENTITY = "platform"
REAUTH = "GET /api/v1/auth/login?reauth=1"
MAX_CHANGES_BYTES = 1024 * 1024
DEFAULT_WITHIN = "15m"
#: The minimal rank an ontology role demands, and the rank of the core's roles.
ONTOLOGY_ROLES = {"viewer": 0, "contributor": 1, "owner": 2, "admin": 3}
CORE_ROLES = {"viewer": 0, "developer": 1, "release_captain": 1, "project_owner": 2, "org_admin": 3}
#: The core role that stands for an ontology role when deciding (the core counts in core roles).
CORE_ROLE_FOR = {
    "viewer": "developer",
    "contributor": "developer",
    "owner": "project_owner",
    "admin": "org_admin",
}
SERVED_EFFECTS = frozenset({"object.create", "object.update", "gitops.pull_request"})


class Refusal(Exception):  # noqa: N818 - an answer, not a failure
    """A reasoned refusal: an HTTP `code`, and the body returned to the caller."""

    def __init__(self, code: int, body: dict[str, Any]) -> None:
        super().__init__(body.get("message") or body.get("error") or str(body))
        self.code = code
        self.body = body


@dataclass(frozen=True, slots=True)
class Actor:
    kind: str  # agent | user
    id: str
    run_id: str | None = None
    user_id: str | None = None
    #: Where the proposal came from: `mcp` for the gate of external clients (ADR 0030).
    via: str | None = None

    def as_dict(self) -> dict[str, Any]:
        fields = {"kind": self.kind, "id": self.id, "run_id": self.run_id, "via": self.via}
        return {k: v for k, v in fields.items() if v}


@dataclass(slots=True)
class Clock:
    """The engine's time: replaceable in tests, to age an authentication."""

    offset: timedelta = field(default_factory=timedelta)

    def now(self) -> datetime:
        return utcnow() + self.offset


@dataclass(slots=True)
class ScmOverride:
    """Each project's SCM, built once per process — a new `FakeScm` on every call would forget its
    PRs between the effect and its evidence. `scm` replaces every project's (tests)."""

    scm: Any = None
    by_project: dict[str, Any] = field(default_factory=dict)


CLOCK = Clock()
SCM_OVERRIDE = ScmOverride()


def replace_scm(scm: Any) -> None:
    SCM_OVERRIDE.scm = scm
    SCM_OVERRIDE.by_project.clear()


async def project_scm(session: AsyncSession, project: Any) -> Any:
    """The project's SCM adapter, built from its `scm` connector (GitHub by default)."""
    if SCM_OVERRIDE.scm is not None:
        return SCM_OVERRIDE.scm
    if project.id not in SCM_OVERRIDE.by_project:
        from choregos_adapters import build
        from choregos_api.db.models import Connector

        of_project = select(Connector).where(Connector.project_id == project.id, Connector.kind == "scm")
        row = (await session.execute(of_project)).scalars().first()
        type_, config = (row.type, dict(row.config)) if row else ("github", {})
        SCM_OVERRIDE.by_project[project.id] = build("scm", type_, config)
    return SCM_OVERRIDE.by_project[project.id]


def _duration(text: str) -> timedelta:
    match = re.fullmatch(r"(\d+)([smhd])", text.strip())
    if match is None:
        raise ValueError(f"invalid duration {text!r}")
    amount, unit = int(match.group(1)), match.group(2)
    return timedelta(**{{"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}[unit]: amount})


def _action(ir: dict[str, Any], name: str) -> dict[str, Any]:
    found = next((a for a in ir.get("action_types", []) if a["name"] == name), None)
    if found is None:
        raise Refusal(404, {"error": f"unknown action {name!r}"})
    return dict(found)


async def _targets(
    session: AsyncSession, project_id: str, action: dict[str, Any], ids: list[str]
) -> list[dict[str, Any]]:
    cardinality = action["cardinality"]
    if cardinality == "none" and ids:
        raise Refusal(400, {"error": f"{action['name']} takes no target"})
    if cardinality == "one" and len(ids) != 1:
        raise Refusal(400, {"error": f"{action['name']} takes exactly one target"})
    if cardinality == "many" and not ids:
        raise Refusal(400, {"error": f"{action['name']} takes at least one target"})
    targets = []
    for ident in ids:
        row = await session.get(ManagedObject, (project_id, action["target_type"], ident))
        if row is None or row.deleted_at is not None:
            raise Refusal(404, {"error": f"{action['target_type']} {ident!r} not found"})
        targets.append(dict(row.properties or {}))
    return targets


def _variables(
    proposal_id: str,
    project: Any,
    actor: dict[str, Any],
    targets: list[dict[str, Any]],
    params: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    return {
        "target": targets[0] if targets else {},
        "targets": targets,
        "params": params,
        "actor": actor,
        "project": {"id": project.id, "slug": project.slug},
        "now": now,
        "proposal": {"id": proposal_id},
    }


def _allowed(path: str, patterns: list[str]) -> bool:
    if path.startswith("/") or ".." in path.split("/"):
        return False
    return any(fnmatch.fnmatchcase(path, pattern.replace("**", "*")) for pattern in patterns)


def _check_pull_request(rendered: dict[str, Any]) -> dict[str, str]:
    """The files of a rendered `gitops.pull_request`: allowed paths, bounded size (AGT-014)."""
    files = ((rendered.get("changes") or {}).get("files")) or []
    if not files:
        raise Refusal(422, {"outcome": "invalid", "message": "gitops.pull_request without files"})
    patterns = list(rendered.get("paths_allowed") or [])
    changes: dict[str, str] = {}
    for item in files:
        path, content = str(item.get("path", "")), str(item.get("content", ""))
        if not _allowed(path, patterns):
            raise Refusal(
                422, {"outcome": "invalid", "message": f"path {path!r} is outside paths_allowed {patterns}"}
            )
        changes[path] = content
    if sum(len(c.encode()) for c in changes.values()) > MAX_CHANGES_BYTES:
        raise Refusal(413, {"outcome": "invalid", "message": "changes larger than 1 MiB"})
    return changes


def _policy_rule(ir: dict[str, Any], action: dict[str, Any]) -> tuple[dict[str, Any], int, dict[str, Any]]:
    policy = next((p for p in ir.get("policies", []) if p["name"] == action["approve_policy"]), None)
    if policy is None:
        raise Refusal(500, {"error": f"policy {action['approve_policy']!r} is not in the active ontology"})
    subject = {
        "action": {"type": action["name"], "risk": action["risk"], "reversibility": action["reversibility"]}
    }
    for index, rule in enumerate(policy["rules"]):
        try:
            applies = cel.condition(rule["when"], subject)
        except cel.CelError:
            # Fail-closed: a rule that cannot be evaluated never approves on its own.
            applies = rule.get("approve") != "auto"
        if applies:
            return policy, index, rule
    raise Refusal(422, {"outcome": "invalid", "message": f"no rule of {policy['name']!r} applies"})


def _meta(action: Any) -> dict[str, Any]:
    """What the ontology knows of one of its actions: its version, its type, its targets, its params."""
    return dict((action.params or {}).get(PARAMS_KEY) or {})


def describe(action: Any) -> dict[str, Any]:
    """The proposal as the trial described it — its file —, read from the core action."""
    meta, result = _meta(action), dict(action.result or {})
    return {
        "proposal": action.id,
        "action_type": meta.get("action_type"),
        "status": action.status,
        "target": list(meta.get("target_ids") or []),
        "params": dict(meta.get("params") or {}),
        "justification": action.justification or "",
        "proposed_by": dict(action.proposed_by or {}),
        "approval": dict(action.approval or {}),
        "decisions": list(action.decisions or []),
        "effects": list(result.get("effects") or []),
        "evidence": list(result.get("evidence") or []),
        "error": action.error,
        "created_at": action.created_at.isoformat() if action.created_at else None,
        "finished_at": action.finished_at.isoformat() if action.finished_at else None,
    }


async def _audit(
    session: AsyncSession, project: Any, principal: Any, what: str, action: Any, **extra: Any
) -> None:
    from choregos_api.audit import record

    await record(
        session,
        principal,
        f"ontology.proposal.{what}",
        org_id=project.org_id,
        target_type="proposal",
        target_id=action.id,
        action_type=_meta(action).get("action_type"),
        **extra,
    )


async def ontology_actions(session: AsyncSession, project_id: str, status: str | None = None) -> list[Any]:
    """The core actions born from the ontology, for this project, in the order they were proposed."""
    from choregos_api.db.models import Action

    query = select(Action).where(Action.project_id == project_id, Action.origin == ORIGIN)
    if status:
        query = query.where(Action.status == status)
    return list((await session.execute(query.order_by(Action.created_at))).scalars().all())


async def get_action(session: AsyncSession, project_id: str, action_id: str) -> Any:
    from choregos_api.db.models import Action

    action = await session.get(Action, action_id)
    if action is None or action.project_id != project_id or action.origin != ORIGIN:
        raise Refusal(404, {"error": f"proposal {action_id!r} not found"})
    return action


# ───────────────────────────── propose ─────────────────────────────


async def _already_proposed(
    session: AsyncSession, project_id: str, action_name: str, key: str, since: datetime
) -> Any:
    """The proposal still alive that carries the same idempotency key, within the window."""
    for existing in reversed(await ontology_actions(session, project_id)):
        meta = _meta(existing)
        created = existing.created_at
        if created is not None and created.tzinfo is None:
            created = created.replace(tzinfo=since.tzinfo)
        if (
            meta.get("action_type") == action_name
            and meta.get("idempotency_key") == key
            and existing.status not in {REJECTED, FAILED}
            and (created is None or created >= since)
        ):
            return existing
    return None


async def propose(
    session: AsyncSession,
    project: Any,
    version: OntologyVersion,
    action_name: str,
    arguments: dict[str, Any],
    actor: Actor,
    principal: Any = None,
    org_slug: str = "",
) -> tuple[int, dict[str, Any]]:
    """Create the proposal — a core action —, or return the one that already exists for the same
    idempotency key.

    An agent proposes as `agent:platform`; a person, if they hold one of the `role:<r>` of
    `permissions.propose` (`principal` and `org_slug` are then required).
    """
    ir = version.compiled_ir
    action = _action(ir, action_name)
    if actor.kind == "agent" and f"agent:{AGENT_IDENTITY}" not in action["propose"]:
        raise Refusal(403, {"error": f"agent:{AGENT_IDENTITY} may not propose {action_name}"})
    if actor.kind == "user":
        roles = [p.removeprefix("role:") for p in action["propose"] if p.startswith("role:")]
        rank = _rank(principal, org_slug, project.slug)
        if not any(rank >= ONTOLOGY_ROLES.get(r, 3) for r in roles):
            raise Refusal(403, {"error": f"proposing {action_name} needs one of {action['propose']}"})
    ids = [str(i) for i in arguments.get("target") or []]
    targets = await _targets(session, project.id, action, ids)
    params = dict(arguments.get("params") or {})
    proposal_id = uuid7()
    now = CLOCK.now()
    variables = _variables(proposal_id, project, actor.as_dict(), targets, params, now)

    key = None
    if action.get("idempotency_key"):
        key = str(cel.render(action["idempotency_key"], variables))
        since = now - _duration(action.get("idempotency_window") or "24h")
        existing = await _already_proposed(session, project.id, action_name, key, since)
        if existing is not None:
            return 200, {**describe(existing), "idempotent_replay": True}

    for target in targets or [{}]:
        for precondition in action.get("preconditions") or []:
            try:
                ok = cel.condition(precondition["expr"], {**variables, "target": target})
                detail = None
            except cel.CelError as error:
                ok, detail = False, str(error)
            if not ok:
                raise Refusal(
                    422,
                    {
                        "outcome": "invalid",
                        "precondition": precondition["name"],
                        "message": precondition.get("message") or precondition["name"],
                        **({"detail": detail} if detail else {}),
                    },
                )

    for effect in action["effects"]:
        if effect["type"] not in SERVED_EFFECTS:
            raise Refusal(
                501, {"outcome": "invalid", "message": f"effect {effect['type']} is not served by the trial"}
            )
        if effect["type"] == "gitops.pull_request":
            for target in targets or [{}]:
                _check_pull_request(
                    cel.render(effect["with"], {**variables, "target": target, "effects": []})
                )

    policy, index, rule = _policy_rule(ir, action)
    step_up = (rule.get("step_up") or {}).get("maxAgeMinutes")
    approvers = [
        {"role": CORE_ROLE_FOR.get(str(a.get("role")), "project_owner"), "min": int(a.get("min", 1))}
        for a in rule.get("approvers") or []
    ] or [{"role": "project_owner", "min": 1}]  # a human rule without approvers: an `owner`
    evidence = list(action.get("evidence") or [])
    effects = [{"effect": EFFECT, "with": {"index": i}} for i in range(len(action["effects"]))] + [
        {"effect": EVIDENCE, "with": {"index": j, "position": len(action["effects"]) + j}}
        for j in range(len(evidence))
    ]
    from choregos_api.schemas.actions import ActionCreate
    from choregos_api.services.actions import proposer

    body = ActionCreate.model_validate(
        {
            "kind": f"ontology.{action_name}"[:128],
            "title": f"{action_name} on {', '.join(ids) or 'nothing'}"[:300],
            "justification": str(arguments.get("justification") or "") or None,
            "params": {
                PARAMS_KEY: {
                    "version_id": version.id,
                    "action_type": action_name,
                    "target_ids": ids,
                    "params": params,
                    "idempotency_key": key,
                }
            },
            "effects": effects,
        }
    )
    from choregos_api.rbac import SYSTEM

    created = await proposer(
        session,
        project,
        body,
        origine=ORIGIN,
        propose_par=actor.as_dict(),
        principal=principal or SYSTEM,
        run_id=actor.run_id,
        action_id=proposal_id,
    )
    # The ontology's rule, as it decides: its approvers (in core roles), and `step_up_minutes` absent
    # when the rule demands no recent authentication.
    created.approval = {
        "policy": policy["name"],
        "rule": index,
        "mode": "auto" if rule.get("approve") == "auto" else "human",
        "approvers": approvers,
        "ontology_approvers": list(rule.get("approvers") or []),
        "step_up_minutes": step_up,
        "separation_of_duties": bool(policy.get("separation_of_duties", True)),
    }
    created.result = {"effects": [], "evidence": []}
    await session.flush()
    await _audit(session, project, None, "created", created, proposed_by=actor.as_dict())
    if created.approval["mode"] == "auto":
        from choregos_api.temporal import action_id as temporal_action_id
        from choregos_api.temporal import get_temporal

        created.status = APPROVED
        created.decisions = [
            {"by": f"policy:{policy['name']}", "decision": "approve", "rule": index, "at": now.isoformat()}
        ]
        created.temporal_wf_id = await get_temporal().start_action(
            temporal_action_id(created.id), {"action_id": created.id}
        )
    return 201, describe(created)


# ───────────────────────────── approve ─────────────────────────────


def _rank(principal: Any, org: str, project_slug: str) -> int:
    roles = [principal.role_for(org, project_slug), principal.org_roles.get(org)]
    return max((CORE_ROLES.get(str(r), 0) for r in roles if r is not None), default=-1)


async def decide(
    session: AsyncSession,
    project: Any,
    org_slug: str,
    action: Any,
    principal: Any,
    decision: str,
    reason: str | None,
) -> dict[str, Any]:
    """The human decision, taken by the CORE (`decider`): human session, rank, separation of duties,
    recent authentication if the rule has a `stepUp` — and, once approved, the action leaves for
    Temporal. Nothing runs in this request."""
    from choregos_api.services.actions import decider

    decided = await decider(session, project, org_slug, action.id, decision, reason, principal)
    await _audit(session, project, principal, "decided", decided, decision=decision)
    return describe(decided)


# ───────────────────────────── execute: the `ontology.effet` effect ─────────────────────────────


async def _apply_effect(
    session: AsyncSession, project: Any, action_type: str, effect_type: str, rendered: dict[str, Any]
) -> dict[str, Any]:
    if effect_type == "gitops.pull_request":
        changes = _check_pull_request(rendered)
        scm = await project_scm(session, project)
        repo, branch, base = (
            str(rendered["repo"]),
            str(rendered["branch"]),
            str(rendered.get("base") or "main"),
        )
        await scm.ensure_branch(repo, branch, base)
        commit = await scm.commit_files(repo, branch, changes, str(rendered.get("title") or action_type))
        ref = await scm.open_pr(
            repo, branch, base, str(rendered.get("title") or ""), str(rendered.get("body") or ""), False
        )
        return {
            "pr": ref.number,
            "url": ref.url,
            "head": branch,
            "repo": repo,
            "commit": commit,
            "state": "open",
        }
    object_type = str(rendered["objectType"])
    if effect_type == "object.update":
        row = await session.get(ManagedObject, (project.id, object_type, str(rendered["id"])))
        if row is None or row.deleted_at is not None:
            raise RuntimeError(f"{object_type} {rendered['id']!r} not found")
        row.properties = {**(row.properties or {}), **dict(rendered.get("properties") or {})}
        row.row_version += 1
        return {"id": row.id}
    # object.create
    properties = dict(rendered.get("properties") or {})
    ident = str(rendered.get("id") or properties.get("key") or properties.get("id") or uuid7())
    if await session.get(ManagedObject, (project.id, object_type, ident)) is not None:
        return {"id": ident, "existing": True}
    session.add(
        ManagedObject(project_id=project.id, object_type=object_type, id=ident, properties=properties)
    )
    return {"id": ident}


async def _context(ctx: Any) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """The ontology's action, its targets read again and its CEL variables, for a core effect."""
    meta = _meta(ctx.action)
    version = await ctx.session.get(OntologyVersion, meta["version_id"])
    if version is None:
        raise RuntimeError(f"ontology version {meta['version_id']!r} not found")
    action = _action(version.compiled_ir, str(meta["action_type"]))
    targets = await _targets(ctx.session, ctx.project.id, action, list(meta.get("target_ids") or []))
    variables = _variables(
        ctx.action.id,
        ctx.project,
        dict(ctx.action.proposed_by or {}),
        targets,
        dict(meta.get("params") or {}),
        CLOCK.now(),
    )
    return meta, action, targets, variables


def _record(action: Any, kind: str, index: int, entry: dict[str, Any]) -> None:
    """The action's file: its effects and its evidence, in order, once each — an effect replayed after
    a failure is not written twice."""
    result = dict(action.result or {})
    entries = [e for e in result.get(kind) or [] if e.get("index") != index]
    result[kind] = sorted([*entries, {**entry, "index": index}], key=lambda e: int(e["index"]))
    action.result = result


async def _record_failure(ctx: Any, kind: str, index: int, entry: dict[str, Any]) -> None:
    """A refusal rolls the activity's transaction back: what it says is recorded in ITS OWN, after
    undoing what the effect had started — the file says why the action failed."""
    from choregos_api.db.session import limiter_aux_organisations

    action_id, model = ctx.action.id, type(ctx.action)
    await ctx.session.rollback()
    await limiter_aux_organisations(ctx.session, "*")
    action = await ctx.session.get(model, action_id)
    if action is None:
        return
    _record(action, kind, index, entry)
    await ctx.session.commit()


async def ontology_effect(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """`ontology.effet`: effect `index` of the ontology's action, rendered with the results of the
    effects already done. A failing effect fails the action, without hiding the error."""
    from choregos_api.effets import EffetRefuse

    index = int(params["index"])
    meta, action, _, variables = await _context(ctx)
    effect = action["effects"][index]
    done = [e for e in (ctx.action.result or {}).get("effects") or [] if int(e.get("index", -1)) < index]
    try:
        rendered = cel.render(effect["with"], {**variables, "effects": done})
        result = await _apply_effect(
            ctx.session, ctx.project, str(meta["action_type"]), effect["type"], rendered
        )
    except Exception as error:  # a failing effect fails the action, without hiding the error
        await _record_failure(ctx, "effects", index, {"type": effect["type"], "error": str(error)[:500]})
        raise EffetRefuse(f"effect {effect['type']} failed: {error}") from error
    entry = {"type": effect["type"], **result}
    _record(ctx.action, "effects", index, entry)
    return entry


# ───────────────────────────── prove: the `ontology.preuve` effect ─────────────────────────────


async def ontology_evidence(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """`ontology.preuve`: evidence `index`. `object.reread` and `connector.query` are judged here;
    `collector.rerun` makes the action wait for the collector's next report, or the timeout."""
    from choregos_api.effets import EffetRefuse

    index, position = int(params["index"]), int(params["position"])
    _, action, targets, variables = await _context(ctx)
    item = (action.get("evidence") or [])[index]
    effects = list((ctx.action.result or {}).get("effects") or [])
    variables = {**variables, "effects": effects}
    kind = item["collect"]["type"]
    entry: dict[str, Any] = {
        "name": item["name"],
        "type": kind,
        "expect": item["expect"],
        "position": position,
    }
    try:
        collected = cel.render(item["collect"].get("with") or {}, variables)
        if kind == "collector.rerun":
            within = _duration(str(collected.get("within") or DEFAULT_WITHIN))
            until = CLOCK.now() + within
            entry.update({"status": "pending", "with": collected, "requested_at": CLOCK.now().isoformat()})
            _record(ctx.action, "evidence", index, entry)
            # The core's `ActionWorkflow` reads these two keys: they are its protocol, not prose.
            return {**entry, "attendre_une_preuve": {"jusqu_a": until.isoformat()}}  # lang: fr-ok
        result = await _collect_now(ctx.session, ctx.project, kind, collected, targets, effects)
        passed = cel.condition(item["expect"], {**variables, "result": result})
        entry.update({"status": "passed" if passed else "failed", "result": result})
    except Exception as error:  # evidence that cannot be collected is failed evidence
        entry.update({"status": "failed", "error": str(error)[:500]})
    if entry["status"] == "failed":
        await _record_failure(ctx, "evidence", index, entry)
        raise EffetRefuse(f"evidence {item['name']} failed: {entry.get('error') or entry.get('result')}")
    _record(ctx.action, "evidence", index, entry)
    return entry


async def _collect_now(
    session: AsyncSession,
    project: Any,
    kind: str,
    collected: dict[str, Any],
    targets: list[dict[str, Any]],
    effects: list[dict[str, Any]],
) -> Any:
    if kind == "object.reread":
        return targets[0] if targets else {}
    if kind == "connector.query" and collected.get("source") == "pull_requests":
        from choregos_core.domain import PrRef

        scm = await project_scm(session, project)
        number = int((collected.get("filters") or {})["number"])
        # The PR's repository: the one of the effect that opened it (the evidence does not repeat it).
        repo = str(collected.get("repo") or next((e["repo"] for e in effects if e.get("pr") == number), ""))
        pr = await scm.get_pr(PrRef(repo=repo, number=number))
        # The core's model does not tell a PR closed without a merge: `open` or `merged`.
        return {
            "number": number,
            "state": "merged" if pr.merged else "open",
            "head": pr.ref.head,
            "url": pr.ref.url,
        }
    raise RuntimeError(f"evidence {kind} ({collected.get('source')}) is not served by the trial")


# ───────────────────────────── the collector's next report ─────────────────────────────


async def on_report(
    session: AsyncSession, project: Any, observations: list[Observation] | None, partial_reason: str | None
) -> list[str]:
    """A collector report arrives: it settles the pending `collector.rerun` evidence, and HANDS it back
    to its action (`signaler_une_preuve`), which resumes or fails — in Temporal.

    The first report received after the request decides. A partial report, an absent layer or an
    `unreachable` line for the layer make the evidence impossible: `failed`, never "key absent".
    Returns the ids of the actions settled.
    """
    from choregos_api.services.actions import signaler_une_preuve

    decided: list[str] = []
    signals: list[tuple[str, int, bool, str]] = []
    for action in await ontology_actions(session, project.id, AWAITING_EVIDENCE):
        result_doc = dict(action.result or {})
        evidence = [dict(e) for e in result_doc.get("evidence") or []]
        settled: list[dict[str, Any]] = []
        for entry in evidence:
            if entry.get("status") != "pending":
                continue
            layer, check = str(entry["with"].get("layer")), str(entry["with"].get("check"))
            scope = entry["with"].get("scope")
            if partial_reason is not None or observations is None:
                entry.update({"status": "failed", "error": f"partial report: {partial_reason}"})
                settled.append(entry)
                continue
            lines = [o for o in observations if o.layer == layer]
            if not lines:
                entry.update({"status": "failed", "error": f"layer {layer!r} is absent from the report"})
                settled.append(entry)
                continue
            if any(o.status == "unreachable" for o in lines):
                entry.update({"status": "failed", "error": f"layer {layer!r} has unreachable lines"})
                settled.append(entry)
                continue
            key_present = any(o.status == "finding" and o.check == check for o in lines)
            line = next((o for o in lines if o.check == check and (scope is None or o.scope == scope)), None)
            result = {
                "key_present": key_present,
                "line": None
                if line is None
                else {"scope": line.scope, "status": line.status, "value": line.value},
            }
            fact = {
                "name": "collector_clean",
                "scope": f"{layer}-{check}",
                "value": not key_present,
                "source": "collector.rerun",
            }
            meta = _meta(action)
            variables = {
                "result": result,
                "proposal": {"id": action.id},
                "params": dict(meta.get("params") or {}),
            }
            try:
                passed = cel.condition(entry["expect"], variables)
            except cel.CelError as error:
                entry.update({"status": "failed", "error": str(error)[:500], "result": result, "fact": fact})
                settled.append(entry)
                continue
            entry.update({"status": "passed" if passed else "failed", "result": result, "fact": fact})
            settled.append(entry)
        if not settled:
            continue
        action.result = {**result_doc, "evidence": evidence}
        signals.extend(
            (
                action.id,
                int(entry["position"]),
                entry["status"] == "passed",
                str(entry.get("error") or f"{entry['name']}: {entry['status']}"),
            )
            for entry in settled
        )
        decided.append(action.id)
    if signals:
        # Commit BEFORE the signals (#440). A signal wakes the action's workflow at once, and its next
        # activities (`marquer_l_attente`, `cloturer_l_action`) read `action.result`, change it and
        # write it back. Signalled before this request's commit, they read the result without the
        # evidence just settled, and their write, landing after ours, erased it: the action succeeded
        # with evidence that had no `result`.
        await session.commit()
    for action_id, position, ok, detail in signals:
        await signaler_une_preuve(action_id, position, ok=ok, detail=detail)
    return decided
