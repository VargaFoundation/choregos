# SPDX-License-Identifier: Apache-2.0
"""Ce qui vaut pour tout connecteur, de projet ou d'organisation (ADR 0034) : ses secrets ne
s'écrivent qu'en références, et une opération ne fait que se resserrer en descendant."""

from __future__ import annotations

from typing import Any

from choregos_adapters import POLITIQUES, ConnectorTypeSpec
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ConnectorOperation, OrgConnector, Project, ProjectOperationPolicy
from ..errors import unprocessable

#: Le rang de chaque politique : un projet ne peut que monter (ADR 0034).
RANG = {politique: rang for rang, politique in enumerate(POLITIQUES)}


def plus_stricte(a: str, b: str | None) -> str:
    return a if b is None or RANG[a] >= RANG[b] else b


def spec_du_type(kind: str, type_name: str) -> ConnectorTypeSpec:
    from choregos_adapters import available, spec_of

    spec = spec_of(kind, type_name)
    if spec is None:
        raise unprocessable(
            f"aucun type `{type_name}` pour `{kind}` (connus : {', '.join(available(kind)) or 'aucun'})"
        )
    return spec


def verifier_les_secrets(
    spec: ConnectorTypeSpec,
    type_name: str,
    config: dict[str, Any],
    secret_refs: dict[str, str],
    secret_ref: str | None = None,
) -> None:
    """Un secret écrit en clair, un champ secret inconnu, une référence illisible : 422.

    Les champs secrets d'un type (`api_token` de Jira, `token` d'Argo CD…) partaient dans `config`,
    en clair dans la base et dans chaque réponse de l'API. Ils s'écrivent désormais en
    références (`secret_refs`), résolues quand l'adaptateur est construit."""
    from choregos_core.secrets import ReferenceInvalide, verifier

    en_clair = sorted(set(config) & set(spec.secret_fields))
    if en_clair:
        raise unprocessable(
            f"secret écrit en clair : {', '.join(en_clair)}. Un secret s'écrit en référence, dans "
            f"`secret_refs` (`{en_clair[0]}: env:NOM_DE_VARIABLE`) — jamais sa valeur"
        )
    inconnus = sorted(set(secret_refs) - set(spec.secret_fields))
    if inconnus:
        raise unprocessable(
            f"`{type_name}` n'a pas de champ secret {', '.join(inconnus)} "
            f"(les siens : {', '.join(spec.secret_fields) or 'aucun'})"
        )
    for reference in [*secret_refs.values(), *([secret_ref] if secret_ref else [])]:
        try:
            verifier(reference)
        except ReferenceInvalide as refus:
            raise unprocessable(str(refus)) from refus


async def politique_pour_le_projet(
    session: AsyncSession, projet: Project, connecteur: str, operation: str
) -> str:
    """La politique d'une opération pour CE projet (ADR 0034) : celle de l'organisation, resserrée
    par le projet. Une opération inconnue, ou dont les groupes ne croisent pas ceux du projet, est
    interdite — ce que le courtier cache à un run, une action ne le joue pas non plus."""
    trouvee = (
        await session.execute(
            select(ConnectorOperation)
            .join(OrgConnector, OrgConnector.id == ConnectorOperation.connector_id)
            .where(
                OrgConnector.org_id == projet.org_id,
                OrgConnector.name == connecteur,
                ConnectorOperation.name == operation,
            )
        )
    ).scalar_one_or_none()
    if trouvee is None:
        return "forbidden"
    groupes = {str(g) for g in ((projet.config or {}).get("groups") or [])}
    if trouvee.groups and not groupes & set(trouvee.groups):
        return "forbidden"
    resserree = (
        await session.execute(
            select(ProjectOperationPolicy.policy).where(
                ProjectOperationPolicy.project_id == projet.id,
                ProjectOperationPolicy.operation_id == trouvee.id,
            )
        )
    ).scalar_one_or_none()
    return plus_stricte(trouvee.policy, resserree)
