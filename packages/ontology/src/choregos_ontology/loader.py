# SPDX-License-Identifier: Apache-2.0
"""Loading of an ontology package: YAML files, one resource per document, with positions.

Every resource keeps the line and column of each of its keys, so that a validation issue points to
the exact place in the file (contract 03 §11.5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from choregos_ontology.errors import Issue
from choregos_ontology.model import API_VERSION, SPECS, Metadata

Path_ = tuple[str | int, ...]
Position = tuple[int, int]


@dataclass(frozen=True, slots=True)
class Resource:
    """One resource of the package (``kind``, ``metadata``, typed ``spec``) and where it was read."""

    kind: str
    metadata: Metadata
    spec: Any
    file: str
    positions: dict[Path_, Position] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.metadata.name

    def where(self, *path: str | int) -> Position | None:
        """The position of the deepest known key of ``path`` (full path: ``"spec", "properties"…``)."""
        probe: Path_ = tuple(path)
        while probe:
            if probe in self.positions:
                return self.positions[probe]
            probe = probe[:-1]
        return self.positions.get(())

    def issue(self, code: str, message: str, *path: str | int) -> Issue:
        position = self.where(*path)
        return Issue(
            code=code,
            message=message,
            file=self.file,
            line=position[0] if position else None,
            column=position[1] if position else None,
            path=".".join(str(part) for part in path) or None,
        )


@dataclass(slots=True)
class Package:
    """A loaded package: its ``Ontology`` resource and all the others, in file order."""

    root: Path
    resources: list[Resource] = field(default_factory=list)

    @property
    def ontology(self) -> Resource | None:
        return next((r for r in self.resources if r.kind == "Ontology"), None)

    def of_kind(self, kind: str) -> list[Resource]:
        return [r for r in self.resources if r.kind == kind]

    def named(self, kind: str) -> dict[str, Resource]:
        """The resources of one kind by name (the first one wins; duplicates are an ``ONT004``)."""
        out: dict[str, Resource] = {}
        for resource in self.of_kind(kind):
            out.setdefault(resource.name, resource)
        return out


def _positions(node: yaml.Node, prefix: Path_ = ()) -> dict[Path_, Position]:
    out: dict[Path_, Position] = {prefix: (node.start_mark.line + 1, node.start_mark.column + 1)}
    if isinstance(node, yaml.MappingNode):
        for key_node, value_node in node.value:
            key = str(key_node.value)
            out.update(_positions(value_node, (*prefix, key)))
            out[(*prefix, key)] = (key_node.start_mark.line + 1, key_node.start_mark.column + 1)
    elif isinstance(node, yaml.SequenceNode):
        for index, item in enumerate(node.value):
            out.update(_positions(item, (*prefix, index)))
    return out


def _documents(text: str) -> list[tuple[yaml.Node, Any]]:
    loader = yaml.SafeLoader(text)
    try:
        documents: list[tuple[yaml.Node, Any]] = []
        while loader.check_node():
            node = loader.get_node()
            if node is not None:
                documents.append((node, loader.construct_document(node)))
        return documents
    finally:
        loader.dispose()


def _schema_issues(
    error: PydanticValidationError, file: str, positions: dict[Path_, Position], root: str
) -> list[Issue]:
    issues = []
    for detail in error.errors():
        path: Path_ = (root, *detail["loc"])
        probe = path
        while probe and probe not in positions:
            probe = probe[:-1]
        position = positions.get(probe)
        line, column = position if position is not None else (None, None)
        issues.append(
            Issue(
                code="ONT001",
                message=f"{detail['msg']} ({detail['type']})",
                file=file,
                line=line,
                column=column,
                path=".".join(str(part) for part in path),
            )
        )
    return issues


def _resource(document: Any, node: yaml.Node, file: str) -> tuple[Resource | None, list[Issue]]:
    positions = _positions(node)
    line, column = positions[()]
    if not isinstance(document, dict):
        return None, [Issue("ONT001", "a resource must be a mapping", file, line, column)]
    api_version, kind = document.get("apiVersion"), document.get("kind")
    if api_version != API_VERSION:
        where = positions.get(("apiVersion",), (line, column))
        message = f"unknown apiVersion {api_version!r} (expected {API_VERSION!r})"
        return None, [Issue("ONT002", message, file, where[0], where[1], "apiVersion")]
    if kind not in SPECS:
        where = positions.get(("kind",), (line, column))
        return None, [Issue("ONT002", f"unknown kind {kind!r}", file, where[0], where[1], "kind")]
    extra = sorted(set(document) - {"apiVersion", "kind", "metadata", "spec"})
    if extra:
        where = positions.get((extra[0],), (line, column))
        return None, [Issue("ONT001", f"unknown top-level key(s): {', '.join(extra)}", file, *where)]
    issues: list[Issue] = []
    try:
        metadata = Metadata.model_validate(document.get("metadata"))
    except PydanticValidationError as error:
        return None, _schema_issues(error, file, positions, "metadata")
    spec_model: type[BaseModel] = SPECS[str(kind)]
    try:
        spec = spec_model.model_validate(document.get("spec") or {})
    except PydanticValidationError as error:
        issues.extend(_schema_issues(error, file, positions, "spec"))
        return None, issues
    return Resource(str(kind), metadata, spec, file, positions), issues


def load_package(root: Path) -> tuple[Package, list[Issue]]:
    """Read every ``*.yaml`` under ``root``. Never raises on content: problems become issues."""
    package = Package(root=root)
    issues: list[Issue] = []
    files = sorted(p for p in root.rglob("*") if p.suffix in {".yaml", ".yml"} and p.is_file())
    for path in files:
        file = path.relative_to(root).as_posix()
        try:
            documents = _documents(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as error:
            mark = getattr(error, "problem_mark", None)
            line = mark.line + 1 if mark is not None else None
            column = mark.column + 1 if mark is not None else None
            issues.append(Issue("ONT001", f"invalid YAML: {error}", file, line, column))
            continue
        for node, document in documents:
            resource, found = _resource(document, node, file)
            issues.extend(found)
            if resource is not None:
                package.resources.append(resource)
    return package, issues
