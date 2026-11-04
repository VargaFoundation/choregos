"""Can a package installed ALONGSIDE register itself without anyone touching the core?

That is the question that decides whether an enterprise edition, or a trade's in-house connector,
can live outside this tree. Until 2026-09-26 the answer was **no**: `register()` was public, but
nothing ever imported a third-party module, so `_register_builtins()` had to be patched.
`grep entry_points` over `packages/` and `apps/` returned nothing outside the tests.

These tests do not simulate discovery: they write a **real** `.dist-info` on disk, add it to
`sys.path`, and let `importlib.metadata` find it. A test that stubbed `entry_points()` would prove
that the stub works.

An entry-point package of the `choregos.plugins` group is an **extension** (ADR 0093 Decision 1).
"""

from __future__ import annotations

import pathlib
import sys
from collections.abc import Iterator
from typing import Any

import pytest

EXTENSION = '''
from choregos_adapters import register


class InHouseTracker:
    """A tracker that does not exist in Choregos' tree."""

    def __init__(self, config):
        self.config = config


def plug_in():
    register("tracker", "in-house")(lambda cfg: InHouseTracker(cfg))
'''

BROKEN_EXTENSION = """
def plug_in():
    raise RuntimeError("a dependency is missing")
"""


def _install(root: pathlib.Path, name: str, source: str) -> None:
    """Writes a module and the `.dist-info` that declares it, as `pip install` would."""
    (root / f"{name}.py").write_text(source, encoding="utf-8")
    info = root / f"{name}-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: 0.1.0\n", encoding="utf-8")
    (info / "entry_points.txt").write_text(f"[choregos.plugins]\n{name} = {name}:plug_in\n", encoding="utf-8")


@pytest.fixture
def temporary_path(tmp_path: pathlib.Path) -> Iterator[pathlib.Path]:
    from choregos_adapters import registry

    # The extensions register connector types in module-level tables: restore them, or
    # `tracker/in-house` outlives the test and `tests/web`'s icon fixture check fails in a full run.
    before = (dict(registry._REGISTRY), dict(registry._SPECS))
    sys.path.insert(0, str(tmp_path))
    try:
        yield tmp_path
    finally:
        registry._REGISTRY.clear()
        registry._REGISTRY.update(before[0])
        registry._SPECS.clear()
        registry._SPECS.update(before[1])
        sys.path.remove(str(tmp_path))
        for module in [m for m in sys.modules if m.startswith("extension_")]:
            del sys.modules[module]


def test_an_extension_outside_the_tree_registers_its_connector(
    temporary_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The core is not modified: the package declares itself, the platform loads it.

    An explicit `CHOREGOS_FAKES=0`: the API suite sets `1`, and `build()` then returns a fake
    whatever the type asked for. Without this zero, the test passes alone and turns red in a
    group; the trap already cost twice today.
    """
    from choregos_adapters import available, build, load_extensions

    monkeypatch.setenv("CHOREGOS_FAKES", "0")

    assert "in-house" not in available("tracker"), "the test would start from an already polluted state"
    _install(temporary_path, "extension_example", EXTENSION)

    loaded = load_extensions()
    assert "extension_example" in loaded

    adapter: Any = build("tracker", "in-house", {"url": "https://example.test"})
    assert type(adapter).__name__ == "InHouseTracker"
    assert adapter.config == {"url": "https://example.test"}
    assert "in-house" in available("tracker")


def test_a_declared_extension_that_does_not_load_stops_everything(temporary_path: pathlib.Path) -> None:
    """Silently absent, it would leave a platform that looks complete and is not.

    The same rule as `guard.yaml` and as the refusal of an unresolved setting: someone installed
    it so that it would serve. Better not to start, and to name the culprit.
    """
    from choregos_adapters import load_extensions

    _install(temporary_path, "extension_broken", BROKEN_EXTENSION)
    with pytest.raises(RuntimeError, match="extension_broken") as failure:
        load_extensions()
    assert "dependency" in str(failure.value), "the original cause must stay readable"


def test_without_an_installed_extension_loading_does_nothing() -> None:
    from choregos_adapters import load_extensions

    assert load_extensions("choregos.group.that.does.not.exist") == []
