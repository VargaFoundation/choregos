# SPDX-License-Identifier: Apache-2.0
"""Validation of an ontology package, with stable error codes (contract 03 §11.5).

The validator reports every error it finds. Each implemented code has a red test and a green test;
the codes this trial does not implement yet are listed in ``NOT_YET``, so that a reader knows which
guards are still missing instead of believing they exist.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import celpy
from celpy import celtypes
from celpy.adapter import json_to_cel

from choregos_ontology.errors import Issue, Validation
from choregos_ontology.loader import Package, Resource
from choregos_ontology.mcpgen import (
    RESERVED_LINK_NAMES,
    RESERVED_TOOLS,
    SCALAR_TYPES,
    TOOL_NAME,
    is_known_type,
    plan_tool_names,
)
from choregos_ontology.model import (
    ActionTypeSpec,
    ApprovalPolicySpec,
    DatasourceSpec,
    LinkTypeSpec,
    ObjectTypeSpec,
    OntologyTestSpec,
)

DEFINITION_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
PACKAGE_NAME = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
WHOLE_EXPRESSION = re.compile(r"^\$\{(.*)\}$", re.DOTALL)

CORE_EFFECTS = frozenset(
    {
        "connector.operation",
        "connector.sync",
        "object.create",
        "object.update",
        "object.delete",
        "run.start",
        "workflow.start",
        "notify",
    }
)
CORE_EVIDENCE = frozenset({"object.reread", "run.facts", "connector.query", "collector.rerun"})

NOT_YET = ("ONT009", "ONT014", "ONT015", "ONT017", "ONT024", "ONTW01")
"""Codes of the contract that this trial does not check yet (functions, CEL typing, cycles…)."""


@dataclass(frozen=True, slots=True)
class Registry:
    """What the installation provides: registered effect and evidence types, engines, embeddings."""

    effects: frozenset[str] = CORE_EFFECTS
    evidence: frozenset[str] = CORE_EVIDENCE
    sql_engine: bool = False
    embeddings: bool = False
    reserved_tools: frozenset[str] = field(default=RESERVED_TOOLS)


_CEL = celpy.Environment()


def _cel_syntax(expr: str) -> str | None:
    try:
        _CEL.compile(expr)
    except Exception as error:  # celpy raises its own parse errors, all subclasses of Exception
        return str(error).splitlines()[0]
    return None


def _expressions(value: Any) -> Iterable[str]:
    """Every ``${…}`` expression inside an effect's ``with`` (whole-string values only)."""
    if isinstance(value, str):
        match = WHOLE_EXPRESSION.match(value)
        if match:
            yield match.group(1)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _expressions(item)
    elif isinstance(value, list):
        for item in value:
            yield from _expressions(item)


def _rule_may_apply(when: str, action: dict[str, str]) -> bool:
    """Whether a policy rule can apply to an action, judged on ``action`` alone (fail-closed)."""
    try:
        program = _CEL.program(_CEL.compile(when))
        result = program.evaluate({"action": json_to_cel(action)})
    except Exception:
        return True
    if isinstance(result, celtypes.BoolType):
        return bool(result)
    return True  # an evaluation error (unknown variable…) cannot prove the rule never applies


class _Checker:
    def __init__(self, package: Package, registry: Registry) -> None:
        self.package = package
        self.registry = registry
        self.errors: list[Issue] = []
        self.warnings: list[Issue] = []
        self.datasources: dict[str, DatasourceSpec] = {
            n: r.spec for n, r in package.named("Datasource").items()
        }
        self.objects: dict[str, ObjectTypeSpec] = {n: r.spec for n, r in package.named("ObjectType").items()}
        self.links: dict[str, LinkTypeSpec] = {n: r.spec for n, r in package.named("LinkType").items()}
        self.actions: dict[str, ActionTypeSpec] = {n: r.spec for n, r in package.named("ActionType").items()}
        self.policies: dict[str, ApprovalPolicySpec] = {
            n: r.spec for n, r in package.named("ApprovalPolicy").items()
        }
        self.windows = package.named("Window")

    def error(self, resource: Resource, code: str, message: str, *path: str | int) -> None:
        self.errors.append(resource.issue(code, message, *path))

    def cel(self, resource: Resource, expr: str, *path: str | int) -> None:
        problem = _cel_syntax(expr)
        if problem:
            self.error(resource, "ONT013", f"CEL syntax error: {problem}", *path)

    def run(self) -> None:
        self.names()
        for resource in self.package.of_kind("ObjectType"):
            self.object_type(resource)
        for resource in self.package.of_kind("Datasource"):
            self.datasource(resource)
        for resource in self.package.of_kind("LinkType"):
            self.link_type(resource)
        for resource in self.package.of_kind("ActionType"):
            self.action_type(resource)
        for resource in self.package.of_kind("ApprovalPolicy"):
            for index, rule in enumerate(resource.spec.rules):
                self.cel(resource, rule.when, "spec", "rules", index, "when")
        for resource in self.package.of_kind("OntologyTest"):
            self.ontology_test(resource)
        self.tool_names()

    def names(self) -> None:
        ontologies = self.package.of_kind("Ontology")
        if len(ontologies) != 1:
            where = ontologies[1] if ontologies else None
            message = f"a package has exactly one Ontology resource, found {len(ontologies)}"
            self.errors.append(where.issue("ONT001", message) if where else Issue("ONT001", message))
        counts = Counter((r.kind, r.name) for r in self.package.resources)
        seen: set[tuple[str, str]] = set()
        for resource in self.package.resources:
            pattern = PACKAGE_NAME if resource.kind == "Ontology" else DEFINITION_NAME
            if not pattern.match(resource.name):
                self.error(resource, "ONT003", f"invalid name {resource.name!r}", "metadata", "name")
            key = (resource.kind, resource.name)
            if counts[key] > 1 and key in seen:
                self.error(
                    resource, "ONT004", f"duplicate {resource.kind} {resource.name!r}", "metadata", "name"
                )
            seen.add(key)

    def object_type(self, resource: Resource) -> None:
        spec: ObjectTypeSpec = resource.spec
        if spec.datasource not in self.datasources:
            self.error(resource, "ONT005", f"unknown datasource {spec.datasource!r}", "spec", "datasource")
        for name, prop in spec.properties.items():
            if not DEFINITION_NAME.match(name):
                self.error(resource, "ONT003", f"invalid property name {name!r}", "spec", "properties", name)
            if not is_known_type(prop.type):
                message = f"unknown property type {prop.type!r}"
                self.error(resource, "ONT010", message, "spec", "properties", name, "type")
        key = spec.properties.get(spec.primary_key)
        if key is None or not key.required or key.type not in SCALAR_TYPES:
            message = f"primaryKey {spec.primary_key!r} must name a required scalar property"
            self.error(resource, "ONT011", message, "spec", "primaryKey")
        for name, derived in spec.derived.items():
            if name in spec.properties:
                message = f"derived property {name!r} collides with a property"
                self.error(resource, "ONT016", message, "spec", "derived", name)
            self.cel(resource, derived.expr, "spec", "derived", name, "expr")
        semantic = spec.search.semantic if spec.search else None
        if semantic is not None:
            for index, field_name in enumerate(semantic.fields):
                field_prop = spec.properties.get(field_name)
                if field_prop is None or field_prop.type != "string":
                    message = f"semantic search field {field_name!r} must be a string property"
                    self.error(resource, "ONT023", message, "spec", "search", "semantic", "fields", index)
        searchable = any(p.searchable for p in spec.properties.values())
        if (semantic is not None or searchable) and not self.registry.embeddings:
            message = "searchable or semantic properties without an embeddings model: no `query` parameter"
            self.warnings.append(resource.issue("ONTW02", message, "spec", "search"))

    def datasource(self, resource: Resource) -> None:
        spec: DatasourceSpec = resource.spec
        if spec.type in {"iceberg", "sql"} and not self.registry.sql_engine:
            message = f"datasource of type {spec.type!r} needs a registered SqlEngine (the data platform)"
            self.error(resource, "ONT026", message, "spec", "type")
        if spec.type == "table" and spec.mapping:
            self.error(resource, "ONT012", "a `table` datasource has no mapping", "spec", "mapping")
        users = {n: o for n, o in self.objects.items() if o.datasource == resource.name}
        for column_owner, object_spec in users.items():
            for prop in spec.mapping or {}:
                if prop not in object_spec.properties:
                    message = f"mapping targets unknown property {prop!r} of {column_owner!r}"
                    self.error(resource, "ONT012", message, "spec", "mapping", prop)

    def link_type(self, resource: Resource) -> None:
        spec: LinkTypeSpec = resource.spec
        for side, end in (("from", spec.from_), ("to", spec.to)):
            if end.object_type not in self.objects:
                message = f"unknown object type {end.object_type!r}"
                self.error(resource, "ONT006", message, "spec", side, "objectType")
            if end.name in RESERVED_LINK_NAMES:
                message = f"link name {end.name!r} is reserved for generated tools"
                self.error(resource, "ONT025", message, "spec", side, "name")
        if spec.foreign_key is not None:
            owner = spec.to if spec.foreign_key.side == "to" else spec.from_
            owner_spec = self.objects.get(owner.object_type)
            if owner_spec is not None and spec.foreign_key.property not in owner_spec.properties:
                message = (
                    f"foreign key {spec.foreign_key.property!r} is not a property of {owner.object_type!r}"
                )
                self.error(resource, "ONT006", message, "spec", "foreignKey", "property")

    def action_type(self, resource: Resource) -> None:  # noqa: C901, PLR0912 — one rule per contract line
        spec: ActionTypeSpec = resource.spec
        target = spec.target
        if target.cardinality == "none":
            if target.object_type is not None and target.object_type not in self.objects:
                self.error(
                    resource, "ONT006", f"unknown object type {target.object_type!r}", "spec", "target"
                )
        elif target.object_type not in self.objects:
            self.error(resource, "ONT006", f"unknown object type {target.object_type!r}", "spec", "target")
        for name, prop in spec.parameters.items():
            if not DEFINITION_NAME.match(name):
                self.error(resource, "ONT003", f"invalid parameter name {name!r}", "spec", "parameters", name)
            if not is_known_type(prop.type):
                message = f"unknown parameter type {prop.type!r}"
                self.error(resource, "ONT010", message, "spec", "parameters", name, "type")
        for index, pre in enumerate(spec.preconditions):
            self.cel(resource, pre.expr, "spec", "preconditions", index, "expr")
        policy_name = spec.permissions.approve.removeprefix("policy:")
        policy = self.policies.get(policy_name)
        if not spec.permissions.approve.startswith("policy:") or policy is None:
            message = f"unknown approval policy {spec.permissions.approve!r}"
            self.error(resource, "ONT007", message, "spec", "permissions", "approve")
        if spec.window is not None and spec.window not in self.windows:
            self.error(resource, "ONT008", f"unknown window {spec.window!r}", "spec", "window")
        for group in ("effects", "compensation"):
            for index, effect in enumerate(getattr(spec, group)):
                if effect.type not in self.registry.effects:
                    message = f"unknown effect type {effect.type!r}"
                    self.error(resource, "ONT027", message, "spec", group, index, "type")
                for expr in _expressions(effect.with_):
                    self.cel(resource, expr, "spec", group, index, "with")
        for index, evidence in enumerate(spec.evidence):
            if evidence.collect.type not in self.registry.evidence:
                message = f"unknown evidence type {evidence.collect.type!r}"
                self.error(resource, "ONT027", message, "spec", "evidence", index, "collect", "type")
            self.cel(resource, evidence.expect, "spec", "evidence", index, "expect")
        if spec.idempotency_key is not None:
            for expr in _expressions(spec.idempotency_key):
                self.cel(resource, expr, "spec", "idempotencyKey")
        if spec.reversibility == "compensable" and not spec.compensation:
            self.error(
                resource, "ONT018", "a compensable action declares a compensation", "spec", "reversibility"
            )
        if spec.risk != "low" and not spec.evidence:
            message = f"an action of risk {spec.risk!r} declares at least one evidence"
            self.error(resource, "ONT020", message, "spec", "evidence")
        if spec.sequencing is not None:
            if target.cardinality != "many":
                self.error(resource, "ONT022", "sequencing needs `cardinality: many`", "spec", "sequencing")
            for index, expr in enumerate(spec.sequencing.between):
                self.cel(resource, expr, "spec", "sequencing", "between", index)
        if policy is not None:
            self.policy_for(resource, spec, policy)

    def policy_for(self, resource: Resource, spec: ActionTypeSpec, policy: ApprovalPolicySpec) -> None:
        action = {"type": resource.name, "risk": spec.risk, "reversibility": spec.reversibility}
        for index, rule in enumerate(policy.rules):
            if not _rule_may_apply(rule.when, action):
                continue
            if rule.approve == "auto" and spec.reversibility == "irreversible":
                message = f"irreversible action reachable by the `approve: auto` rule {index} of its policy"
                self.error(resource, "ONT019", message, "spec", "reversibility")
            if rule.approve != "auto" and spec.risk in {"high", "critical"} and rule.step_up is None:
                message = f"rule {index} of the policy validates a {spec.risk} action without stepUp"
                self.error(resource, "ONT021", message, "spec", "permissions", "approve")

    def ontology_test(self, resource: Resource) -> None:
        spec: OntologyTestSpec = resource.spec
        if spec.action not in self.actions:
            self.error(
                resource, "ONT001", f"test references an unknown action {spec.action!r}", "spec", "action"
            )
        for type_name in spec.fixtures:
            if type_name not in self.objects:
                message = f"fixtures of an unknown object type {type_name!r}"
                self.error(resource, "ONT006", message, "spec", "fixtures", type_name)

    def tool_names(self) -> None:
        names = plan_tool_names(self.objects, self.links, self.actions)
        counts = Counter(name for name, _, _ in names)
        for name, kind, source in names:
            owner = self._owner(kind, source)
            problem = None
            if len(name) > 64 or not TOOL_NAME.match(name):
                problem = f"generated tool name {name!r} is invalid or longer than 64 characters"
            elif name in self.registry.reserved_tools:
                problem = f"generated tool name {name!r} is reserved"
            elif counts[name] > 1:
                problem = f"generated tool name {name!r} is produced twice"
            if problem and owner is not None:
                self.error(owner, "ONT025", problem, "metadata", "name")

    def _owner(self, kind: str, source: str) -> Resource | None:
        resource_kind = {
            "search": "ObjectType",
            "get": "ObjectType",
            "link": "LinkType",
            "action": "ActionType",
        }
        return self.package.named(resource_kind[kind]).get(source)


def validate(
    package: Package, registry: Registry | None = None, load_issues: list[Issue] | None = None
) -> Validation:
    """Validate a loaded package; ``load_issues`` are the problems already found while loading."""
    checker = _Checker(package, registry or Registry())
    checker.run()
    return Validation(errors=[*(load_issues or []), *checker.errors], warnings=checker.warnings)
