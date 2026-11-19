# SPDX-License-Identifier: Apache-2.0
"""The former `choregos_*` import names, kept as aliases of `tranibor_*` (ADR 0176, R2).

A shim package `choregos_<x>` (the `compat/choregos-<x>` distributions) calls `install_alias` from its
`__init__`. The alias is the real package, not a copy: `sys.modules["choregos_api"]` is the module
object `tranibor_api`, and a meta-path finder, put in front of the path-based one, answers every
`choregos_api.<submodule>` with the module object `tranibor_api.<submodule>`. So `import
choregos_api.rbac`, `from choregos_core.dsl import validate` and `isinstance` checks, pickles and
pydantic models all see one class, not two. A process that imports an old name gets one
`DeprecationWarning` for that package, naming the new import.

`python -m choregos_api.migrate` runs `tranibor_api.migrate` as `__main__`: the loader gives runpy the
real module's code (#1899).

The aliases are for the runtime only. A shim ships no `py.typed` and no stubs, so a type checker does
not see through it (`mypy` reports `import-untyped` for every `choregos_*` import): code that is type
checked imports `tranibor_*` (ADR 0176, amendment of #1899).

This module lives in the lowest package (every other one depends on `tranibor-contracts`) so that
each shim stays three lines long and carries no logic of its own.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
import types
import warnings
from collections.abc import Sequence

__all__ = ["install_alias"]


class _AliasLoader(importlib.abc.InspectLoader):
    """Hands the import system the module `<new>.<rest>`, imported under its own name.

    `import <old>.<rest>` gets the module object of `<new>.<rest>` (`create_module`), and nothing is
    executed twice. `python -m <old>.<rest>` (runpy) does not import the module: it asks the loader
    for the code and runs it as `__main__`, so `get_code`, `get_source`, `is_package` and
    `get_filename` answer for the real module, through the real module's own loader (#1899).
    """

    def __init__(self, real: importlib.machinery.ModuleSpec) -> None:
        self._real = real
        self._real_spec: importlib.machinery.ModuleSpec | None = None

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> types.ModuleType:
        module = importlib.import_module(self._real.name)
        self._real_spec = module.__spec__
        return module

    def exec_module(self, module: types.ModuleType) -> None:
        # `module_from_spec` stamped the alias spec on the real module: it keeps its own, so that
        # `tranibor_x.y.__spec__.name` stays `tranibor_x.y` after someone imported `choregos_x.y`.
        if self._real_spec is not None:
            module.__spec__ = self._real_spec

    def _delegate(self) -> importlib.abc.InspectLoader:
        loader = self._real.loader
        if not isinstance(loader, importlib.abc.InspectLoader):
            raise ImportError(f"{self._real.name} has no loader that gives its code", name=self._real.name)
        return loader

    def get_code(self, fullname: str) -> types.CodeType | None:
        return self._delegate().get_code(self._real.name)

    def get_source(self, fullname: str) -> str | None:
        return self._delegate().get_source(self._real.name)

    def is_package(self, fullname: str) -> bool:
        return self._real.submodule_search_locations is not None

    def get_filename(self, fullname: str) -> str:
        if self._real.origin is None:
            raise ImportError(f"{self._real.name} has no file", name=self._real.name)
        return self._real.origin


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
            # Found, not imported: `python -m` runs the code as `__main__` without importing the
            # module first, and an import here would execute it twice.
            try:
                found = importlib.util.find_spec(real)
            except ModuleNotFoundError as exc:
                if exc.name == real or (exc.name and real.startswith(exc.name + ".")):
                    return None  # a parent of it is a module, not a package
                raise
            if found is None:
                return None  # there is no such submodule under the new name either
            spec = importlib.machinery.ModuleSpec(
                fullname,
                _AliasLoader(found),
                origin=found.origin,
                is_package=found.submodule_search_locations is not None,
            )
            spec.has_location = found.has_location
            return spec
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
