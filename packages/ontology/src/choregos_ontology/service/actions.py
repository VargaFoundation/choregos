# SPDX-License-Identifier: Apache-2.0
"""Le moteur d'actions de l'essai (éléments 4 à 6) : proposer, valider, exécuter, prouver.

Ce que la spec confie à `ApprovalWorkflow` et `ActionWorkflow` (Temporal, R-SOC-ACT-*), l'essai le
fait en ligne, dans la requête : c'est assez pour éprouver le contrat — préconditions, politique,
séparation des tâches, ré-authentification, effet `gitops.pull_request` ouvert par la plateforme,
preuves `connector.query` et `collector.rerun` — sans prétendre à la reprise sur panne.

- **Proposer** (un agent, par l'outil `action_<nom>`) : cibles lues, préconditions évaluées cible par
  cible, chemins de `gitops.pull_request` contrôlés contre `paths_allowed`, politique appliquée. Une
  proposition invalide n'est pas enregistrée ; une proposition valide est `pending_approval`, ou
  approuvée par la politique (`approve: auto`) et exécutée aussitôt.
- **Valider** (un humain, par la route de décision) : rôle exigé par la règle, séparation des tâches,
  et **authentification récente** si la règle a un `stepUp` — sinon 401 vers `?reauth=1`. La décision
  est consignée avec l'heure d'authentification (`auth_time`) qui l'a permise.
- **Exécuter** : les effets dans l'ordre ; `gitops.pull_request` passe par l'adaptateur SCM du cœur,
  qui détient le jeton — l'agent ne le voit jamais. Branche `choregos/<id de la proposition>`.
- **Prouver** : `object.reread`, `connector.query` sur les PR (via le SCM), et `collector.rerun`, dont
  la preuve arrive avec le rapport suivant du collecteur : une couche absente, une ligne `unreachable`,
  un rapport partiel ou un délai dépassé la font échouer, jamais « clé absente ».
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
from choregos_ontology.service.store import ActionProposal, ManagedObject, OntologyVersion

PENDING = "pending_approval"
REJECTED = "rejected"
RUNNING = "running"
AWAITING_EVIDENCE = "awaiting_evidence"
SUCCEEDED = "succeeded"
FAILED = "failed"
TERMINAL = frozenset({REJECTED, SUCCEEDED, FAILED})

#: Dans l'essai, tout run propose sous l'identité d'agent `platform` (les identités d'agents,
#: R-SOC-MCP-03, n'existent pas encore).
AGENT_IDENTITY = "platform"
REAUTH = "GET /api/v1/auth/login?reauth=1"
MAX_CHANGES_BYTES = 1024 * 1024
DEFAULT_WITHIN = "15m"
#: Rang minimal exigé par un rôle de l'ontologie, et rang des rôles du cœur.
ONTOLOGY_ROLES = {"viewer": 0, "contributor": 1, "owner": 2, "admin": 3}
CORE_ROLES = {"viewer": 0, "developer": 1, "release_captain": 1, "project_owner": 2, "org_admin": 3}
SERVED_EFFECTS = frozenset({"object.create", "object.update", "gitops.pull_request"})


class Refusal(Exception):  # noqa: N818 - une réponse, pas une panne
    """Refus motivé : `code` HTTP, et le corps rendu à l'appelant."""

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

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in {"kind": self.kind, "id": self.id, "run_id": self.run_id}.items() if v}


@dataclass(slots=True)
class Clock:
    """L'heure du moteur : remplaçable dans les tests, pour vieillir une authentification."""

    offset: timedelta = field(default_factory=timedelta)

    def now(self) -> datetime:
        return utcnow() + self.offset


@dataclass(slots=True)
class ScmOverride:
    """Le SCM de chaque projet, construit une fois par processus — un `FakeScm` neuf par appel
    oublierait ses PR entre l'effet et sa preuve. `scm` remplace celui de tous les projets (tests)."""

    scm: Any = None
    by_project: dict[str, Any] = field(default_factory=dict)


CLOCK = Clock()
SCM_OVERRIDE = ScmOverride()


def replace_scm(scm: Any) -> None:
    SCM_OVERRIDE.scm = scm
    SCM_OVERRIDE.by_project.clear()


async def project_scm(session: AsyncSession, project: Any) -> Any:
    """L'adaptateur SCM du projet, construit depuis son connecteur `scm` (GitHub par défaut)."""
    if SCM_OVERRIDE.scm is not None:
        return SCM_OVERRIDE.scm
    if project.id not in SCM_OVERRIDE.by_project:
        from choregos_adapters import build
        from choregos_api.db.models import Connector

        du_projet = select(Connector).where(Connector.project_id == project.id, Connector.kind == "scm")
        row = (await session.execute(du_projet)).scalars().first()
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
    """Les fichiers d'un `gitops.pull_request` rendu : chemins permis, taille bornée (AGT-014)."""
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
            # Fail-closed : une règle qu'on ne sait pas évaluer n'approuve jamais d'office.
            applies = rule.get("approve") != "auto"
        if applies:
            return policy, index, rule
    raise Refusal(422, {"outcome": "invalid", "message": f"no rule of {policy['name']!r} applies"})


def describe(proposal: ActionProposal) -> dict[str, Any]:
    return {
        "proposal": proposal.id,
        "action_type": proposal.action_type,
        "status": proposal.status,
        "target": list(proposal.target_ids),
        "params": dict(proposal.params),
        "justification": proposal.justification,
        "proposed_by": dict(proposal.proposed_by),
        "approval": dict(proposal.approval),
        "decisions": list(proposal.decisions),
        "effects": list(proposal.effects),
        "evidence": list(proposal.evidence),
        "created_at": proposal.created_at.isoformat() if proposal.created_at else None,
        "finished_at": proposal.finished_at.isoformat() if proposal.finished_at else None,
    }


async def _audit(
    session: AsyncSession, project: Any, principal: Any, what: str, proposal: ActionProposal, **extra: Any
) -> None:
    from choregos_api.audit import record

    await record(
        session,
        principal,
        f"ontology.proposal.{what}",
        org_id=project.org_id,
        target_type="proposal",
        target_id=proposal.id,
        action_type=proposal.action_type,
        **extra,
    )


# ───────────────────────────── proposer ─────────────────────────────


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
    """Crée une proposition, ou rend celle qui existe déjà pour la même clé d'idempotence.

    Un agent propose sous `agent:platform` ; un humain, s'il a l'un des rôles `role:<r>` de
    `permissions.propose` (`principal` et `org_slug` sont alors exigés).
    """
    ir = version.compiled_ir
    action = _action(ir, action_name)
    if actor.kind == "agent" and f"agent:{AGENT_IDENTITY}" not in action["propose"]:
        raise Refusal(403, {"error": f"agent:{AGENT_IDENTITY} may not propose {action_name}"})
    if actor.kind == "user":
        roles = [p.removeprefix("role:") for p in action["propose"] if p.startswith("role:")]
        rang = _rank(principal, org_slug, project.slug)
        if not any(rang >= ONTOLOGY_ROLES.get(r, 3) for r in roles):
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
        existing = (
            (
                await session.execute(
                    select(ActionProposal).where(
                        ActionProposal.project_id == project.id,
                        ActionProposal.action_type == action_name,
                        ActionProposal.idempotency_key == key,
                        ActionProposal.status.not_in([REJECTED, FAILED]),
                        ActionProposal.created_at >= since,
                    )
                )
            )
            .scalars()
            .first()
        )
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
    proposal = ActionProposal(
        id=proposal_id,
        project_id=project.id,
        version_id=version.id,
        action_type=action_name,
        target_ids=ids,
        params=params,
        justification=str(arguments.get("justification") or ""),
        status=PENDING,
        proposed_by=actor.as_dict(),
        approval={
            "policy": policy["name"],
            "rule": index,
            "mode": "auto" if rule.get("approve") == "auto" else "human",
            "approvers": list(rule.get("approvers") or []),
            "step_up_minutes": step_up,
            "separation_of_duties": bool(policy.get("separation_of_duties", True)),
        },
        decisions=[],
        effects=[],
        evidence=[],
        idempotency_key=key,
    )
    session.add(proposal)
    await session.flush()
    await _audit(session, project, None, "created", proposal, proposed_by=actor.as_dict())
    if proposal.approval["mode"] == "auto":
        proposal.decisions = [
            {"by": f"policy:{policy['name']}", "decision": "approve", "rule": index, "at": now.isoformat()}
        ]
        await execute(session, project, proposal, ir)
    return 201, describe(proposal)


# ───────────────────────────── valider ─────────────────────────────


def _rank(principal: Any, org: str, project_slug: str) -> int:
    roles = [principal.role_for(org, project_slug), principal.org_roles.get(org)]
    return max((CORE_ROLES.get(str(r), 0) for r in roles if r is not None), default=-1)


async def decide(
    session: AsyncSession,
    project: Any,
    org_slug: str,
    proposal: ActionProposal,
    principal: Any,
    decision: str,
    reason: str | None,
) -> dict[str, Any]:
    if decision not in {"approve", "reject"}:
        raise Refusal(400, {"error": "decision is `approve` or `reject`"})
    if principal.authentifie_le is None:
        # Seule une session humaine porte une heure d'authentification : un jeton d'API n'en a pas.
        refus = {"error": "decision_requires_session", "message": "a decision needs a human session"}
        raise Refusal(403, refus)
    if decision == "reject" and not (reason or "").strip():
        raise Refusal(422, {"error": "reason_required", "message": "a rejection needs a reason"})
    if proposal.status != PENDING:
        raise Refusal(409, {"error": f"the proposal is {proposal.status}, not {PENDING}"})
    approval = proposal.approval
    approvers = approval.get("approvers") or []
    # Une règle humaine sans approbateurs déclarés est tranchée par un `owner`.
    required = max((ONTOLOGY_ROLES.get(str(a.get("role")), 3) for a in approvers), default=2)
    if _rank(principal, org_slug, project.slug) < required:
        raise Refusal(403, {"error": f"deciding needs the role {approval.get('approvers')} on this project"})
    proposer = proposal.proposed_by or {}
    if (
        approval.get("separation_of_duties", True)
        and proposer.get("kind") == "user"
        and proposer.get("id") == principal.email
    ):
        raise Refusal(422, {"error": "separation_of_duties", "message": "the proposer does not decide"})
    now = CLOCK.now()
    auth_time = principal.authentifie_le
    age = None if auth_time is None else int(now.timestamp()) - int(auth_time)
    step_up = approval.get("step_up_minutes")
    if decision == "approve" and step_up is not None and (age is None or age > int(step_up) * 60):
        since = "an unknown time" if age is None else f"{age // 60} min"
        raise Refusal(
            401,
            {
                "error": "step_up_required",
                "message": f"approving needs an authentication less than {step_up} min old; "
                f"yours is {since} old",
                "reauth": REAUTH,
            },
        )
    entry = {
        "by": principal.email,
        "user_id": principal.user_id,
        "decision": decision,
        "reason": reason,
        "at": now.isoformat(),
        "auth_time": auth_time,
        "auth_age_seconds": age,
    }
    proposal.decisions = [*proposal.decisions, entry]
    await _audit(session, project, principal, "decided", proposal, decision=decision, auth_time=auth_time)
    if decision == "reject":
        proposal.status = REJECTED
        proposal.finished_at = now
        return describe(proposal)
    version = await session.get(OntologyVersion, proposal.version_id)
    assert version is not None  # FK, et une version n'est jamais supprimée seule
    await execute(session, project, proposal, version.compiled_ir)
    return describe(proposal)


# ───────────────────────────── exécuter ─────────────────────────────


async def _apply_effect(
    session: AsyncSession, project: Any, proposal: ActionProposal, effect_type: str, rendered: dict[str, Any]
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
        commit = await scm.commit_files(
            repo, branch, changes, str(rendered.get("title") or proposal.action_type)
        )
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


async def execute(session: AsyncSession, project: Any, proposal: ActionProposal, ir: dict[str, Any]) -> None:
    """Les effets dans l'ordre, puis les preuves ; un effet qui échoue arrête tout (`failed`)."""
    action = _action(ir, proposal.action_type)
    proposal.status = RUNNING
    targets = await _targets(session, project.id, action, list(proposal.target_ids))
    variables = _variables(
        proposal.id, project, dict(proposal.proposed_by), targets, dict(proposal.params), CLOCK.now()
    )
    results: list[dict[str, Any]] = []
    for effect in action["effects"]:
        try:
            rendered = cel.render(effect["with"], {**variables, "effects": results})
            result = await _apply_effect(session, project, proposal, effect["type"], rendered)
        except Exception as error:  # un effet en panne fait échouer la proposition, sans masquer l'erreur
            proposal.effects = [*results, {"type": effect["type"], "error": str(error)[:500]}]
            await _finish(session, project, proposal, FAILED, f"effect {effect['type']} failed")
            return
        results.append({"type": effect["type"], **result})
    proposal.effects = results
    await _collect(session, project, proposal, action, {**variables, "effects": results}, targets)


async def _finish(
    session: AsyncSession, project: Any, proposal: ActionProposal, status: str, reason: str
) -> None:
    proposal.status = status
    proposal.finished_at = CLOCK.now()
    await _audit(session, project, None, status, proposal, reason=reason)


# ───────────────────────────── prouver ─────────────────────────────


async def _collect(
    session: AsyncSession,
    project: Any,
    proposal: ActionProposal,
    action: dict[str, Any],
    variables: dict[str, Any],
    targets: list[dict[str, Any]],
) -> None:
    evidence: list[dict[str, Any]] = []
    waiting = False
    for item in action.get("evidence") or []:
        kind = item["collect"]["type"]
        entry: dict[str, Any] = {"name": item["name"], "type": kind, "expect": item["expect"]}
        try:
            collected = cel.render(item["collect"].get("with") or {}, variables)
            if kind == "collector.rerun":
                within = _duration(str(collected.get("within") or DEFAULT_WITHIN))
                entry.update(
                    {"status": "pending", "with": collected, "requested_at": CLOCK.now().isoformat()}
                )
                proposal.evidence_due_at = CLOCK.now() + within
                waiting = True
            else:
                result = await _collect_now(session, project, kind, collected, targets, variables["effects"])
                passed = cel.condition(item["expect"], {**variables, "result": result})
                entry.update({"status": "passed" if passed else "failed", "result": result})
        except Exception as error:  # une preuve impossible à collecter est une preuve fausse
            entry.update({"status": "failed", "error": str(error)[:500]})
        evidence.append(entry)
    proposal.evidence = evidence
    if any(e["status"] == "failed" for e in evidence):
        await _finish(session, project, proposal, FAILED, "evidence failed")
    elif waiting:
        proposal.status = AWAITING_EVIDENCE
        await _audit(session, project, None, "collector_rerun_requested", proposal)
    else:
        await _finish(session, project, proposal, SUCCEEDED, "all evidence passed")


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
        # Le dépôt de la PR : celui de l'effet qui l'a ouverte (la preuve ne le répète pas).
        repo = str(collected.get("repo") or next((e["repo"] for e in effects if e.get("pr") == number), ""))
        pr = await scm.get_pr(PrRef(repo=repo, number=number))
        # Le modèle du cœur ne distingue pas une PR fermée sans fusion : `open` ou `merged`.
        return {
            "number": number,
            "state": "merged" if pr.merged else "open",
            "head": pr.ref.head,
            "url": pr.ref.url,
        }
    raise RuntimeError(f"evidence {kind} ({collected.get('source')}) is not served by the trial")


async def on_report(
    session: AsyncSession, project: Any, observations: list[Observation] | None, partial_reason: str | None
) -> list[str]:
    """Un rapport du collecteur arrive : il tranche les preuves `collector.rerun` en attente.

    Le premier rapport reçu après la demande décide. Un rapport partiel, une couche absente ou une
    ligne `unreachable` pour la couche rendent la preuve impossible : `failed`, jamais « clé absente ».
    Rend les identifiants des propositions tranchées.
    """
    pending = (
        (
            await session.execute(
                select(ActionProposal).where(
                    ActionProposal.project_id == project.id, ActionProposal.status == AWAITING_EVIDENCE
                )
            )
        )
        .scalars()
        .all()
    )
    decided: list[str] = []
    for proposal in pending:
        if await expire(session, project, proposal):
            decided.append(proposal.id)
            continue
        evidence = [dict(e) for e in proposal.evidence]
        for entry in evidence:
            if entry.get("status") != "pending":
                continue
            layer, check = str(entry["with"].get("layer")), str(entry["with"].get("check"))
            scope = entry["with"].get("scope")
            if partial_reason is not None or observations is None:
                entry.update({"status": "failed", "error": f"partial report: {partial_reason}"})
                continue
            lines = [o for o in observations if o.layer == layer]
            if not lines:
                entry.update({"status": "failed", "error": f"layer {layer!r} is absent from the report"})
                continue
            if any(o.status == "unreachable" for o in lines):
                entry.update({"status": "failed", "error": f"layer {layer!r} has unreachable lines"})
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
            variables = {"result": result, "proposal": {"id": proposal.id}, "params": dict(proposal.params)}
            try:
                passed = cel.condition(entry["expect"], variables)
            except cel.CelError as error:
                entry.update({"status": "failed", "error": str(error)[:500], "result": result, "fact": fact})
                continue
            entry.update({"status": "passed" if passed else "failed", "result": result, "fact": fact})
        proposal.evidence = evidence
        if any(e.get("status") == "pending" for e in evidence):
            continue
        status = FAILED if any(e.get("status") == "failed" for e in evidence) else SUCCEEDED
        await _finish(session, project, proposal, status, "collector rerun")
        decided.append(proposal.id)
    return decided


async def expire(session: AsyncSession, project: Any, proposal: ActionProposal) -> bool:
    """Une preuve `collector.rerun` dont le délai est passé échoue (« délai dépassé »)."""
    if proposal.status != AWAITING_EVIDENCE or proposal.evidence_due_at is None:
        return False
    due = (
        proposal.evidence_due_at
        if proposal.evidence_due_at.tzinfo
        else proposal.evidence_due_at.replace(tzinfo=CLOCK.now().tzinfo)
    )
    if CLOCK.now() <= due:
        return False
    proposal.evidence = [
        {**e, "status": "failed", "error": "no complete report within the delay"}
        if e.get("status") == "pending"
        else e
        for e in proposal.evidence
    ]
    await _finish(session, project, proposal, FAILED, "collector rerun timed out")
    return True
