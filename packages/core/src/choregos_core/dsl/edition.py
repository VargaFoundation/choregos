# SPDX-License-Identifier: Apache-2.0
"""Typed edits of a workflow, grafted into the TEXT (ADR 0031).

The map and the process view modify a workflow without rewriting its YAML: each operation becomes
one or more grafts (the replacement of a range of characters) guided by `yaml.compose`, which
gives the position of each node. The rest of the document, comments and style included, is not
touched. `ruamel` would rewrite the style, and it is not a dependency.

Each operation returns its **inverse**: applied right after, it gives the original bytes back.
That is what lets the console undo, and what the tests check operation by operation.
To get there, an item of a block collection takes with it the comments that precede it (they
describe it), and the inverse of a removal carries the removed text, as it was.

An operation whose target does not exist is refused (`EditRefused`); an operation that makes the
workflow invalid is not: the validator will say so, and the console does not save an invalid
workflow.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

import yaml
from choregos_contracts.workflow import PRODUCTION_PREFIX, implicit_effect
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

#: The states whose NAME still carries an effect, for a workflow written before #175 (`does`,
#: `production`): a rename writes it first, an addition or a removal announces it.
PREFIXES_WITH_EFFECT = ("pr_", "merged", "deployed_prod")
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]*$")


class EditRefused(ValueError):  # noqa: N818 - a reasoned refusal, rendered as a 422
    pass


# ───────────────────────────── operations ─────────────────────────────


class _Op(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class AddState(_Op):
    op: Literal["add_state"] = "add_state"
    name: str
    #: `{display, kind, terminal, tracker}`; `display` defaults to the name.
    spec: dict[str, Any] | None = None
    #: The exact block to put back: the inverse of a removal.
    text: str | None = None
    index: int | None = None


class RemoveState(_Op):
    op: Literal["remove_state"] = "remove_state"
    name: str


class RenameState(_Op):
    op: Literal["rename_state"] = "rename_state"
    from_: str = Field(alias="from")
    to: str


class _SetField(_Op):
    field: str
    #: The value to write: a scalar, or a collection rendered in flow style.
    value: Any = None
    #: The exact text of the value: the inverse of a change.
    raw: str | None = None
    #: Remove the key.
    unset: bool = False
    #: The exact block of the key to put back, and its place: the inverse of a removal.
    text: str | None = None
    index: int | None = None
    #: `block` writes a collection value as indented lines — a `task`, an `action`, a `spawn` — so that
    #: the diff a reviewer reads is one field per line (ADR 0112 §3); `flow` keeps it on one line.
    style: Literal["flow", "block"] = "flow"
    #: With `text`: put this exact block back IN PLACE of the key's current one — the inverse of a
    #: write that replaced a value written in block style.
    swap: bool = False


class SetState(_SetField):
    op: Literal["set_state"] = "set_state"
    name: str


class AddTransition(_Op):
    op: Literal["add_transition"] = "add_transition"
    #: `{id, from, to, by, …}`; without `id`, `t-<from>-<to>`.
    transition: dict[str, Any] | None = None
    text: str | None = None
    index: int | None = None


class RemoveTransition(_Op):
    op: Literal["remove_transition"] = "remove_transition"
    id: str


class SetTransition(_SetField):
    op: Literal["set_transition"] = "set_transition"
    id: str


class AddGate(_Op):
    op: Literal["add_gate"] = "add_gate"
    transition: str
    gate: str | dict[str, Any] | None = None
    text: str | None = None
    index: int | None = None


class RemoveGate(_Op):
    op: Literal["remove_gate"] = "remove_gate"
    transition: str
    name: str
    #: Its place, when known: the inverse of an addition gives it.
    index: int | None = None


class AddActor(_Op):
    op: Literal["add_actor"] = "add_actor"
    name: str
    spec: dict[str, Any] | None = None
    text: str | None = None
    index: int | None = None


class RemoveActor(_Op):
    op: Literal["remove_actor"] = "remove_actor"
    name: str


class SetActor(_SetField):
    op: Literal["set_actor"] = "set_actor"
    name: str


class SetWorkflow(_SetField):
    """The workflow's own fields (ADR 0112 §3): `metadata.description`, `metadata.inputs` (the work item's
    fields), `metadata.catalogue`, and `initial`. The name and the version are not edited here: the name
    is the route's, the version is the publication's."""

    op: Literal["set_workflow"] = "set_workflow"


Operation = Annotated[
    AddState
    | RemoveState
    | RenameState
    | SetState
    | AddTransition
    | RemoveTransition
    | SetTransition
    | AddGate
    | RemoveGate
    | AddActor
    | RemoveActor
    | SetActor
    | SetWorkflow,
    Field(discriminator="op"),
]
OPERATIONS: TypeAdapter[list[Operation]] = TypeAdapter(list[Operation])


@dataclass
class Edition:
    """The edited text, the operations that undo it (in the order to play them), the warnings."""

    yaml: str
    inverse: list[Any] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def edit(text: str, operations: list[Any]) -> Edition:
    """Apply the operations in order; the inverse undoes them in the opposite order.

    Renaming a state whose NAME carries an effect (a workflow written before #175) writes it first:
    `does` on the system transitions that lead to it, `production: true` on the state. The rename
    no longer changes what the workflow does, and its inverse undoes the whole, to the byte."""
    edition = Edition(yaml=text)
    for operation in OPERATIONS.validate_python(operations):
        steps: list[Any] = [operation]
        if isinstance(operation, RenameState):
            written = _effects_to_write(edition.yaml, operation.from_)
            steps = [*written, operation]
            if written:
                edition.warnings.append(
                    f"the effect the name `{operation.from_}` carried is now written "
                    "(`does`, `production`): the rename does not change what the workflow does"
                )
        for step in steps:
            apply = _APPLIERS[step.op]
            edition.yaml, inverse, notices = apply(edition.yaml, step)
            edition.inverse.insert(0, inverse)
            edition.warnings.extend(notices)
    return edition


def _effects_to_write(text: str, name: str) -> list[Any]:
    """What must be WRITTEN before renaming `name` so that the rename changes nothing (#175)."""
    effect = implicit_effect(name)
    production = name.startswith(PRODUCTION_PREFIX)
    if effect is None and not production:
        return []
    data = yaml.safe_load(text) or {}
    actors = data.get("actors") or {}
    system_actors = {n for n, a in actors.items() if isinstance(a, dict) and a.get("type") == "system"}
    written: list[Any] = []
    for transition in data.get("transitions") or []:
        if not isinstance(transition, dict) or effect is None:
            continue
        if (
            transition.get("to") == name
            and transition.get("by") in system_actors
            and not transition.get("does")
        ):
            if not transition.get("id"):
                raise EditRefused(
                    f"the transition to `{name}` has no `id`: its effect cannot be written before the "
                    "rename — give it an `id`, or write `does` by hand"
                )
            written.append(SetTransition(id=str(transition["id"]), field="does", value=effect))
    state = (data.get("states") or {}).get(name)
    if production and not (isinstance(state, dict) and state.get("production")):
        written.append(SetState(name=name, field="production", value=True))
    return written


# ───────────────────────────── reading the text ─────────────────────────────


def _compose(text: str) -> MappingNode:
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as error:
        raise EditRefused(f"unreadable YAML: {error}") from error
    if not isinstance(root, MappingNode):
        raise EditRefused("the document must be a YAML object")
    return root


def _find_key(mapping: Node | None, name: str) -> tuple[ScalarNode, Node] | None:
    if isinstance(mapping, MappingNode):
        for key, value in mapping.value:
            if isinstance(key, ScalarNode) and key.value == name:
                return key, value
    return None


def _value_of(mapping: Node | None, name: str) -> Node | None:
    found = _find_key(mapping, name)
    return found[1] if found is not None else None


def _key_index(mapping: Node, name: str) -> int:
    assert isinstance(mapping, MappingNode)
    return next(
        i for i, (key, _) in enumerate(mapping.value) if isinstance(key, ScalarNode) and key.value == name
    )


def _content_end(node: Node) -> int:
    """Where the last SIGNIFICANT character of a node ends.

    A block ends, for PyYAML, at the next token (the following line, comments included); its
    leaves, scalars and flow collections, end exactly.
    """
    if isinstance(node, ScalarNode) or getattr(node, "flow_style", False):
        return int(node.end_mark.index)
    children: list[Node] = []
    if isinstance(node, MappingNode):
        for key, value in node.value:
            children += [key, value]
    elif isinstance(node, SequenceNode):
        children = list(node.value)
    return max((_content_end(e) for e in children), default=int(node.start_mark.index))


def _line_end(text: str, i: int) -> int:
    if i > 0 and text[i - 1] == "\n":
        return i
    j = text.find("\n", i)
    return len(text) if j < 0 else j + 1


def _line_start(text: str, i: int) -> int:
    return text.rfind("\n", 0, i) + 1


def _render(value: Any) -> str:
    rendered = yaml.safe_dump(
        value, default_flow_style=True, allow_unicode=True, sort_keys=False, width=10**6
    )
    if rendered.endswith("\n...\n"):
        rendered = rendered[: -len("\n...\n")]
    return rendered.strip()


def _graft(text: str, start: int, end: int, new: str) -> str:
    return text[:start] + new + text[end:]


# ───────────────────────────── collections ─────────────────────────────


@dataclass
class _Collection:
    """A collection of the document: mapping or sequence, block or flow.

    `bound`: where the lines of its first item begin, the end of the line of its key. A block
    object, item of a sequence, has no key: its first field shares the dash's line,
    and `first_frozen` forbids touching it.
    """

    text: str
    name: str
    node: Node
    bound: int
    first_frozen: bool = False
    #: How to find the same collection again in an edited text (its nodes move).
    relocate: Callable[[str], _Collection] | None = None

    def reread(self, text: str) -> _Collection:
        if self.relocate is None:
            raise EditRefused(f"`{self.name}` cannot be read again after an edit")
        return self.relocate(text)

    @property
    def is_flow(self) -> bool:
        return bool(getattr(self.node, "flow_style", False))

    @property
    def size(self) -> int:
        return len(self.node.value) if isinstance(self.node, MappingNode | SequenceNode) else 0

    def _extents(self) -> list[tuple[int, int]]:
        """(start, end) of the content of each item."""
        if isinstance(self.node, MappingNode):
            return [
                (int(c.start_mark.index), max(_content_end(c), _content_end(v))) for c, v in self.node.value
            ]
        return [(int(e.start_mark.index), _content_end(e)) for e in self.node.value]

    def _blocks(self) -> list[tuple[int, int]]:
        """The lines of each item of a block collection, leading comments included."""
        previous, blocks = self.bound, []
        for _, end in self._extents():
            block_end = _line_end(self.text, end)
            blocks.append((previous, block_end))
            previous = block_end
        return blocks

    def _guard_frozen(self, i: int) -> None:
        if self.first_frozen and not self.is_flow and i == 0:
            raise EditRefused(f"the first field of `{self.name}` shares the dash's line: edit the YAML")

    def remove(self, i: int) -> tuple[str, str]:
        """The text without item `i`, and the removed text (for the inverse)."""
        self._guard_frozen(i)
        if self.size == 1 and not self.is_flow and self.name in {"states", "actors", "transitions"}:
            # Empty, the block would read as `null`: nothing could be put back, and a workflow keeps one.
            raise EditRefused(f"`{self.name}` keeps at least one item: add another before removing this one")
        if self.is_flow:
            bornes = self._extents()
            if len(bornes) == 1:
                start, end = int(self.node.start_mark.index) + 1, int(self.node.end_mark.index) - 1
            elif i < len(bornes) - 1:
                start, end = bornes[i][0], bornes[i + 1][0]
            else:
                start, end = bornes[i - 1][1], bornes[i][1]
        else:
            start, end = self._blocks()[i]
            if end == len(self.text) and not self.text.endswith("\n") and start > 0:
                start -= (
                    1  # the last item of a file without a final line break takes the previous one with it
                )
        return _graft(self.text, start, end, ""), self.text[start:end]

    def _position(self, i: int) -> int:
        n = self.size
        if not 0 <= i <= n:
            raise EditRefused(f"position {i} outside `{self.name}` ({n} items)")
        if i < n:
            self._guard_frozen(i)
        if self.is_flow:
            bornes = self._extents()
            if n == 0:
                return int(self.node.start_mark.index) + 1
            return bornes[i][0] if i < n else bornes[-1][1]
        blocks = self._blocks()
        if not blocks:
            raise EditRefused(f"`{self.name}` is empty: write it in flow style (`[]` or `{{}}`)")
        return blocks[i][0] if i < n else blocks[-1][1]

    def insert_raw(self, i: int, raw: str) -> str:
        """Put back, at place `i`, the text a removal returned."""
        position = self._position(i)
        return _graft(self.text, position, position, raw)

    def insert(self, i: int, element: str) -> str:
        """Insert a NEW item: `key: value` of a mapping, or `- value` of a sequence."""
        n = self.size
        position = self._position(i)
        if self.is_flow:
            raw = element if n == 0 else (f"{element}, " if i < n else f", {element}")
            return _graft(self.text, position, position, raw)
        # The indentation of the first item: in front of a sequence's dash; under a mapping's first
        # key, even when it shares a dash's line.
        first = self._extents()[0][0]
        before = self.text[_line_start(self.text, first) : first]
        if isinstance(self.node, SequenceNode):
            indent = before[: len(before) - len(before.lstrip())]
        else:
            indent = " " * len(before)
        line = f"{indent}{element}"
        raw = f"\n{line}" if position == len(self.text) and not self.text.endswith("\n") else f"{line}\n"
        return _graft(self.text, position, position, raw)


def _sub_collection(text: str, key: ScalarNode, node: Node) -> _Collection:
    return _Collection(text, str(key.value), node, _line_end(text, int(key.end_mark.index)))


def _sequence_item(text: str, name: str, element: Node) -> _Collection:
    """An object that is an item of a sequence: its fields start at the first one, on the dash's line."""
    return _Collection(text, name, element, int(element.start_mark.index), first_frozen=True)


def _collection(text: str, parent: Node, name: str) -> _Collection:
    found = _find_key(parent, name)
    if found is None:
        raise EditRefused(f"no `{name}` here")
    key, node = found
    if not isinstance(node, MappingNode | SequenceNode):
        raise EditRefused(f"`{name}` is not a collection")
    return _sub_collection(text, key, node)


def _block(text: str, carrier: _Collection, name: str, value: Any) -> str:
    """`name: value` as indented lines, at the indentation of the carrier's keys."""
    assert isinstance(carrier.node, MappingNode) and carrier.node.value
    column = int(carrier.node.value[0][0].start_mark.column)
    rendered = yaml.safe_dump(
        {name: value}, default_flow_style=False, allow_unicode=True, sort_keys=False, width=100
    )
    return "".join(" " * column + line for line in rendered.splitlines(keepends=True))


def _insert_lines(text: str, carrier: _Collection, place: int, lines: str) -> str:
    """Whole lines at `place`, as a removal gives them back — or, at the very end of a file without a
    final line break, preceded by one."""
    position = carrier._position(place)
    if position == len(text) and not text.endswith("\n"):
        lines = "\n" + lines.rstrip("\n")
    return _graft(text, position, position, lines)


def _set_key(
    text: str, carrier: _Collection, op: _SetField, new_place: int | None = None
) -> tuple[str, dict[str, Any]]:
    """A field of an item: written, replaced, removed or put back; returns what undoes it.

    `new_place`: where a key that does not exist yet goes (the end of the carrier by default)."""
    if not isinstance(carrier.node, MappingNode):
        raise EditRefused(f"`{carrier.name}` is not an object")
    found = _find_key(carrier.node, op.field)
    if op.text is not None and op.swap:
        if found is None:
            raise EditRefused(f"`{op.field}` does not exist")
        place = _key_index(carrier.node, op.field)
        without, removed = carrier.remove(place)
        rest = _read_again(without, carrier)
        return rest.insert_raw(place, op.text), {
            "field": op.field,
            "text": removed,
            "index": place,
            "swap": True,
        }
    if op.text is not None:
        if found is not None:
            raise EditRefused(f"`{op.field}` already exists")
        place = carrier.size if op.index is None else op.index
        return carrier.insert_raw(place, op.text), {"field": op.field, "unset": True}
    if op.unset:
        if found is None:
            raise EditRefused(f"`{op.field}` does not exist")
        place = _key_index(carrier.node, op.field)
        without, removed = carrier.remove(place)
        return without, {"field": op.field, "text": removed, "index": place}
    as_block = (
        op.style == "block"
        and op.raw is None
        and isinstance(op.value, dict | list)
        and bool(op.value)
        and not carrier.is_flow
        and carrier.size > 0
    )
    value = found[1] if found is not None else None
    written_as_block = isinstance(value, MappingNode | SequenceNode) and not value.flow_style
    if found is not None and (as_block or written_as_block):
        # Replace the key's whole lines: its value is, or becomes, a block.
        place = _key_index(carrier.node, op.field)
        without, removed = carrier.remove(place)
        rest = _read_again(without, carrier)
        if as_block:
            edited = _insert_lines(without, rest, place, _block(without, rest, op.field, op.value))
        else:
            new = op.raw if op.raw is not None else _render(op.value)
            edited = rest.insert(place, f"{op.field}: {new}")
        return edited, {"field": op.field, "text": removed, "index": place, "swap": True}
    place = carrier.size if new_place is None else new_place
    if found is None and as_block:
        return _insert_lines(text, carrier, place, _block(text, carrier, op.field, op.value)), {
            "field": op.field,
            "unset": True,
        }
    new = op.raw if op.raw is not None else _render(op.value)
    if found is None:
        return carrier.insert(place, f"{op.field}: {new}"), {"field": op.field, "unset": True}
    assert value is not None
    start, end = int(value.start_mark.index), int(value.end_mark.index)
    return _graft(text, start, end, new), {"field": op.field, "raw": text[start:end]}


def _read_again(text: str, carrier: _Collection) -> _Collection:
    """The same carrier, read again in `text` after an edit: its nodes moved."""
    return carrier.reread(text)


# ───────────────────────────── states ─────────────────────────────


def _state_references(root: MappingNode, name: str) -> list[ScalarNode]:
    """Each scalar that NAMES the state, other than its key in `states`."""
    candidates: list[Node | None] = [_value_of(root, "initial")]
    transitions = _value_of(root, "transitions")
    for transition in transitions.value if isinstance(transitions, SequenceNode) else []:
        candidates += [_value_of(transition, field_name) for field_name in ("from", "to", "on_reject")]
        for retry_key in ("on_fail", "on_changes_requested"):
            sub = _value_of(transition, retry_key)
            candidates += [_value_of(sub, field_name) for field_name in ("to", "escalate_to")]
    defaults = _value_of(root, "defaults")
    agents = _value_of(defaults, "from_any_agent_state")
    candidates += [
        _value_of(agents, field_name) for field_name in ("on_question", "on_budget_exceeded", "on_timeout")
    ]
    candidates.append(_value_of(_value_of(defaults, "needs_human"), "on_abandon"))
    return [n for n in candidates if isinstance(n, ScalarNode) and n.value == name]


def _effect_warning(name: str) -> list[str]:
    if name.startswith(PREFIXES_WITH_EFFECT):
        return [f"the state `{name}` carries an effect through its name (opening a PR, merging, production)"]
    return []


def _states_of(text: str) -> tuple[MappingNode, _Collection]:
    root = _compose(text)
    return root, _collection(text, root, "states")


def _add_state(text: str, op: AddState) -> tuple[str, Any, list[str]]:
    _, states = _states_of(text)
    if _find_key(states.node, op.name) is not None:
        raise EditRefused(f"the state `{op.name}` already exists")
    place = states.size if op.index is None else op.index
    if op.text is not None:
        return states.insert_raw(place, op.text), RemoveState(name=op.name), []
    if not IDENTIFIER.match(op.name):
        raise EditRefused(f"`{op.name}` is not a state name (lowercase letters, digits, `_`, `-`)")
    spec = {"display": op.name, **(op.spec or {})}
    return (
        states.insert(place, f"{op.name}: {_render(spec)}"),
        RemoveState(name=op.name),
        _effect_warning(op.name),
    )


def _remove_state(text: str, op: RemoveState) -> tuple[str, Any, list[str]]:
    root, states = _states_of(text)
    if _find_key(states.node, op.name) is None:
        raise EditRefused(f"the state `{op.name}` does not exist")
    references = _state_references(root, op.name)
    if references:
        line_numbers = sorted({r.start_mark.line + 1 for r in references})
        raise EditRefused(
            f"the state `{op.name}` is still named on line(s) {line_numbers}: remove its transitions first"
        )
    place = _key_index(states.node, op.name)
    without, removed = states.remove(place)
    return without, AddState(name=op.name, text=removed, index=place), _effect_warning(op.name)


def _rename_state(text: str, op: RenameState) -> tuple[str, Any, list[str]]:
    root, states = _states_of(text)
    found = _find_key(states.node, op.from_)
    if found is None:
        raise EditRefused(f"the state `{op.from_}` does not exist")
    if not IDENTIFIER.match(op.to):
        raise EditRefused(f"`{op.to}` is not a state name (lowercase letters, digits, `_`, `-`)")
    if _find_key(states.node, op.to) is not None or _state_references(root, op.to):
        raise EditRefused(f"`{op.to}` is already a state, or already named in the workflow")
    nodes = [found[0], *_state_references(root, op.from_)]
    # From the end to the start: each graft keeps the positions of the previous ones valid.
    for node in sorted(nodes, key=lambda n: -int(n.start_mark.index)):
        start, end = int(node.start_mark.index), int(node.end_mark.index)
        if node.style in {'"', "'"}:
            start, end = start + 1, end - 1
        text = _graft(text, start, end, op.to)
    return text, RenameState(**{"from": op.to, "to": op.from_}), _effect_warning(op.to)


def _state_carrier(text: str, name: str) -> _Collection:
    _, states = _states_of(text)
    found = _find_key(states.node, name)
    if found is None:
        raise EditRefused(f"the state `{name}` does not exist")
    carrier = _sub_collection(text, found[0], found[1])
    carrier.relocate = lambda t: _state_carrier(t, name)
    return carrier


def _set_state(text: str, op: SetState) -> tuple[str, Any, list[str]]:
    new, inverse = _set_key(text, _state_carrier(text, op.name), op)
    return new, SetState(name=op.name, **inverse), []


# ───────────────────────────── transitions ─────────────────────────────


def _id_of(transition: Node) -> str | None:
    value = _value_of(transition, "id")
    return str(value.value) if isinstance(value, ScalarNode) else None


def _transitions(text: str) -> _Collection:
    return _collection(text, _compose(text), "transitions")


def _transition(text: str, ident: str) -> tuple[_Collection, int, Node]:
    transitions = _transitions(text)
    for i, element in enumerate(transitions.node.value):
        if _id_of(element) == ident:
            return transitions, i, element
    raise EditRefused(f"the transition `{ident}` does not exist")


def _add_transition(text: str, op: AddTransition) -> tuple[str, Any, list[str]]:
    transitions = _transitions(text)
    place = transitions.size if op.index is None else op.index
    if op.text is not None:
        edited_text = transitions.insert_raw(place, op.text)
    else:
        spec = dict(op.transition or {})
        if not spec.get("from") or not spec.get("to"):
            raise EditRefused("a transition requires `from` and `to`")
        ident = str(spec.pop("id", None) or f"t-{spec['from']}-{spec['to']}")
        if ident in {_id_of(e) for e in transitions.node.value}:
            raise EditRefused(f"the transition `{ident}` already exists")
        rendered = _render({"id": ident, **spec})
        edited_text = transitions.insert(place, rendered if transitions.is_flow else f"- {rendered}")
    added = _id_of(_transitions(edited_text).node.value[place])
    if added is None:
        raise EditRefused("an added transition must carry its `id`")
    return edited_text, RemoveTransition(id=added), []


def _remove_transition(text: str, op: RemoveTransition) -> tuple[str, Any, list[str]]:
    transitions, place, _ = _transition(text, op.id)
    without, removed = transitions.remove(place)
    return without, AddTransition(text=removed, index=place), []


def _transition_carrier(text: str, ident: str) -> _Collection:
    _, _, element = _transition(text, ident)
    carrier = _sequence_item(text, f"transition {ident}", element)
    carrier.relocate = lambda t: _transition_carrier(t, ident)
    return carrier


def _set_transition(text: str, op: SetTransition) -> tuple[str, Any, list[str]]:
    if op.field == "id":
        raise EditRefused("a transition's `id` cannot be changed: remove it, then add it again")
    new, inverse = _set_key(text, _transition_carrier(text, op.id), op)
    return new, SetTransition(id=op.id, **inverse), []


# ───────────────────────────── guarantees ─────────────────────────────


def _guarantee_name(node: Node) -> str | None:
    if isinstance(node, ScalarNode):
        return str(node.value)
    value = _value_of(node, "name")
    return str(value.value) if isinstance(value, ScalarNode) else None


def _add_gate(text: str, op: AddGate) -> tuple[str, Any, list[str]]:
    _, _, element = _transition(text, op.transition)
    found = _find_key(element, "gates")
    if found is None:
        if op.gate is None:
            raise EditRefused("`gate` missing")
        carrier = _sequence_item(text, f"transition {op.transition}", element)
        new, inverse = _set_key(
            text, carrier, SetTransition(id=op.transition, field="gates", value=[op.gate])
        )
        return new, SetTransition(id=op.transition, **inverse), []
    guarantees = _sub_collection(text, found[0], found[1])
    place = guarantees.size if op.index is None else op.index
    if op.text is not None:
        edited_text = guarantees.insert_raw(place, op.text)
    elif op.gate is not None:
        name = op.gate if isinstance(op.gate, str) else str(op.gate.get("name"))
        if name in {_guarantee_name(g) for g in guarantees.node.value}:
            raise EditRefused(f"the transition `{op.transition}` already has the guarantee `{name}`")
        rendered = _render(op.gate)
        edited_text = guarantees.insert(place, rendered if guarantees.is_flow else f"- {rendered}")
    else:
        raise EditRefused("`gate` missing")
    after = _value_of(_transition(edited_text, op.transition)[2], "gates")
    assert isinstance(after, SequenceNode)
    added_name = _guarantee_name(after.value[place]) or ""
    return edited_text, RemoveGate(transition=op.transition, name=added_name, index=place), []


def _remove_gate(text: str, op: RemoveGate) -> tuple[str, Any, list[str]]:
    _, _, element = _transition(text, op.transition)
    found = _find_key(element, "gates")
    if found is None or not isinstance(found[1], SequenceNode):
        raise EditRefused(f"the transition `{op.transition}` has no guarantees")
    guarantees = _sub_collection(text, found[0], found[1])
    ranks = [r for r, g in enumerate(found[1].value) if _guarantee_name(g) == op.name]
    if op.index is not None:
        ranks = [r for r in ranks if r == op.index]
    if not ranks:
        raise EditRefused(f"the transition `{op.transition}` does not have the guarantee `{op.name}`")
    without, removed = guarantees.remove(ranks[0])
    return without, AddGate(transition=op.transition, text=removed, index=ranks[0]), []


# ───────────────────────────── actors ─────────────────────────────


def _actor_references(root: MappingNode, name: str) -> list[ScalarNode]:
    candidates: list[Node | None] = []
    transitions = _value_of(root, "transitions")
    for transition in transitions.value if isinstance(transitions, SequenceNode) else []:
        candidates.append(_value_of(transition, "by"))
        candidates.append(_value_of(_value_of(transition, "train"), "approval"))
        agents = _value_of(_value_of(transition, "review"), "agents")
        candidates += list(agents.value) if isinstance(agents, SequenceNode) else []
    return [n for n in candidates if isinstance(n, ScalarNode) and n.value == name]


def _actors_of(text: str) -> tuple[MappingNode, _Collection]:
    root = _compose(text)
    return root, _collection(text, root, "actors")


def _add_actor(text: str, op: AddActor) -> tuple[str, Any, list[str]]:
    _, actors = _actors_of(text)
    if _find_key(actors.node, op.name) is not None:
        raise EditRefused(f"the actor `{op.name}` already exists")
    place = actors.size if op.index is None else op.index
    if op.text is not None:
        return actors.insert_raw(place, op.text), RemoveActor(name=op.name), []
    if not IDENTIFIER.match(op.name):
        raise EditRefused(f"`{op.name}` is not an actor name (lowercase letters, digits, `_`, `-`)")
    if not op.spec or "type" not in op.spec:
        raise EditRefused("an actor requires its `type`: agent, human or system")
    return actors.insert(place, f"{op.name}: {_render(op.spec)}"), RemoveActor(name=op.name), []


def _remove_actor(text: str, op: RemoveActor) -> tuple[str, Any, list[str]]:
    root, actors = _actors_of(text)
    if _find_key(actors.node, op.name) is None:
        raise EditRefused(f"the actor `{op.name}` does not exist")
    references = _actor_references(root, op.name)
    if references:
        line_numbers = sorted({r.start_mark.line + 1 for r in references})
        raise EditRefused(f"the actor `{op.name}` still carries transitions, line(s) {line_numbers}")
    place = _key_index(actors.node, op.name)
    without, removed = actors.remove(place)
    return without, AddActor(name=op.name, text=removed, index=place), []


def _actor_carrier(text: str, name: str) -> _Collection:
    _, actors = _actors_of(text)
    found = _find_key(actors.node, name)
    if found is None:
        raise EditRefused(f"the actor `{name}` does not exist")
    carrier = _sub_collection(text, found[0], found[1])
    carrier.relocate = lambda t: _actor_carrier(t, name)
    return carrier


def _set_actor(text: str, op: SetActor) -> tuple[str, Any, list[str]]:
    new, inverse = _set_key(text, _actor_carrier(text, op.name), op)
    return new, SetActor(name=op.name, **inverse), []


# ───────────────────────────── the workflow itself ─────────────────────────────

#: What `set_workflow` edits, and where: under `metadata`, or at the top of the document.
WORKFLOW_FIELDS = {
    "description": "metadata",
    "inputs": "metadata",
    "catalogue": "metadata",
    "initial": "root",
}


def _metadata_carrier(text: str) -> _Collection:
    carrier = _collection(text, _compose(text), "metadata")
    carrier.relocate = _metadata_carrier
    return carrier


def _root_carrier(text: str) -> _Collection:
    root = _compose(text)
    return _Collection(text, "the workflow", root, 0, relocate=_root_carrier)


def _set_workflow(text: str, op: SetWorkflow) -> tuple[str, Any, list[str]]:
    where = WORKFLOW_FIELDS.get(op.field)
    if where is None:
        raise EditRefused(
            f"`{op.field}` is not a field of the workflow this operation edits "
            f"({', '.join(sorted(WORKFLOW_FIELDS))}); the name is the route's, "
            "the version the publication's"
        )
    if where == "metadata":
        new, inverse = _set_key(text, _metadata_carrier(text), op)
    else:
        root = _root_carrier(text)
        # A new `initial:` goes right after `metadata`, where a reader looks for it.
        after = _key_index(root.node, "metadata") + 1 if _find_key(root.node, "metadata") else None
        new, inverse = _set_key(text, root, op, new_place=after)
    return new, SetWorkflow(**inverse), []


_APPLIERS: dict[str, Callable[[str, Any], tuple[str, Any, list[str]]]] = {
    "add_state": _add_state,
    "remove_state": _remove_state,
    "rename_state": _rename_state,
    "set_state": _set_state,
    "add_transition": _add_transition,
    "remove_transition": _remove_transition,
    "set_transition": _set_transition,
    "add_gate": _add_gate,
    "remove_gate": _remove_gate,
    "add_actor": _add_actor,
    "remove_actor": _remove_actor,
    "set_actor": _set_actor,
    "set_workflow": _set_workflow,
}


__all__ = [
    "OPERATIONS",
    "PREFIXES_WITH_EFFECT",
    "EditRefused",
    "Edition",
    "Operation",
    "edit",
]
