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
    from choregos_core.injection import analyser
    from temporalio.exceptions import ApplicationError

    mode = getattr(bundle.policy.sandbox, "prompt_injection", "warn")
    if mode == "ignore":
        return
    documents = item.documents or {}
    sources: dict[str, str] = {"ticket.title": item.title or "", "ticket.body": item.body_snapshot or ""}
    sources.update({f"document.{nom}": texte for nom, texte in documents.items() if isinstance(texte, str)})
    sources.update({f"memory.{m.id or i}": m.content for i, m in enumerate(context_pack.memories)})
    alertes = analyser(sources)
    if not alertes:
        return
    # Dans SA transaction : en `block`, l'activité lève juste après, et la session de
    # l'étape est annulée avec elle — l'événement doit survivre, c'est lui que le ticket montre.
    async with db() as journal:
        await persist_event(
            journal,
            EventType.SECURITY_INJECTION_SUSPECTED,
            project_id=bundle.project.id,
            work_item_id=item.id,
            project_slug=bundle.slug,
            subject=item.tracker_key,
            run_id=run_id,
            stage=plan.role,
            mode=mode,
            suspicions=[a.to_dict() for a in alertes[:10]],
        )
    if mode == "block":
        resume = " ; ".join(f"{a.source} : {a.motif}" for a in alertes[:3])
        raise ApplicationError(
            f"injection de prompt suspectée, étape arrêtée avant tout run ({resume}) — "
            "politique sandbox.prompt_injection: block ; relire le ticket, puis relancer",
            type="injection_suspected",
            non_retryable=True,
        )
