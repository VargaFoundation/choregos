# SPDX-License-Identifier: Apache-2.0
"""Les reçus de passage entre étapes (S25-04).

Une étape produit des sorties (une spec, un plan, les profils retenus) que l'étape suivante lit :
dans son prompt, que l'orchestrateur rend à la création du run, puis par `get_ticket` pendant le run.
Rien ne disait QUELLE révision l'étape suivante avait lue : une sortie réécrite entre-temps — par une
reprise, ou à la main — passait sans trace. Chaque sortie rangée laisse donc un reçu « produite »,
sous son empreinte, et chaque lecture un reçu « lue », sous l'empreinte de ce qui a été lu. Un reçu ne
change jamais : une nouvelle révision fait un nouveau reçu.

Écrire un reçu est idempotent (ADR 0008) : la contrainte (ticket, sortie, empreinte, run, genre)
départage, et une activité rejouée ou deux lectures simultanées ne font qu'un reçu.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import HandOffReceipt, Run, WorkItem
from ..schemas import HandOff, HandOffEvent

PRODUITE = "produced"
LUE = "read"

#: Les documents logiciels qu'une étape lit dans son prompt quand son gabarit les cite.
DOCUMENTS_DU_PROMPT = ("spec_markdown", "plan_markdown")


def empreinte(valeur: Any) -> str:
    """L'empreinte d'une sortie : le SHA-256 de son JSON canonique (clés triées, sans espaces)."""
    canonique = json.dumps(valeur, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonique.encode("utf-8")).hexdigest()


def _presente(valeur: Any) -> bool:
    return valeur not in (None, "", [], {})


async def consigner(
    session: AsyncSession,
    *,
    org_id: str,
    work_item_id: str,
    run_id: str,
    stage: str | None,
    sorties: dict[str, Any],
    genre: str,
) -> int:
    """Un reçu par sortie présente ; rend le nombre de reçus écrits (zéro quand tous existaient)."""
    ecrits = 0
    for nom, valeur in sorties.items():
        if not _presente(valeur):
            continue
        digest = empreinte(valeur)
        deja = (
            await session.execute(
                select(HandOffReceipt.id).where(
                    HandOffReceipt.work_item_id == work_item_id,
                    HandOffReceipt.output == nom,
                    HandOffReceipt.digest == digest,
                    HandOffReceipt.run_id == run_id,
                    HandOffReceipt.kind == genre,
                )
            )
        ).first()
        if deja is not None:
            continue
        try:
            # Deux lectures simultanées du même run passent toutes deux la vérification ci-dessus :
            # la contrainte départage, et la seconde ne fait rien.
            async with session.begin_nested():
                session.add(
                    HandOffReceipt(
                        org_id=org_id,
                        work_item_id=work_item_id,
                        output=nom,
                        digest=digest,
                        kind=genre,
                        run_id=run_id,
                        stage=stage,
                    )
                )
                await session.flush()
        except IntegrityError:
            continue
        ecrits += 1
    return ecrits


def lu_par_le_prompt(documents: dict[str, Any], entrees: list[str], prompt: str) -> dict[str, Any]:
    """Ce que le prompt d'un run embarque des étapes d'avant : les entrées que sa transition déclare
    (`inputs: [profils]`) — et la spec ou le plan, seulement quand leur texte est DANS le prompt rendu :
    un gabarit qui ne les cite pas ne les fait pas lire."""
    lues = {nom: documents.get(nom) for nom in entrees if _presente(documents.get(nom))}
    for nom in DOCUMENTS_DU_PROMPT:
        valeur = documents.get(nom)
        if isinstance(valeur, str) and valeur.strip() and valeur in prompt:
            lues[nom] = valeur
    return lues


async def passages_du_ticket(session: AsyncSession, item: WorkItem) -> list[HandOff]:
    """Les reçus d'un ticket, sortie par sortie, avec l'empreinte de ce qu'elle vaut AUJOURD'HUI :
    une sortie modifiée après coup n'a plus celle de son dernier reçu « produite »."""
    recus = (
        (
            await session.execute(
                select(HandOffReceipt)
                .where(HandOffReceipt.work_item_id == item.id)
                .order_by(HandOffReceipt.created_at, HandOffReceipt.kind.desc())
            )
        )
        .scalars()
        .all()
    )
    tentatives = {
        run.id: run.attempt
        for run in (await session.execute(select(Run).where(Run.id.in_({r.run_id for r in recus})))).scalars()
    }
    documents = item.documents or {}
    passages: dict[str, HandOff] = {}
    for recu in recus:
        valeur = documents.get(recu.output)
        passage = passages.setdefault(
            recu.output,
            HandOff(
                output=recu.output,
                current_digest=empreinte(valeur) if _presente(valeur) else None,
                events=[],
            ),
        )
        passage.events.append(
            HandOffEvent(
                kind=recu.kind,
                digest=recu.digest,
                run_id=recu.run_id,
                stage=recu.stage,
                attempt=tentatives.get(recu.run_id),
                at=recu.created_at,
            )
        )
    return list(passages.values())
