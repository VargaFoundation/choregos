"""Chargement YAML avec position des nœuds, pour des erreurs localisées (ligne/colonne).

Le validateur du DSL doit dire « ligne 42, colonne 7 » et pas seulement « champ manquant » :
c'est ce que l'éditeur de workflow du front affiche, et ce que la CLI imprime.
"""

from __future__ import annotations

from typing import Any

import yaml


class PositionedDict(dict[str, Any]):
    """Dictionnaire qui se souvient d'où viennent ses clés dans le YAML source."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.positions: dict[str, tuple[int, int]] = {}
        self.line: int = 0
        self.column: int = 0


class PositionedList(list[Any]):
    """Liste qui retient la position de chacun de ses éléments."""

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.positions: list[tuple[int, int]] = []
        self.line: int = 0
        self.column: int = 0


class PositionalLoader(yaml.SafeLoader):
    """SafeLoader dont les constructeurs conservent la position des nœuds (via `start_mark`)."""


def _node_pos(node: Any) -> tuple[int, int]:
    mark = getattr(node, "start_mark", None)
    if mark is None:  # pragma: no cover - nœuds synthétiques
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
    """Charge du YAML en conservant les positions (mappings et séquences)."""
    return yaml.load(text, Loader=PositionalLoader)  # noqa: S506 - loader dérivé de SafeLoader


def locate(document: Any, path: list[str | int]) -> tuple[int | None, int | None]:
    """Retrouve (ligne, colonne) d'un chemin de clés/index dans un document chargé par `load_yaml`."""
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
    """Rend un chemin lisible : `states.ready.display` ou `transitions[2].to`."""
    out = ""
    for part in path:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else str(part)
    return out
