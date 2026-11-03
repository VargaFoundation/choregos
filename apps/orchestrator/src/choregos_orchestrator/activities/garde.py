# SPDX-License-Identifier: Apache-2.0
"""La garde contre l'injection de prompt, jouée avant de donner quoi que ce soit à l'agent."""

from __future__ import annotations

from typing import Any

from choregos_api.services import persist_event
from choregos_contracts import ContextPack, EventType

from .base import db
from .plan import StagePlan


async def garde_contre_l_injection(
    session: Any, bundle: Any, item: Any, run_id: str, plan: StagePlan, context_pack: ContextPack
) -> None:
    """Regarde ce que l'agent va lire AVANT de le lui donner, et fait ce que la politique dit.

    `warn` : un événement de sécurité, visible sur le ticket. `block` : l'étape ne démarre
    pas — l'activité échoue sans reprise, le ticket est marqué mort avec la raison (P0-2),
    un humain relit. `ignore` : rien. C'est lexical (`choregos_core.injection`), et dit
    comme tel : un filet contre les cas grossiers, pas une compréhension.
    """
    if getattr(bundle.policy.sandbox, "prompt_injection", "warn") == "ignore":
        return
    documents = item.documents or {}
    sources: dict[str, str] = {
        "ticket.title": item.title or "",
        "ticket.reporter": getattr(item, "reporter", None) or "",
        "ticket.body": item.body_snapshot or "",
    }
    sources.update({f"document.{nom}": texte for nom, texte in documents.items() if isinstance(texte, str)})
    sources.update({f"memory.{m.id or i}": m.content for i, m in enumerate(context_pack.memories)})
    await guard_sources(bundle, item, run_id, plan.role, sources)


async def guard_sources(bundle: Any, item: Any, run_id: str, stage: str, sources: dict[str, str]) -> None:
    """The injection guard on named texts about to leave for a model: an agent's context, or the
    state a decision step sends (ADR 0121 §2), under the policy's `sandbox.prompt_injection`.

    `warn` records a security event on the work item; `block` raises a non-retryable
    `injection_suspected` and the step does not start; `ignore` does nothing."""
    from choregos_core.injection import analyser
    from temporalio.exceptions import ApplicationError

    mode = getattr(bundle.policy.sandbox, "prompt_injection", "warn")
    if mode == "ignore":
        return
    suspected = analyser(sources)
    if not suspected:
        return
    # In its own transaction: in `block` the activity raises right after and the step's session is
    # rolled back with it; the event must survive, it is what the work item shows.
    async with db() as journal:
        await persist_event(
            journal,
            EventType.SECURITY_INJECTION_SUSPECTED,
            project_id=bundle.project.id,
            work_item_id=item.id,
            project_slug=bundle.slug,
            subject=item.tracker_key,
            run_id=run_id,
            stage=stage,
            mode=mode,
            suspicions=[a.to_dict() for a in suspected[:10]],
        )
    if mode == "block":
        resume = "; ".join(f"{a.source}: {a.motif}" for a in suspected[:3])
        raise ApplicationError(
            f"suspected prompt injection, step stopped before any run ({resume}) — "
            "policy sandbox.prompt_injection: block; read the work item, then retry",
            type="injection_suspected",
            non_retryable=True,
        )
