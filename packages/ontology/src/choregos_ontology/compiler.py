# SPDX-License-Identifier: Apache-2.0
"""Compilation of a valid package into its IR, MCP tools included (R-SOC-ONT-02, contract 03 §9)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from choregos_ontology.errors import OntologyError, Validation
from choregos_ontology.ir import (
    CompiledOntology,
    IrActionType,
    IrDatasource,
    IrDerived,
    IrEvidence,
    IrLinkEnd,
    IrLinkType,
    IrObjectType,
    IrPolicy,
    IrProperty,
    IrRule,
    IrSemantic,
    IrStep,
    IrWindow,
    McpTool,
)
from choregos_ontology.loader import Package, load_package
from choregos_ontology.mcpgen import (
    action_tool_name,
    action_tool_schema,
    link_tool_name,
    parameters_schema,
    search_tool,
)
from choregos_ontology.model import (
    ActionTypeSpec,
    ApprovalPolicySpec,
    DatasourceSpec,
    Defaults,
    Effect,
    LinkTypeSpec,
    ObjectTypeSpec,
    OntologySpec,
    Property,
    WindowSpec,
)
from choregos_ontology.validator import Registry, validate

DEFAULT_FACTS_MAX_AGE = "24h"
DEFAULT_IDEMPOTENCY_WINDOW = "24h"


def _property(name: str, prop: Property, defaults: Defaults) -> IrProperty:
    return IrProperty(
        name=name,
        type=prop.type,
        required=prop.required,
        enum=list(prop.enum) if prop.enum is not None else None,
        format=prop.format,
        sensitivity=prop.sensitivity or defaults.sensitivity,
        searchable=prop.searchable,
        filterable=prop.filterable,
        sortable=prop.sortable,
    )


def _step(effect: Effect) -> IrStep:
    return IrStep(type=effect.type, with_=dict(effect.with_))


def _object_type(
    name: str,
    spec: ObjectTypeSpec,
    display: tuple[str | None, str | None],
    datasource: DatasourceSpec,
    defaults: Defaults,
) -> IrObjectType:
    semantic = spec.search.semantic if spec.search else None
    return IrObjectType(
        name=name,
        display_name=display[0],
        description=display[1],
        datasource=IrDatasource(name=spec.datasource, **datasource.model_dump(exclude_none=True)),
        primary_key=spec.primary_key,
        title_property=spec.title_property or spec.primary_key,
        properties=[_property(n, p, defaults) for n, p in sorted(spec.properties.items())],
        derived=[IrDerived(name=n, type=d.type, expr=d.expr) for n, d in sorted(spec.derived.items())],
        semantic=IrSemantic(fields=list(semantic.fields), model=semantic.model) if semantic else None,
        freshness_max_age=spec.freshness.max_age if spec.freshness else None,
        mcp_exposed=spec.mcp.expose if spec.mcp.expose is not None else defaults.mcp.expose_objects,
    )


def _link_type(name: str, spec: LinkTypeSpec) -> IrLinkType:
    join: dict[str, str]
    if spec.foreign_key is not None:
        join = {"kind": "foreign_key", "side": spec.foreign_key.side, "property": spec.foreign_key.property}
    elif spec.via is not None:
        join = {
            "kind": "via",
            "datasource": spec.via.datasource,
            "from_key": spec.via.from_key,
            "to_key": spec.via.to_key,
        }
    else:
        join = {"kind": "none"}
    return IrLinkType(
        name=name,
        from_=IrLinkEnd(
            object_type=spec.from_.object_type, name=spec.from_.name, cardinality=spec.from_.cardinality
        ),
        to=IrLinkEnd(object_type=spec.to.object_type, name=spec.to.name, cardinality=spec.to.cardinality),
        join=join,
    )


def _action_type(
    name: str, spec: ActionTypeSpec, display: tuple[str | None, str | None], defaults: Defaults
) -> IrActionType:
    sequencing = spec.sequencing.model_dump() if spec.sequencing else {"mode": "all", "between": []}
    return IrActionType(
        name=name,
        display_name=display[0],
        description=display[1],
        target_type=spec.target.object_type,
        cardinality=spec.target.cardinality,
        parameters=[_property(n, p, defaults) for n, p in sorted(spec.parameters.items())],
        parameters_schema=parameters_schema(spec.parameters),
        preconditions=[p.model_dump() for p in spec.preconditions],
        facts_max_age=spec.facts_max_age or DEFAULT_FACTS_MAX_AGE,
        propose=list(spec.permissions.propose),
        approve_policy=spec.permissions.approve.removeprefix("policy:"),
        risk=spec.risk,
        reversibility=spec.reversibility,
        window=spec.window,
        effects=[_step(e) for e in spec.effects],
        sequencing=sequencing,
        compensation=[_step(e) for e in spec.compensation],
        evidence=[
            IrEvidence(
                name=e.name, collect=IrStep(type=e.collect.type, with_=dict(e.collect.with_)), expect=e.expect
            )
            for e in spec.evidence
        ],
        idempotency_key=spec.idempotency_key,
        idempotency_window=spec.idempotency_window or DEFAULT_IDEMPOTENCY_WINDOW,
        rate_limit=spec.rate_limit.model_dump() if spec.rate_limit else None,
        mcp_exposed=spec.mcp.expose if spec.mcp.expose is not None else defaults.mcp.expose_actions,
    )


def _policy(name: str, spec: ApprovalPolicySpec) -> IrPolicy:
    return IrPolicy(
        name=name,
        separation_of_duties=spec.separation_of_duties,
        rules=[
            IrRule(
                when=r.when,
                approve=r.approve,
                approvers=[a.model_dump(exclude_none=True) for a in r.approvers],
                step_up=r.step_up.model_dump(by_alias=True, exclude_none=True) if r.step_up else None,
                sla=r.sla.model_dump(by_alias=True, exclude_none=True) if r.sla else None,
            )
            for r in spec.rules
        ],
    )


def _tools(
    objects: list[IrObjectType],
    links: list[IrLinkType],
    actions: list[IrActionType],
    sources: dict[str, ObjectTypeSpec],
    action_specs: dict[str, ActionTypeSpec],
    registry: Registry,
) -> list[McpTool]:
    exposed = {o.name for o in objects if o.mcp_exposed}
    tools = [
        McpTool(
            name="ontology_describe",
            kind="describe",
            source=None,
            description="Object types, links and actions visible to the caller, with their descriptions.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        McpTool(
            name="action_status",
            kind="status",
            source=None,
            description="State, decisions and evidence of a proposal.",
            input_schema={
                "type": "object",
                "properties": {"proposal": {"type": "string"}},
                "required": ["proposal"],
                "additionalProperties": False,
            },
        ),
        McpTool(
            name="action_list",
            kind="list",
            source=None,
            description="Proposals of the project, filtered by status or action type.",
            input_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "action_type": {"type": "string"},
                    "cursor": {"type": "string"},
                },
                "additionalProperties": False,
            },
        ),
    ]
    for obj in objects:
        if not obj.mcp_exposed:
            continue
        semantic = obj.semantic is not None and registry.embeddings
        label = obj.description or obj.display_name or obj.name
        tools.append(
            McpTool(
                name=f"{obj.name}_search",
                kind="search",
                source=obj.name,
                description=f"Search {obj.name} objects. {label}",
                input_schema=search_tool(obj.name, sources[obj.name], semantic=semantic),
            )
        )
        tools.append(
            McpTool(
                name=f"{obj.name}_get",
                kind="get",
                source=obj.name,
                description=f"Read one {obj.name} by its id. {label}",
                input_schema={
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                    "additionalProperties": False,
                },
            )
        )
    page = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            "cursor": {"type": "string"},
        },
        "required": ["id"],
        "additionalProperties": False,
    }
    for link in links:
        for owner, end, other in ((link.from_, link.from_, link.to), (link.to, link.to, link.from_)):
            if owner.object_type in exposed and other.object_type in exposed:
                tools.append(
                    McpTool(
                        name=link_tool_name(owner.object_type, end.name),
                        kind="link",
                        source=link.name,
                        description=f"{other.object_type} objects linked to a "
                        f"{owner.object_type} ({link.name}).",
                        input_schema=page,
                    )
                )
    for action in actions:
        if not action.mcp_exposed:
            continue
        what = action.description or action.display_name or action.name
        tools.append(
            McpTool(
                name=action_tool_name(action.name),
                kind="action",
                source=action.name,
                description=f"Propose `{action.name}`: {what} Risk {action.risk}, validated by policy "
                f"{action.approve_policy!r}. This only creates a proposal; nothing runs before validation.",
                input_schema=action_tool_schema(action_specs[action.name]),
            )
        )
    return sorted(tools, key=lambda t: t.name)


def _checksum(package: Package) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in package.root.rglob("*") if p.suffix in {".yaml", ".yml"} and p.is_file()):
        digest.update(path.relative_to(package.root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return "sha256:" + digest.hexdigest()


def compile_package(
    package: Package, registry: Registry | None = None, validation: Validation | None = None
) -> CompiledOntology:
    """Compile a loaded package; raises :class:`OntologyError` if it is not valid."""
    registry = registry or Registry()
    validation = validation or validate(package, registry)
    if not validation.valid:
        raise OntologyError(validation)
    ontology = package.ontology
    assert ontology is not None  # guaranteed by validation (exactly one Ontology resource)
    spec: OntologySpec = ontology.spec
    datasources = {n: r.spec for n, r in package.named("Datasource").items()}
    object_specs = {n: r.spec for n, r in package.named("ObjectType").items()}
    action_specs = {n: r.spec for n, r in package.named("ActionType").items()}

    def display(kind: str, name: str) -> tuple[str | None, str | None]:
        metadata = package.named(kind)[name].metadata
        return metadata.display_name, metadata.description

    objects = [
        _object_type(n, s, display("ObjectType", n), datasources[s.datasource], spec.defaults)
        for n, s in sorted(object_specs.items())
    ]
    links = [_link_type(n, r.spec) for n, r in sorted(package.named("LinkType").items())]
    actions = [
        _action_type(n, s, display("ActionType", n), spec.defaults) for n, s in sorted(action_specs.items())
    ]
    policies = [_policy(n, r.spec) for n, r in sorted(package.named("ApprovalPolicy").items())]
    windows = []
    for name, resource in sorted(package.named("Window").items()):
        window: WindowSpec = resource.spec
        windows.append(
            IrWindow(
                name=name,
                timezone=window.timezone,
                schedule=list(window.schedule),
                freezes=list(window.freezes),
            )
        )
    return CompiledOntology(
        name=ontology.name,
        version=ontology.metadata.version or "0.0.0",
        checksum=_checksum(package),
        object_types=objects,
        link_types=links,
        action_types=actions,
        policies=policies,
        windows=windows,
        tests=sorted(package.named("OntologyTest")),
        mcp_tools=_tools(objects, links, actions, object_specs, action_specs, registry),
    )


def compile_directory(
    root: Path, registry: Registry | None = None
) -> tuple[CompiledOntology | None, Validation]:
    """Load, validate and compile a package directory; never raises on content."""
    package, load_issues = load_package(root)
    validation = validate(package, registry, load_issues)
    if not validation.valid:
        return None, validation
    return compile_package(package, registry, validation), validation
