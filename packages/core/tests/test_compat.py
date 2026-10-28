"""Former names that keep working after a rename (S45-41, ADR 0098 §3 (c)): `choregos_core.compat`.

A former name must read the English object, follow a monkeypatch in both directions, survive
`from module import former`, compose across several calls, and warn only when it is external.
"""

from __future__ import annotations

import importlib
import sys
import textwrap
import types
import warnings
from collections.abc import Iterator
from pathlib import Path

import pytest
from choregos_core.compat import EXTERNAL_ALIAS_REMOVAL, alias_names, replace_module

#: A module as a renaming story leaves it: the English names, the module's own code reading them,
#: and the typed former names kept in the source.
SAMPLE = """
def new():
    return "english"

def current():
    return "english too"

def use():
    return new(), current()

old = new
former = current
"""


def patched() -> str:
    return "patched"


def synthetic(monkeypatch: pytest.MonkeyPatch, name: str, source: str = SAMPLE) -> types.ModuleType:
    """A module built from `source`, registered in `sys.modules` for the duration of the test."""
    module = types.ModuleType(name)
    exec(source, module.__dict__)
    monkeypatch.setitem(sys.modules, name, module)
    return module


@pytest.fixture
def mod(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> types.ModuleType:
    return synthetic(monkeypatch, f"choregos_compat_{request.node.name}")


@pytest.fixture
def package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> Iterator[str]:
    """The name of a fresh importable package under `tmp_path`; its modules leave `sys.modules` after."""
    name = f"choregos_compat_{request.node.name}"
    (tmp_path / name).mkdir()
    (tmp_path / name / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    yield name
    for key in [key for key in sys.modules if key == name or key.startswith(f"{name}.")]:
        del sys.modules[key]


def write_module(root: Path, package: str, module: str, source: str) -> None:
    (root / package / f"{module}.py").write_text(textwrap.dedent(source))


def test_former_name_reads_the_english_object(monkeypatch: pytest.MonkeyPatch) -> None:
    # No `old = new` line here: the former name is served by the alias alone.
    mod = synthetic(monkeypatch, "choregos_compat_reads", "def new():\n    return 'english'\n")
    alias_names(mod.__name__, {"old": "new"})
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert mod.old is mod.new
        assert mod.old is mod.__dict__["new"]
        assert "old" not in mod.__dict__


def test_external_alias_warns_with_its_window(mod: types.ModuleType) -> None:
    alias_names(mod.__name__, {"old": "new"}, removed_in=EXTERNAL_ALIAS_REMOVAL)
    with pytest.warns(DeprecationWarning, match=r"use new.*0\.22\.0") as record:
        value = mod.old
    assert value is mod.new
    assert len(record) == 1
    assert record[0].filename == __file__, "the warning points at the line that reads the former name"
    assert f"{mod.__name__}.old is deprecated" in str(record[0].message)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert mod.new() == "english"  # the English name never warns


def test_patching_the_former_name_patches_the_english_one(mod: types.ModuleType) -> None:
    alias_names(mod.__name__, {"old": "new"})
    original = mod.new
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(mod, "old", patched)
        assert mod.new is patched
        assert mod.old is patched
        assert mod.use() == ("patched", "english too"), "the module's own code sees the patch"
    assert mod.new is original
    assert mod.old is original
    assert mod.use() == ("english", "english too")


def test_patching_the_english_name_is_seen_through_the_former_one(mod: types.ModuleType) -> None:
    alias_names(mod.__name__, {"old": "new"})
    original = mod.new
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(mod, "new", patched)
        assert mod.old is patched
    assert mod.old is original
    assert mod.new is original


def test_from_import_of_a_former_name_gets_the_current_object(
    tmp_path: Path, package: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_module(
        tmp_path,
        package,
        "sample",
        """
        from choregos_core.compat import alias_names


        def new() -> str:
            return "english"


        old = new

        alias_names(__name__, {"old": "new"})
        """,
    )
    sample = importlib.import_module(f"{package}.sample")
    monkeypatch.setattr(sample, "new", patched)
    namespace: dict[str, object] = {}
    exec(f"from {package}.sample import old", namespace)
    assert namespace["old"] is patched


def test_two_calls_compose(mod: types.ModuleType) -> None:
    alias_names(mod.__name__, {"old": "new"})
    aliased = type(mod)
    alias_names(mod.__name__, {"former": "current"})
    assert type(mod) is aliased
    assert aliased is not types.ModuleType
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(mod, "old", patched)
        patch.setattr(mod, "former", patched)
        assert mod.use() == ("patched", "patched")
    assert mod.use() == ("english", "english too")
    alias_names(mod.__name__, {"old": "new"})  # the same pair again, as a reload does: accepted
    assert type(mod) is aliased


def test_a_module_that_aliases_its_names_reloads(tmp_path: Path, package: str) -> None:
    write_module(
        tmp_path,
        package,
        "sample",
        """
        from choregos_core.compat import alias_names

        new = "english"
        old = new

        alias_names(__name__, {"old": "new"})
        """,
    )
    sample = importlib.import_module(f"{package}.sample")
    aliased = type(sample)
    assert aliased is not types.ModuleType
    assert importlib.reload(sample) is sample, "the second call, with the same pair, is accepted"
    assert type(sample) is aliased
    assert sample.old == "english"


def test_alias_to_a_missing_name_is_refused(mod: types.ModuleType) -> None:
    with pytest.raises(ValueError, match="defines no missing"):
        alias_names(mod.__name__, {"old": "missing"})
    assert type(mod) is types.ModuleType, "a refused call changes nothing"


def test_a_self_alias_a_second_target_and_a_chain_are_refused(mod: types.ModuleType) -> None:
    with pytest.raises(ValueError, match="alias of itself"):
        alias_names(mod.__name__, {"new": "new"})
    alias_names(mod.__name__, {"old": "new"})
    with pytest.raises(ValueError, match="already an alias of new"):
        alias_names(mod.__name__, {"old": "current"})
    with pytest.raises(ValueError, match="both a former name and a new one"):
        alias_names(mod.__name__, {"former": "old"})
    with pytest.raises(ValueError, match="both a former name and a new one"):
        alias_names(mod.__name__, {"new": "current"})
    assert mod.old is mod.new
    assert mod.former is mod.current, "the refused pairs installed nothing"


def test_replace_module_makes_the_former_import_the_new_module(tmp_path: Path, package: str) -> None:
    write_module(tmp_path, package, "current", "VALUE = object()\n")
    write_module(
        tmp_path,
        package,
        "former",
        f"""
        import sys

        import {package}.current
        from choregos_core.compat import replace_module

        replace_module(__name__, sys.modules["{package}.current"])
        """,
    )
    current = importlib.import_module(f"{package}.current")
    assert importlib.import_module(f"{package}.former") is current
    namespace: dict[str, object] = {}
    exec(f"from {package} import former", namespace)
    assert namespace["former"] is current
    assert sys.modules[f"{package}.former"] is current
    replace_module(f"{package}.other", current)
    assert importlib.import_module(f"{package}.other") is current


def test_unknown_name_still_raises_attribute_error(mod: types.ModuleType) -> None:
    alias_names(mod.__name__, {"old": "new"})
    with pytest.raises(AttributeError, match="has no attribute 'unknown'"):
        _ = mod.unknown
    with pytest.MonkeyPatch.context() as patch:
        patch.delattr(mod, "new")
        with pytest.raises(AttributeError, match="has no attribute 'old'"):
            _ = mod.old
        assert not hasattr(mod, "old")
        assert getattr(mod, "old", None) is None
    assert mod.old is mod.new
