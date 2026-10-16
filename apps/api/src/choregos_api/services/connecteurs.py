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
from .catalogue_rights import connector_enabled_for

#: Le rang de chaque politique : un projet ne peut que monter (ADR 0034).
RANG = {politique: rang for rang, politique in enumerate(POLITIQUES)}


def plus_stricte(a: str, b: str | None) -> str:
    return a if b is None or RANG[a] >= RANG[b] else b


def human_floor(connector: OrgConnector, operation: str, policy: str) -> str:
    """An operation its type declares `human_approval` (ADR 0052) is never below `approval`: the
    organisation cannot open it to the policy, and a row edited behind the API does not either."""
    from choregos_adapters import operation_spec

    declared = operation_spec(connector.kind, connector.type, operation)
    if declared is not None and declared.human_approval:
        return plus_stricte(policy, "approval")
    return policy


def spec_du_type(kind: str, type_name: str) -> ConnectorTypeSpec:
    from choregos_adapters import available, spec_of

    spec = spec_of(kind, type_name)
    if spec is None:
        raise unprocessable(
            f"no type `{type_name}` for `{kind}` (known: {', '.join(available(kind)) or 'none'})"
        )
    return spec


def verifier_les_secrets(
    spec: ConnectorTypeSpec,
    type_name: str,
    config: dict[str, Any],
    secret_refs: dict[str, str],
    secret_ref: str | None = None,
    *,
    may_reference: bool = True,
    secret_values: dict[str, str] | None = None,
    store_prefix: str | None = None,
) -> None:
    """Un secret écrit en clair, un champ secret inconnu, une référence illisible : 422.

    Les champs secrets d'un type (`api_token` de Jira, `token` d'Argo CD…) partaient dans `config`,
    en clair dans la base et dans chaque réponse de l'API. Ils s'écrivent désormais en
    references (`secret_refs`), resolved when the adapter is built, or as VALUES (`secret_values`),
    sealed in the database and replaced by a derived `store:` reference (ADR 0058).

    A reference to the environment names a variable of the platform (S39-06b): only the
    organisation's administrator writes one (`may_reference`). A `store:` reference is never
    user-written: it is accepted only when it is exactly the one this connector's field derives from
    `store_prefix` (the idempotent re-send of a DTO). Otherwise a project owner could point a
    connector at `store:connector/entra-acme/client_secret` and read the organisation's secret
    through a host they control."""
    from choregos_core.secrets import ReferenceInvalide, est_une_reference_du_magasin, verifier

    en_clair = sorted(set(config) & set(spec.secret_fields))
    if en_clair:
        raise unprocessable(
            f"secret written in clear: {', '.join(en_clair)}. A secret is written as a reference, in "
            f"`secret_refs` (`{en_clair[0]}: env:VARIABLE_NAME`), or as a `secret_values` entry that "
            "the platform seals — never inside `config`"
        )
    valeurs = secret_values or {}
    inconnus = sorted((set(secret_refs) | set(valeurs)) - set(spec.secret_fields))
    if inconnus:
        raise unprocessable(
            f"`{type_name}` has no secret field {', '.join(inconnus)} "
            f"(its own: {', '.join(spec.secret_fields) or 'none'})"
        )
    doubles = sorted(set(secret_refs) & set(valeurs))
    if doubles:
        raise unprocessable(
            f"{', '.join(doubles)}: a secret field is given as a value or as a reference, not both"
        )
    vides = sorted(f for f, v in valeurs.items() if not v)
    if vides:
        raise unprocessable(f"{', '.join(vides)}: an empty secret value (to remove a secret, clear it)")
    written = [(f, r) for f, r in [*secret_refs.items(), *([("", secret_ref)] if secret_ref else [])]]
    for champ, reference in written:
        if est_une_reference_du_magasin(reference):
            if champ and store_prefix is not None and reference == f"store:{store_prefix}/{champ}":
                continue
            raise unprocessable(
                "a `store:` reference is derived by the platform, never written: type the value "
                "(`secret_values`) to store a secret"
            )
        if not may_reference:
            raise unprocessable(
                "only the organisation's administrator writes a secret reference (`env:NAME`): a reference "
                "reads a variable of the platform's environment; type the value instead"
            )
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
    ligne = (
        await session.execute(
            select(ConnectorOperation, OrgConnector)
            .join(OrgConnector, OrgConnector.id == ConnectorOperation.connector_id)
            .where(
                OrgConnector.org_id == projet.org_id,
                OrgConnector.name == connecteur,
                ConnectorOperation.name == operation,
            )
        )
    ).one_or_none()
    if ligne is None:
        return "forbidden"
    trouvee, instance = ligne
    if not await connector_enabled_for(session, instance, projet):
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
    return human_floor(instance, operation, plus_stricte(trouvee.policy, resserree))
