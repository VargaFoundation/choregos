# SPDX-License-Identifier: Apache-2.0
"""Le moteur d'actions de l'ontologie (éléments 4 à 6) : proposer, valider, exécuter, prouver —
sur les actions gouvernées du CŒUR (ADR 0035, S20-08).

L'essai faisait tout dans la requête qui décidait. Une proposition est désormais une action du cœur
(origine `ontology`) : elle se décide par `decider` — session humaine, rang, séparation des tâches,
authentification récente quand la règle a un `stepUp` —, et l'`ActionWorkflow` la joue, dans Temporal,
effet par effet sous sa clé ; ce qui était fait se compense si la suite échoue.

- **Proposer** (un agent, par l'outil `action_<nom>` ; un humain, par la route ou la porte MCP) :
  cibles lues, préconditions évaluées cible par cible, chemins de `gitops.pull_request` contrôlés
  contre `paths_allowed`, politique appliquée. Une proposition invalide n'est pas enregistrée ; une
  proposition valide attend une décision, ou naît approuvée par la politique (`approve: auto`) et
  part aussitôt dans Temporal.
- **Exécuter** : chaque effet de l'ontologie est un effet du cœur, `ontology.effet` ;
  `gitops.pull_request` passe par l'adaptateur SCM du cœur, qui détient le jeton — l'agent ne le voit
  jamais. Branche `choregos/<id de la proposition>`.
- **Prouver** : chaque preuve est un effet, `ontology.preuve`. `object.reread` et `connector.query`
  se jugent aussitôt ; `collector.rerun` fait ATTENDRE l'action (`awaiting_evidence`) jusqu'au rapport
  suivant du collecteur, qui la lui remet (`on_report`) : une couche absente, une ligne
  `unreachable`, un rapport partiel ou un délai dépassé la font échouer, jamais « clé absente ».
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
#: L'origine d'une action du cœur née de l'ontologie, et les deux effets que le greffon déclare.
ORIGINE = "ontology"
EFFET = "ontology.effet"
PREUVE = "ontology.preuve"

#: Dans l'essai, tout run propose sous l'identité d'agent `platform` (les identités d'agents,
#: R-SOC-MCP-03, n'existent pas encore).
AGENT_IDENTITY = "platform"
REAUTH = "GET /api/v1/auth/login?reauth=1"
MAX_CHANGES_BYTES = 1024 * 1024
DEFAULT_WITHIN = "15m"
#: Rang minimal exigé par un rôle de l'ontologie, et rang des rôles du cœur.
ONTOLOGY_ROLES = {"viewer": 0, "contributor": 1, "owner": 2, "admin": 3}
CORE_ROLES = {"viewer": 0, "developer": 1, "release_captain": 1, "project_owner": 2, "org_admin": 3}
#: Le rôle du cœur qui tient lieu d'un rôle de l'ontologie pour décider (`decider` compte en rôles du cœur).
ROLE_DU_COEUR = {
    "viewer": "developer",
    "contributor": "developer",
    "owner": "project_owner",
    "admin": "org_admin",
}
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
    #: Par où la proposition est arrivée : `mcp` pour la porte des clients externes (ADR 0030).
    via: str | None = None

    def as_dict(self) -> dict[str, Any]:
        champs = {"kind": self.kind, "id": self.id, "run_id": self.run_id, "via": self.via}
        return {k: v for k, v in champs.items() if v}


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


def _meta(action: Any) -> dict[str, Any]:
    """Ce que l'ontologie sait d'une de ses actions : sa version, son type, ses cibles, ses paramètres."""
    return dict((action.params or {}).get("ontologie") or {})


def describe(action: Any) -> dict[str, Any]:
    """La proposition telle que l'essai la décrivait — son dossier —, lue sur l'action du cœur."""
    meta, resultat = _meta(action), dict(action.result or {})
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
        "effects": list(resultat.get("effects") or []),
        "evidence": list(resultat.get("evidence") or []),
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


async def actions_de_l_ontologie(
    session: AsyncSession, project_id: str, statut: str | None = None
) -> list[Any]:
    """Les actions du cœur nées de l'ontologie, pour ce projet, dans l'ordre de leur proposition."""
    from choregos_api.db.models import Action

    requete = select(Action).where(Action.project_id == project_id, Action.origin == ORIGINE)
    if statut:
        requete = requete.where(Action.status == statut)
    return list((await session.execute(requete.order_by(Action.created_at))).scalars().all())


async def une_action(session: AsyncSession, project_id: str, identifiant: str) -> Any:
    from choregos_api.db.models import Action

    action = await session.get(Action, identifiant)
    if action is None or action.project_id != project_id or action.origin != ORIGINE:
        raise Refusal(404, {"error": f"proposal {identifiant!r} not found"})
    return action


# ───────────────────────────── proposer ─────────────────────────────


async def _deja_proposee(
    session: AsyncSession, project_id: str, action_name: str, key: str, since: datetime
) -> Any:
    """La proposition encore vivante qui porte la même clé d'idempotence, dans la fenêtre."""
    for existing in reversed(await actions_de_l_ontologie(session, project_id)):
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
    """Crée la proposition — une action du cœur —, ou rend celle qui existe déjà pour la même clé
    d'idempotence.

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
        existing = await _deja_proposee(session, project.id, action_name, key, since)
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
        {"role": ROLE_DU_COEUR.get(str(a.get("role")), "project_owner"), "min": int(a.get("min", 1))}
        for a in rule.get("approvers") or []
    ] or [{"role": "project_owner", "min": 1}]  # une règle humaine sans approbateurs : un `owner`
    preuves = list(action.get("evidence") or [])
    effets = [{"effect": EFFET, "with": {"index": i}} for i in range(len(action["effects"]))] + [
        {"effect": PREUVE, "with": {"index": j, "position": len(action["effects"]) + j}}
        for j in range(len(preuves))
    ]
    from choregos_api.schemas.actions import ActionCreate
    from choregos_api.services.actions import proposer

    corps = ActionCreate.model_validate(
        {
            "kind": f"ontology.{action_name}"[:128],
            "title": f"{action_name} on {', '.join(ids) or 'nothing'}"[:300],
            "justification": str(arguments.get("justification") or "") or None,
            "params": {
                "ontologie": {
                    "version_id": version.id,
                    "action_type": action_name,
                    "target_ids": ids,
                    "params": params,
                    "idempotency_key": key,
                }
            },
            "effects": effets,
        }
    )
    from choregos_api.rbac import SYSTEM

    nouvelle = await proposer(
        session,
        project,
        corps,
        origine=ORIGINE,
        propose_par=actor.as_dict(),
        principal=principal or SYSTEM,
        run_id=actor.run_id,
        action_id=proposal_id,
    )
    # La règle de l'ontologie, telle qu'elle décide : ses approbateurs (en rôles du cœur), et
    # `step_up_minutes` absent quand la règle n'exige pas d'authentification récente.
    nouvelle.approval = {
        "policy": policy["name"],
        "rule": index,
        "mode": "auto" if rule.get("approve") == "auto" else "human",
        "approvers": approvers,
        "ontology_approvers": list(rule.get("approvers") or []),
        "step_up_minutes": step_up,
        "separation_of_duties": bool(policy.get("separation_of_duties", True)),
    }
    nouvelle.result = {"effects": [], "evidence": []}
    await session.flush()
    await _audit(session, project, None, "created", nouvelle, proposed_by=actor.as_dict())
    if nouvelle.approval["mode"] == "auto":
        from choregos_api.temporal import action_id as identifiant_temporal
        from choregos_api.temporal import get_temporal

        nouvelle.status = APPROVED
        nouvelle.decisions = [
            {"by": f"policy:{policy['name']}", "decision": "approve", "rule": index, "at": now.isoformat()}
        ]
        nouvelle.temporal_wf_id = await get_temporal().start_action(
            identifiant_temporal(nouvelle.id), {"action_id": nouvelle.id}
        )
    return 201, describe(nouvelle)


# ───────────────────────────── valider ─────────────────────────────


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
    """La décision humaine, prise par le CŒUR (`decider`) : session humaine, rang, séparation des
    tâches, authentification récente si la règle a un `stepUp` — et, approuvée, l'action part dans
    Temporal. Rien ne s'exécute dans cette requête."""
    from choregos_api.services.actions import decider

    decidee = await decider(session, project, org_slug, action.id, decision, reason, principal)
    await _audit(session, project, principal, "decided", decidee, decision=decision)
    return describe(decidee)


# ───────────────────────────── exécuter : l'effet `ontology.effet` ─────────────────────────────


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


async def _contexte(ctx: Any) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """L'action de l'ontologie, ses cibles relues et ses variables CEL, pour un effet du cœur."""
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


def _consigner(action: Any, liste: str, index: int, entree: dict[str, Any]) -> None:
    """Le dossier de l'action : ses effets et ses preuves, dans l'ordre, une fois chacun — un effet
    rejoué après une panne ne s'y écrit pas deux fois."""
    resultat = dict(action.result or {})
    entrees = [e for e in resultat.get(liste) or [] if e.get("index") != index]
    resultat[liste] = sorted([*entrees, {**entree, "index": index}], key=lambda e: int(e["index"]))
    action.result = resultat


async def _consigner_l_echec(ctx: Any, liste: str, index: int, entree: dict[str, Any]) -> None:
    """Un refus annule la transaction de l'activité : ce qu'il dit se consigne dans LA SIENNE, après
    avoir défait ce que l'effet avait commencé — le dossier dit pourquoi l'action a échoué."""
    from choregos_api.db.session import limiter_aux_organisations

    identifiant, modele = ctx.action.id, type(ctx.action)
    await ctx.session.rollback()
    await limiter_aux_organisations(ctx.session, "*")
    action = await ctx.session.get(modele, identifiant)
    if action is None:
        return
    _consigner(action, liste, index, entree)
    await ctx.session.commit()


async def effet_de_l_ontologie(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """`ontology.effet` : l'effet `index` de l'action de l'ontologie, rendu avec les résultats des
    effets déjà faits. Un effet en panne fait échouer l'action, sans masquer l'erreur."""
    from choregos_api.effets import EffetRefuse

    index = int(params["index"])
    meta, action, _, variables = await _contexte(ctx)
    effect = action["effects"][index]
    faits = [e for e in (ctx.action.result or {}).get("effects") or [] if int(e.get("index", -1)) < index]
    try:
        rendered = cel.render(effect["with"], {**variables, "effects": faits})
        resultat = await _apply_effect(
            ctx.session, ctx.project, str(meta["action_type"]), effect["type"], rendered
        )
    except Exception as error:  # un effet en panne fait échouer l'action, sans masquer l'erreur
        await _consigner_l_echec(ctx, "effects", index, {"type": effect["type"], "error": str(error)[:500]})
        raise EffetRefuse(f"effect {effect['type']} failed: {error}") from error
    entree = {"type": effect["type"], **resultat}
    _consigner(ctx.action, "effects", index, entree)
    return entree


# ───────────────────────────── prouver : l'effet `ontology.preuve` ─────────────────────────────


async def preuve_de_l_ontologie(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """`ontology.preuve` : la preuve `index`. `object.reread` et `connector.query` se jugent ici ;
    `collector.rerun` fait attendre l'action jusqu'au rapport suivant du collecteur, ou au délai."""
    from choregos_api.effets import EffetRefuse

    index, position = int(params["index"]), int(params["position"])
    _, action, targets, variables = await _contexte(ctx)
    item = (action.get("evidence") or [])[index]
    effets = list((ctx.action.result or {}).get("effects") or [])
    variables = {**variables, "effects": effets}
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
            jusqu_a = CLOCK.now() + within
            entry.update({"status": "pending", "with": collected, "requested_at": CLOCK.now().isoformat()})
            _consigner(ctx.action, "evidence", index, entry)
            return {**entry, "attendre_une_preuve": {"jusqu_a": jusqu_a.isoformat()}}
        result = await _collect_now(ctx.session, ctx.project, kind, collected, targets, effets)
        passed = cel.condition(item["expect"], {**variables, "result": result})
        entry.update({"status": "passed" if passed else "failed", "result": result})
    except Exception as error:  # une preuve impossible à collecter est une preuve fausse
        entry.update({"status": "failed", "error": str(error)[:500]})
    if entry["status"] == "failed":
        await _consigner_l_echec(ctx, "evidence", index, entry)
        raise EffetRefuse(f"evidence {item['name']} failed: {entry.get('error') or entry.get('result')}")
    _consigner(ctx.action, "evidence", index, entry)
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


# ───────────────────────────── le rapport suivant du collecteur ─────────────────────────────


async def on_report(
    session: AsyncSession, project: Any, observations: list[Observation] | None, partial_reason: str | None
) -> list[str]:
    """Un rapport du collecteur arrive : il tranche les preuves `collector.rerun` en attente, et les
    REMET à leur action (`signaler_une_preuve`), qui reprend ou échoue — dans Temporal.

    Le premier rapport reçu après la demande décide. Un rapport partiel, une couche absente ou une
    ligne `unreachable` pour la couche rendent la preuve impossible : `failed`, jamais « clé absente ».
    Rend les identifiants des actions tranchées.
    """
    from choregos_api.services.actions import signaler_une_preuve

    decided: list[str] = []
    for action in await actions_de_l_ontologie(session, project.id, AWAITING_EVIDENCE):
        resultat = dict(action.result or {})
        evidence = [dict(e) for e in resultat.get("evidence") or []]
        tranchees: list[dict[str, Any]] = []
        for entry in evidence:
            if entry.get("status") != "pending":
                continue
            layer, check = str(entry["with"].get("layer")), str(entry["with"].get("check"))
            scope = entry["with"].get("scope")
            if partial_reason is not None or observations is None:
                entry.update({"status": "failed", "error": f"partial report: {partial_reason}"})
                tranchees.append(entry)
                continue
            lines = [o for o in observations if o.layer == layer]
            if not lines:
                entry.update({"status": "failed", "error": f"layer {layer!r} is absent from the report"})
                tranchees.append(entry)
                continue
            if any(o.status == "unreachable" for o in lines):
                entry.update({"status": "failed", "error": f"layer {layer!r} has unreachable lines"})
                tranchees.append(entry)
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
                tranchees.append(entry)
                continue
            entry.update({"status": "passed" if passed else "failed", "result": result, "fact": fact})
            tranchees.append(entry)
        if not tranchees:
            continue
        action.result = {**resultat, "evidence": evidence}
        await session.flush()
        for entry in tranchees:
            await signaler_une_preuve(
                action.id,
                int(entry["position"]),
                ok=entry["status"] == "passed",
                detail=str(entry.get("error") or f"{entry['name']}: {entry['status']}"),
            )
        decided.append(action.id)
    return decided
