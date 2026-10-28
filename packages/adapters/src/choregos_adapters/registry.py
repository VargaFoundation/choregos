# SPDX-License-Identifier: Apache-2.0
"""Fabrique d'adaptateurs : du `connector.type` à l'implémentation.

`CHOREGOS_FAKES=1` bascule toute la plateforme sur les fakes en mémoire — c'est le mode
utilisé par les tests, le mode démo du front et `make demo`.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
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
from .errors import ConfigurationError

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
    #: A write that a PERSON approves, always (ADR 0052): its policy never goes below `approval`,
    #: neither by the organisation nor by any other path — a firewall rule is not approved by policy.
    human_approval: bool = False
    #: What undoes it, declared by the type: `{"operation": …, "arguments": {…}}`, rendered with the
    #: operation's own `result` (ADR 0035). The broker attaches it to the action it proposes.
    compensation: dict[str, Any] | None = None
    #: Arguments the PLATFORM fills, never the agent: `action_key` (stable for one action and one
    #: call — an idempotency key) and `work_item_key` (the work item the action belongs to).
    platform_arguments: tuple[str, ...] = ()

    @property
    def default_policy(self) -> str:
        return "allowed" if self.access == "read" else "approval"


#: The arguments the platform knows how to fill (`OperationSpec.platform_arguments`).
PLATFORM_ARGUMENTS = frozenset({"action_key", "work_item_key"})


def operation_spec(kind: str, type_name: str, operation: str) -> OperationSpec | None:
    """What a type declares of one of its operations — `None` for a discovered MCP tool."""
    spec = _SPECS.get((kind, type_name))
    return next((o for o in spec.operations if o.name == operation), None) if spec else None


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
    #: The `(host, port)` pairs an instance configured so calls (S39-22, ADR 0066). `None`: the
    #: hosts of the URL-shaped configuration fields, falling back to the schema's defaults.
    egress: Callable[[dict[str, Any]], list[tuple[str, int]]] | None = None
    #: A check the configuration must pass to be saved (S40-01k): raises an `AdapterError` naming the
    #: refusal. The adapter repeats it when built, so a row written behind the API fails closed too.
    check_config: Callable[[dict[str, Any]], None] | None = None
    #: The secret fields that may hold several keys, one per slot (`api_key`, `api_key.2` …
    #: `api_key.10`, ADR 0089): the adapter receives them as a list, used in turn.
    pooled_fields: tuple[str, ...] = ()


#: A slot of a pooled field: `api_key.2` … `api_key.10` (ADR 0089). The first slot is the field.
SLOTS = range(2, 11)
_SLOT = re.compile(r"^(?P<field>[a-z][a-z0-9_]{0,63})\.(?P<n>[2-9]|10)$")


def slot_of(name: str) -> tuple[str, int]:
    """`api_key.3` -> (`api_key`, 3); `api_key` -> (`api_key`, 1)."""
    m = _SLOT.match(name)
    return (m.group("field"), int(m.group("n"))) if m else (name, 1)


_REGISTRY: dict[tuple[str, str], Factory] = {}
_SPECS: dict[tuple[str, str], ConnectorTypeSpec] = {}

#: Le type qu'un projet obtient pour une sorte qu'il ne configure pas.
TYPES_PAR_DEFAUT: dict[str, str] = {
    # A project without a tracker connector files its requests in Choregos, as
    # `le_tracker_est_interne` says (#704): `github-issues` with no `repo` could only crash.
    "tracker": "internal",
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
    preresolved: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """The configuration with its secrets RESOLVED: enough to build the adapter, never to save or
    return. An unresolvable reference raises `SecretIntrouvable`: a connector in error beats an
    adapter that silently falls back to its default value.

    `preresolved` (field -> value): the fields the platform already read with the organisation's
    context — the sealed `store:` values (ADR 0058). They are not resolved again."""
    from choregos_core.secrets import resoudre

    resolue = dict(config or {})
    deja = dict(preresolved or {})
    for champ, reference in references_de_secret(kind, type_name, secret_refs, secret_ref).items():
        resolue[champ] = deja[champ] if champ in deja else resoudre(reference)
    return pooled(kind, type_name, resolue)


def pooled(kind: str, type_name: str, resolved: dict[str, Any]) -> dict[str, Any]:
    """The slots of each pooled field gathered under the field, in slot order (ADR 0089): a list
    when there are several keys, the one string otherwise — a type that pools nothing sees no change.
    A slot of a field its type does not pool is dropped: no adapter reads `api_key.2`."""
    spec = spec_of(kind, type_name)
    fields = set(spec.pooled_fields) if spec else set()
    slots: dict[str, list[tuple[int, Any]]] = {}
    out: dict[str, Any] = {}
    for name, value in resolved.items():
        field, n = slot_of(name)
        if field in fields and (n > 1 or name == field):
            slots.setdefault(field, []).append((n, value))
        elif n == 1:
            out[name] = value
    for field, found in slots.items():
        values = [v for _, v in sorted(found, key=lambda s: s[0]) if v]
        out[field] = values if len(values) > 1 else (values[0] if values else "")
    return out


def default_gateway_type() -> str:
    """The gateway of a project that declares none: the deployment's (`CHOREGOS_GATEWAY_TYPE`)."""
    return os.environ.get("CHOREGOS_GATEWAY_TYPE", "") or TYPES_PAR_DEFAUT["gateway"]


def type_par_defaut(kind: str) -> str | None:
    if kind == "runtime":
        return default_executor_kind()
    if kind == "gateway":
        return default_gateway_type()
    return TYPES_PAR_DEFAUT.get(kind)


def deployment_default_type(kind: str) -> str | None:
    """The type a DEPLOYMENT really provides for a capability, or None (S40-05).

    Only `runtime` (the deployment's executor), `gateway` (`CHOREGOS_GATEWAY_TYPE`) and `memory`
    (`ecphoria` when `CHOREGOS_MEMORY_URL` is set, `lexical` otherwise) are provided by a
    deployment. For every other kind, `type_par_defaut` names what the adapter factory would
    build with an empty configuration, which is not something that works: `argocd` without a
    GitOps repository and a token provides no CD. The console claims a platform default only
    from this function."""
    if kind == "runtime":
        return default_executor_kind()
    if kind == "gateway":
        return default_gateway_type()
    if kind == "memory":
        return "ecphoria" if os.environ.get("CHOREGOS_MEMORY_URL", "") else "lexical"
    return None


def fakes_enabled() -> bool:
    return os.environ.get("CHOREGOS_FAKES", "") in {"1", "true", "yes"}


def default_executor_kind() -> str:
    """The executor of a project that declares none: the deployment's own (ADR 0072).

    `agent_sandbox` when the deployment says nothing: Tekton is no longer assumed, since it
    is absent from the clusters this platform runs on. A deployment that wants Tekton says so
    (`global.runner.executor: tekton`)."""
    return os.environ.get("CHOREGOS_EXECUTOR_KIND", "") or "agent_sandbox"


def executors_declared() -> bool:
    """True when the deployment says which executors it runs (the chart always does)."""
    return bool(
        os.environ.get("CHOREGOS_EXECUTOR_KIND", "") or os.environ.get("CHOREGOS_EXECUTORS_ALLOWED", "")
    )


def allowed_executor_kinds() -> list[str]:
    """The executors a project may pick as its `runtime`: the deployment's, plus the allowed ones.

    `CHOREGOS_EXECUTORS_ALLOWED` is a comma-separated list (chart `global.runner.allowedExecutors`);
    the default executor is always in it. `fake` is added under `CHOREGOS_FAKES`."""
    kinds = [default_executor_kind()]
    for raw in os.environ.get("CHOREGOS_EXECUTORS_ALLOWED", "").split(","):
        name = raw.strip()
        if name and name not in kinds:
            kinds.append(name)
    if fakes_enabled() and "fake" not in kinds:
        kinds.append("fake")
    return kinds


def with_custom_image_executor(executor: Any) -> Any:
    """On `agent_sandbox`, send a step that names its own image to a second executor (ADR 0072).

    `CHOREGOS_CUSTOM_IMAGE_EXECUTOR` (chart `runner.customImageExecutor`) names it; empty keeps the
    sandbox executor's refusal. Nothing changes for any other executor, or when the pool's image
    (`CHOREGOS_RUNNER_IMAGE`) is unknown, because an override cannot then be told from the default."""
    secondary = os.environ.get("CHOREGOS_CUSTOM_IMAGE_EXECUTOR", "")
    pool_image = os.environ.get("CHOREGOS_RUNNER_IMAGE", "")
    if not secondary or not pool_image or str(getattr(executor, "kind", "")) != "agent_sandbox":
        return executor
    from .executor.routing import RoutingExecutor

    return RoutingExecutor(executor, build("runtime", secondary, {}), pool_image)


def available(kind: ConnectorKind | str) -> list[str]:
    return sorted(type_name for (k, type_name) in _REGISTRY if k == str(kind))


#: Types that stay themselves under `CHOREGOS_FAKES`: their fake is what they call, not the adapter. A
#: plugin tracker (S43-07) under fakes talks to the in-memory server of the tracker profile
#: (`FakePluginTrackerServer`, the transport the API injects), so the profile mapping is what runs.
BUILT_UNDER_FAKES: frozenset[tuple[str, str]] = frozenset({("tracker", "plugin")})


def build(kind: ConnectorKind | str, type_name: str, config: dict[str, Any] | None = None) -> Any:
    """Construit un adaptateur. En mode fakes, le type demandé est ignoré."""
    config = config or {}
    if fakes_enabled() and (str(kind), type_name) not in BUILT_UNDER_FAKES:
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
                kind,
                type_name,
                spec.get("config", {}),
                spec.get("secret_refs"),
                spec.get("secret_ref"),
                preresolved=spec.get("resolved"),
            )
            return build(kind, type_name, config)

        return cls(
            tracker=get("tracker", TYPES_PAR_DEFAUT["tracker"]),
            scm=get("scm", TYPES_PAR_DEFAUT["scm"]),
            ci=get("ci", TYPES_PAR_DEFAUT["ci"]),
            cd=get("cd", TYPES_PAR_DEFAUT["cd"]),
            executor=with_custom_image_executor(get("runtime", default_executor_kind())),
            memory=get("memory", TYPES_PAR_DEFAUT["memory"]),
            gateway=get("gateway", default_gateway_type()),
            notify=get("notify", TYPES_PAR_DEFAUT["notify"]),
        )


def validate_config(kind: str, type_name: str, config: dict[str, Any]) -> None:
    """Run the type's own configuration check, if it declares one; raises an `AdapterError`."""
    spec = _SPECS.get((kind, type_name))
    rotation = config.get("key_rotation")
    if rotation is not None and (spec is None or not spec.pooled_fields):
        raise ConfigurationError(f"`{type_name}` holds one key per field: `key_rotation` does not apply")
    if rotation is not None and rotation not in ("failover", "round_robin"):
        raise ConfigurationError("`key_rotation` is `failover` or `round_robin`")
    if spec is not None and spec.check_config is not None:
        spec.check_config(config)


def egress_hosts(kind: str, type_name: str, config: dict[str, Any]) -> list[tuple[str, int]]:
    """The hosts a connector of this type, configured so, calls: derived on the server, never typed."""
    from choregos_core.egress import hosts_of_config

    spec = _SPECS.get((kind, type_name))
    if spec is not None and spec.egress is not None:
        return spec.egress(config)
    properties = (spec.config_schema.get("properties") if spec else None) or {}
    defaults = {name: p.get("default") for name, p in properties.items() if isinstance(p, dict)}
    return hosts_of_config(config, defaults)
