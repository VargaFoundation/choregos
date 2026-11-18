# SPDX-License-Identifier: Apache-2.0
"""YAML loading with node positions, for located errors (line/column).

The DSL validator must say "line 42, column 7" and not only "missing field":
that is what the front end's workflow editor shows, and what the CLI prints.
"""

from __future__ import annotations

from typing import Any

import yaml


class PositionedDict(dict[str, Any]):
    """A dictionary that remembers where its keys come from in the source YAML."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.positions: dict[str, tuple[int, int]] = {}
        self.line: int = 0
        self.column: int = 0


class PositionedList(list[Any]):
    """A list that keeps the position of each of its items."""

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.positions: list[tuple[int, int]] = []
        self.line: int = 0
        self.column: int = 0


class PositionalLoader(yaml.SafeLoader):
    """A SafeLoader whose constructors keep the position of the nodes (via `start_mark`)."""


def _node_pos(node: Any) -> tuple[int, int]:
    mark = getattr(node, "start_mark", None)
    if mark is None:  # pragma: no cover - synthetic nodes
        return 0, 0
    return mark.line + 1, mark.column + 1


def _construct_mapping(loader: PositionalLoader, node: yaml.MappingNode) -> Any:
    loader.flatten_mapping(node)
    data = PositionedDict()
    data.line, data.column = _node_pos(node)
    yield data
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=False)
        value = loader.construct_object(value_node, deep=False)
        data[key] = value
        if isinstance(key, str):
            data.positions[key] = _node_pos(key_node)


def _construct_sequence(loader: PositionalLoader, node: yaml.SequenceNode) -> Any:
    data = PositionedList()
    data.line, data.column = _node_pos(node)
    yield data
    for child in node.value:
        data.append(loader.construct_object(child, deep=False))
        data.positions.append(_node_pos(child))


PositionalLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)
PositionalLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_SEQUENCE_TAG, _construct_sequence)


def load_yaml(text: str) -> Any:
    """Load YAML while keeping the positions (mappings and sequences)."""
    return yaml.load(text, Loader=PositionalLoader)  # noqa: S506 - loader derived from SafeLoader


def locate(document: Any, path: list[str | int]) -> tuple[int | None, int | None]:
    """Find (line, column) for a path of keys/indexes in a document loaded by `load_yaml`."""
    node: Any = document
    line: int | None = getattr(document, "line", None) or None
    column: int | None = getattr(document, "column", None) or None
    for part in path:
        found = _position_of(node, part)
        if found is not None:
            line, column = found
        try:
            node = node[part]
        except (KeyError, IndexError, TypeError):
            break
    return line, column


def _position_of(node: Any, part: str | int) -> tuple[int, int] | None:
    if isinstance(node, PositionedDict) and isinstance(part, str):
        return node.positions.get(part)
    if isinstance(node, PositionedList) and isinstance(part, int) and 0 <= part < len(node.positions):
        return node.positions[part]
    return None


def json_pointer(path: list[str | int]) -> str:
    """Render a readable path: `states.ready.display` or `transitions[2].to`."""
    out = ""
    for part in path:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else str(part)
    return out
