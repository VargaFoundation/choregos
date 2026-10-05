# SPDX-License-Identifier: Apache-2.0
"""Fabrique d'adaptateurs : du `connector.type` à l'implémentation.

`CHOREGOS_FAKES=1` bascule toute la plateforme sur les fakes en mémoire — c'est le mode
utilisé par les tests, le mode démo du front et `make demo`.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from choregos_contracts import ConnectorKind

from .base import (
    CdAdapter,
    CiAdapter,
    Executor,
    GatewayAdapter,
    MemoryAdapter,
    Notifier,
    ScmAdapter,
    TrackerAdapter,
)

Factory = Callable[[dict[str, Any]], Any]


#: Du plus permissif au plus strict : un projet ne peut que monter dans cet ordre (ADR 0034).
POLITIQUES = ("allowed", "approval", "forbidden")


@dataclass(frozen=True, slots=True)
class OperationSpec:
    """Une opération qu'un type de connecteur expose — ce qu'un agent peut appeler par lui.

    `access` dit si elle lit ou écrit. Sa politique par défaut en découle : une lecture est
    permise, une écriture passe par une validation (ADR 0035), tant que l'administrateur de
    l'organisation n'en décide pas autrement."""

    name: str
    access: str = "read"
    description: str = ""
    #: ce que l'opération attend : le courtier l'annonce à l'agent et le vérifie avant l'appel
    input_schema: dict[str, Any] | None = None

    @property
    def default_policy(self) -> str:
        return "allowed" if self.access == "read" else "approval"


def schema_d_entree(*requis: str, **autres: dict[str, Any]) -> dict[str, Any]:
    """Le schéma d'entrée d'une opération : des chaînes requises, d'autres propriétés facultatives,
    et RIEN d'autre (`additionalProperties: false`) — le courtier et l'effet `connector.call` le
    vérifient avant l'appel, un argument inattendu n'atteint jamais le système tiers."""
    proprietes: dict[str, Any] = {nom: {"type": "string"} for nom in requis}
    proprietes.update(autres)
    return {
        "type": "object",
        "properties": proprietes,
        "required": list(requis),
        "additionalProperties": False,
    }


@dataclass(frozen=True, slots=True)
class ConnectorTypeSpec:
    """Ce qu'un type de connecteur déclare de lui-même (ADR 0034) : la console en tire son
    formulaire, la plateforme ses refus. `secret_fields` : les clefs de configuration qui sont des
    secrets — elles ne s'écrivent jamais en clair, seulement en référence (`env:NOM`…), résolue
    quand l'adaptateur est construit."""

    display: str
    capabilities: tuple[str, ...] = ()
    config_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    secret_fields: tuple[str, ...] = ()
    #: Un ancien nom, gardé pour les projets qui le portent, jamais proposé à un nouveau.
    deprecated: bool = False
    #: Ce qu'un agent peut appeler par ce type ; un serveur `mcp` découvre les siennes (S19-03).
    operations: tuple[OperationSpec, ...] = ()


_REGISTRY: dict[tuple[str, str], Factory] = {}
_SPECS: dict[tuple[str, str], ConnectorTypeSpec] = {}

#: Le type qu'un projet obtient pour une sorte qu'il ne configure pas.
TYPES_PAR_DEFAUT: dict[str, str] = {
    "tracker": "github-issues",
    "scm": "github",
    "ci": "tekton",
    "cd": "argocd",
    "memory": "ecphoria",
    "gateway": "litellm",
    "notify": "slack",
}


def register(
    kind: ConnectorKind | str, type_name: str, spec: ConnectorTypeSpec | None = None
) -> Callable[[Factory], Factory]:
    """Enregistre une implémentation pour un couple (kind, type), et ce qu'elle déclare."""

    def decorator(factory: Factory) -> Factory:
        cle = (str(kind), type_name)
        _REGISTRY[cle] = factory
        # Remplacer l'implémentation ne retire pas la déclaration : l'API réenregistre la mémoire
        # lexicale avec SA fabrique (adossée à ses tables), et l'alias `pgvector` redevenait
        # proposable à la création.
        _SPECS[cle] = (
            spec or _SPECS.get(cle) or ConnectorTypeSpec(display=type_name, capabilities=(str(kind),))
        )
        return factory

    return decorator


def spec_of(kind: ConnectorKind | str, type_name: str) -> ConnectorTypeSpec | None:
    return _SPECS.get((str(kind), type_name))


def connector_types() -> list[tuple[str, str, ConnectorTypeSpec]]:
    """Les types enregistrés — ceux du cœur et ceux des greffons —, sans les faux des tests ni
    les anciens noms : un nouveau projet ne choisit pas un nom qui ment (`pgvector`)."""
    return sorted((k, t, spec) for (k, t), spec in _SPECS.items() if t != "fake" and not spec.deprecated)


def references_de_secret(
    kind: str, type_name: str, secret_refs: dict[str, str] | None, secret_ref: str | None = None
) -> dict[str, str]:
    """Les références d'un connecteur, champ par champ. `secret_ref` (une seule, d'avant l'ADR 0034)
    vaut pour le PREMIER champ secret de son type."""
    references = dict(secret_refs or {})
    spec = spec_of(kind, type_name)
    if secret_ref and spec and spec.secret_fields:
        references.setdefault(spec.secret_fields[0], secret_ref)
    return references


def configuration_resolue(
    kind: str,
    type_name: str,
    config: dict[str, Any] | None,
    secret_refs: dict[str, str] | None,
    secret_ref: str | None = None,
) -> dict[str, Any]:
    """La configuration, ses secrets RÉSOLUS : de quoi construire l'adaptateur — jamais de quoi
    enregistrer ni rendre. Une référence irrésoluble lève `SecretIntrouvable` : mieux vaut un
    connecteur en erreur qu'un adaptateur qui retombe en silence sur sa valeur par défaut."""
    from choregos_core.secrets import resoudre

    resolue = dict(config or {})
    for champ, reference in references_de_secret(kind, type_name, secret_refs, secret_ref).items():
        resolue[champ] = resoudre(reference)
    return resolue


def type_par_defaut(kind: str) -> str | None:
    if kind == "runtime":
        return default_executor_kind()
    return TYPES_PAR_DEFAUT.get(kind)


def fakes_enabled() -> bool:
    return os.environ.get("CHOREGOS_FAKES", "") in {"1", "true", "yes"}


def default_executor_kind() -> str:
    """L'exécuteur d'un projet qui n'en déclare pas : celui du déploiement.

    `CHOREGOS_EXECUTOR_KIND` était posé par le chart et lu nulle part — chaque projet
    retombait sur Tekton, y compris là où Tekton n'existe pas (un locataire qui ne peut
    lancer que des Jobs dans son namespace)."""
    return os.environ.get("CHOREGOS_EXECUTOR_KIND", "") or "tekton"


def available(kind: ConnectorKind | str) -> list[str]:
    return sorted(type_name for (k, type_name) in _REGISTRY if k == str(kind))


def build(kind: ConnectorKind | str, type_name: str, config: dict[str, Any] | None = None) -> Any:
    """Construit un adaptateur. En mode fakes, le type demandé est ignoré."""
    config = config or {}
    if fakes_enabled():
        type_name = "fake"
    factory = _REGISTRY.get((str(kind), type_name))
    if factory is None:
        known = ", ".join(available(kind)) or "aucun"
        raise KeyError(f"aucun adaptateur `{type_name}` pour `{kind}` (disponibles : {known})")
    return factory(config)


@dataclass(slots=True)
class AdapterSet:
    """L'ensemble des adaptateurs d'un projet, résolu une fois puis passé aux activités."""

    tracker: TrackerAdapter
    scm: ScmAdapter
    ci: CiAdapter
    cd: CdAdapter
    executor: Executor
    memory: MemoryAdapter
    gateway: GatewayAdapter
    notify: Notifier
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def fakes(cls) -> AdapterSet:
        """Jeu complet de fakes, partageant les mêmes instances (utile en test)."""
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

        return cls(
            tracker=FakeTracker(),
            scm=FakeScm(),
            ci=FakeCi(),
            cd=FakeCd(),
            executor=FakeExecutor(),
            memory=FakeMemory(),
            gateway=FakeGateway(),
            notify=FakeNotifier(),
        )

    @classmethod
    def from_connectors(cls, connectors: dict[str, dict[str, Any]]) -> AdapterSet:
        """Construit depuis la configuration des connecteurs d'un projet."""
        if fakes_enabled():
            return cls.fakes()

        def get(kind: str, default_type: str) -> Any:
            spec = connectors.get(kind, {})
            type_name = spec.get("type", default_type)
            # Les secrets résolus ICI, dans le processus qui construit l'adaptateur (ADR 0034).
            config = configuration_resolue(
                kind, type_name, spec.get("config", {}), spec.get("secret_refs"), spec.get("secret_ref")
            )
            return build(kind, type_name, config)

        return cls(
            tracker=get("tracker", TYPES_PAR_DEFAUT["tracker"]),
            scm=get("scm", TYPES_PAR_DEFAUT["scm"]),
            ci=get("ci", TYPES_PAR_DEFAUT["ci"]),
            cd=get("cd", TYPES_PAR_DEFAUT["cd"]),
            executor=get("runtime", default_executor_kind()),
            memory=get("memory", TYPES_PAR_DEFAUT["memory"]),
            gateway=get("gateway", TYPES_PAR_DEFAUT["gateway"]),
            notify=get("notify", TYPES_PAR_DEFAUT["notify"]),
        )
