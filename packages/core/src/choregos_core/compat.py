# SPDX-License-Identifier: Apache-2.0
"""Former names that keep working after a rename (ADR 0098 §3).

A renamed name that only gets imported keeps a typed alias in the module that defines it, and
nothing else:

    NewName = ...
    OldName = NewName  # lang: fr-ok alias:internal

A plain alias does not forward a patch: `monkeypatch.setattr(module, "OldName", fake)` rebinds
`OldName` and leaves `NewName`, which the module's own code reads, untouched. A module whose former
names tests patch keeps the typed alias, for mypy and for readers, and calls `alias_names` once,
at its end:

    alias_names(__name__, {"OldName": "NewName"})

From then on the module's class is a `ModuleType` subclass with one property per former name.
The property reads, writes and deletes the English name in the module `__dict__`, and a data
descriptor on the type wins over the instance dict, so `module.OldName`, `from module import
OldName`, `monkeypatch.setattr(module, "OldName", ...)` and its undo all reach `NewName`, and a
patch of `NewName` is seen through `OldName`. An external former name (ADR 0098 §3 (f)) passes
`removed_in=EXTERNAL_ALIAS_REMOVAL` and warns, with `DeprecationWarning`, each time it is read.
`unittest.mock.patch` of a former name works too, but it takes the value it restores from the
module `__dict__`, where the typed alias still holds the import-time object: inside a patch of
`NewName`, patch `OldName` with `monkeypatch`.

A renamed module leaves a shim at its former path that ends with
`replace_module(__name__, sys.modules["package.new_module"])`.

Two rules:

- never a module `__getattr__`: strict mypy gives `Any` to what it returns, so the former name
  loses its type (ADR 0098, alternatives discarded);
- never either helper in a module under `apps/orchestrator/src/choregos_orchestrator/workflows/`:
  the Temporal sandbox re-imports those modules on its own, so a workflow keeps its plain typed
  aliases.

Pure standard library, no I/O.
"""

from __future__ import annotations

import sys
import warnings
from collections.abc import Mapping
from types import ModuleType
from typing import cast

EXTERNAL_ALIAS_REMOVAL = "0.22.0"
"""The release that removes the external former names (`alias:external`, ADR 0098 §3 (f))."""

_MARKER = "__choregos_aliases__"


def _former_name(module: str, old: str, new: str, removed_in: str | None) -> property:
    """The property that stands for `old` on the class of `module`: every access goes to `new`."""

    def read(self: ModuleType) -> object:
        if removed_in is not None:
            warnings.warn(
                f"{module}.{old} is deprecated: use {new} (removed in {removed_in}, ADR 0098)",
                DeprecationWarning,
                stacklevel=2,
            )
        try:
            return self.__dict__[new]
        except KeyError:
            raise AttributeError(f"module {module!r} has no attribute {old!r}", name=old, obj=self) from None

    def write(self: ModuleType, value: object) -> None:
        self.__dict__[new] = value

    def delete(self: ModuleType) -> None:
        try:
            del self.__dict__[new]
        except KeyError:
            raise AttributeError(f"module {module!r} has no attribute {old!r}", name=old, obj=self) from None

    return property(read, write, delete, f"Former name of `{module}.{new}`.")


def alias_names(module: str, aliases: Mapping[str, str], *, removed_in: str | None = None) -> None:
    """Make every former name of `aliases` (old -> new) read, write and delete the English one.

    `module` is the dotted name of a module already in `sys.modules`; the module calls this once,
    at its end, with `__name__`. A second call on the same module extends the class the first one
    installed, so several stories can each add their former names. `removed_in` is set for an
    external former name only (`EXTERNAL_ALIAS_REMOVAL`): reading it then warns.

    Raises `ValueError`, before changing anything, when a former name equals its English name,
    when the English name is not defined in the module, when a former name is already aliased to
    another name, or when a name would be both a former name and an English one (a chain of
    aliases reads a stale value).
    """
    mod = sys.modules[module]
    installed: dict[str, str] | None = vars(type(mod)).get(_MARKER)
    known = dict(installed or {})
    for old, new in aliases.items():
        if old == new:
            raise ValueError(f"{module}.{old} cannot be an alias of itself")
        if new not in mod.__dict__:
            raise ValueError(f"{module}.{old} cannot be an alias of {new}: {module} defines no {new}")
        if known.get(old, new) != new:
            raise ValueError(f"{module}.{old} is already an alias of {known[old]}, not of {new}")
        known[old] = new
    chained = set(known) & set(known.values())
    if chained:
        raise ValueError(f"{module}: {', '.join(sorted(chained))} cannot be both a former name and a new one")
    if installed is None:
        installed = {}
        mod.__class__ = cast(
            "type[ModuleType]", type(f"_Aliased_{module}", (type(mod),), {_MARKER: installed})
        )
    aliased = type(mod)
    for old, new in aliases.items():
        setattr(aliased, old, _former_name(module, old, new, removed_in))
        installed[old] = new


def replace_module(former: str, current: ModuleType) -> None:
    """Make the former dotted name `former` of a renamed module give the `current` module.

    The shim left at the former path calls it at its end,
    `replace_module(__name__, sys.modules["package.new_module"])`. The import system returns what
    `sys.modules` holds once a module has run and binds that object on the parent package, so
    `import package.former`, `from package import former` and `importlib.import_module` all give
    the current module, and a patch on one is a patch on the other.
    """
    sys.modules[former] = current
