# SPDX-License-Identifier: Apache-2.0
"""The former `choregos_*` import names, kept as aliases of `tranibor_*` (ADR 0176, R2).

A shim package `choregos_<x>` (the `compat/choregos-<x>` distributions) calls `install_alias` from its
`__init__`. The alias is the real package, not a copy: `sys.modules["choregos_api"]` is the module
object `tranibor_api`, and a meta-path finder, put in front of the path-based one, answers every
`choregos_api.<submodule>` with the module object `tranibor_api.<submodule>`. So `import
choregos_api.rbac`, `from choregos_core.dsl import validate` and `isinstance` checks, pickles and
pydantic models all see one class, not two. A process that imports an old name gets one
`DeprecationWarning` for that package, naming the new import.

This module lives in the lowest package (every other one depends on `tranibor-contracts`) so that
each shim stays three lines long and carries no logic of its own.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import sys
import types
import warnings
from collections.abc import Sequence

__all__ = ["install_alias"]


class _AliasLoader(importlib.abc.Loader):
    """Hands the import system the module that already exists, and executes nothing."""

    def __init__(self, module: types.ModuleType) -> None:
        self._module = module

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> types.ModuleType:
        return self._module

    def exec_module(self, module: types.ModuleType) -> None:
        return None


class _AliasFinder(importlib.abc.MetaPathFinder):
    """Resolves `<old>.<rest>` to the module `<new>.<rest>`, for every alias installed."""

    def __init__(self) -> None:
        self.aliases: dict[str, str] = {}

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: types.ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        for old, new in self.aliases.items():
            if not fullname.startswith(old + "."):
                continue
            real = new + fullname[len(old) :]
            try:
                module = importlib.import_module(real)
            except ModuleNotFoundError as exc:
                if exc.name == real or (exc.name and real.startswith(exc.name + ".")):
                    return None  # there is no such submodule under the new name either
                raise
            return importlib.machinery.ModuleSpec(
                fullname,
                _AliasLoader(module),
                origin=getattr(module, "__file__", None),
                is_package=hasattr(module, "__path__"),
            )
        return None


_FINDER = _AliasFinder()
_WARNED: set[str] = set()


def install_alias(old: str, new: str) -> None:
    """Make the package `old` an alias of the package `new`, and warn once for `old`.

    Called from the shim's `__init__`, while `old` is being imported: replacing its `sys.modules`
    entry there is what makes `import old` return the module `new`.
    """
    module = importlib.import_module(new)
    _FINDER.aliases[old] = new
    if _FINDER not in sys.meta_path:
        sys.meta_path.insert(0, _FINDER)
    sys.modules[old] = module
    if old not in _WARNED:
        _WARNED.add(old)
        warnings.warn(
            f"`import {old}` is deprecated: the package is now `{new}` (ADR 0176); "
            f"the old name stays an alias for one minor cycle",
            DeprecationWarning,
            stacklevel=3,
            skip_file_prefixes=("<frozen importlib",),
        )
