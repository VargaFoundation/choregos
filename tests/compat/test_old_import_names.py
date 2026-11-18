"""ADR 0176 R2: every `choregos_*` import name still resolves, to the very same module objects.

The enterprise edition (`from choregos_api import ...`), the plugins and any script written before
the rename import the old names for one deprecation window. Three things must hold, and each test
below turns red without the shim that carries it:

- every module of every package imports under both names, and the two names give ONE object, not
  two copies (a copy would split classes: an `isinstance` across the two names would be false);
- the alias is the package's own business: a module added to `tranibor_x` tomorrow is covered
  without anyone touching the shim (the meta-path finder), and a NEW package without a shim fails;
- one `DeprecationWarning` per old package and process, naming the new import.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: (distribution suffix, module suffix, the directory holding `src/`)
PACKAGES = [
    ("contracts", "contracts", "packages/contracts"),
    ("core", "core", "packages/core"),
    ("ontology", "ontology", "packages/ontology"),
    ("adapters", "adapters", "packages/adapters"),
    ("runner", "runner", "packages/runner"),
    ("quant", "quant", "packages/quant"),
    ("tools-mcp", "tools_mcp", "packages/tools-mcp"),
    ("playbooks", "playbooks", "packages/playbooks"),
    ("cli", "cli", "packages/cli"),
    ("agent-sdk", "agent_sdk", "packages/agent-sdk"),
    ("outpost", "outpost", "packages/outpost"),
    ("api", "api", "apps/api"),
    ("orchestrator", "orchestrator", "apps/orchestrator"),
]

#: Modules that do something when imported (they start a server, read argv) or that are not code.
NOT_IMPORTED = ("__main__", ".tests.", ".migrations.versions.", ".migrations.env")


def _modules(module: str, directory: str) -> list[str]:
    base = ROOT / directory / "src"
    found = []
    for file in sorted((base / f"tranibor_{module}").rglob("*.py")):
        parts = list(file.relative_to(base).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        name = ".".join(parts)
        if not any(skip in f".{name}." or name.endswith(skip.strip(".")) for skip in NOT_IMPORTED):
            found.append(name)
    return found


def _all_modules() -> list[tuple[str, str]]:
    return [
        (m, f"tranibor_{module}") for _, module, directory in PACKAGES for m in _modules(module, directory)
    ]


@pytest.mark.parametrize(("dist", "module", "directory"), PACKAGES)
def test_every_module_imports_under_both_names_and_is_the_same_object(
    dist: str, module: str, directory: str
) -> None:
    names = _modules(module, directory)
    assert names, f"no module found for tranibor_{module}: the test would prove nothing"
    for new in names:
        old = "choregos_" + new.removeprefix("tranibor_")
        try:
            new_module = importlib.import_module(new)
        except ImportError as exc:  # an optional dependency the suite does not install
            pytest.skip(f"{new}: {exc}")
        old_module = importlib.import_module(old)
        assert old_module is new_module, f"{old} is a copy of {new}, not the same module"
        assert sys.modules[old] is sys.modules[new]


def test_the_same_class_under_both_names() -> None:
    old = importlib.import_module("choregos_core.plugins.versions")
    new = importlib.import_module("tranibor_core.plugins.versions")
    assert old.core_version is new.core_version
    from choregos_contracts.enums import Size as OldStage  # type: ignore[attr-defined,unused-ignore]
    from tranibor_contracts.enums import Size as NewStage  # type: ignore[attr-defined,unused-ignore]

    assert OldStage is NewStage


def test_from_import_of_a_name_and_of_a_submodule_through_the_old_name() -> None:
    from choregos_core import plugins  # noqa: F401
    from choregos_core.plugins import versions

    assert versions is importlib.import_module("tranibor_core.plugins.versions")


def test_a_module_that_does_not_exist_still_fails_the_old_way() -> None:
    with pytest.raises(ModuleNotFoundError) as caught:
        importlib.import_module("choregos_core.no_such_module")
    assert caught.value.name == "choregos_core.no_such_module"


def test_a_module_added_to_an_aliased_package_is_covered_without_touching_the_shim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tranibor_contracts._compat import install_alias

    package = tmp_path / "tranibor_zz"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "later.py").write_text("VALUE = object()\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        # The shim is the package's three lines; `later.py` is written after nothing mentions it.
        install_alias("choregos_zz", "tranibor_zz")
        assert importlib.import_module("choregos_zz.later") is importlib.import_module("tranibor_zz.later")
    finally:
        for name in [n for n in sys.modules if n.split(".")[0] in ("choregos_zz", "tranibor_zz")]:
            del sys.modules[name]
        from tranibor_contracts import _compat

        _compat._FINDER.aliases.pop("choregos_zz", None)


def test_one_deprecation_warning_per_old_package_and_process() -> None:
    code = textwrap.dedent(
        """
        import warnings
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            import choregos_core
            import choregos_core.plugins.versions
            import choregos_core.dsl
            import choregos_contracts
        messages = [str(w.message) for w in seen if w.category is DeprecationWarning]
        print(len(messages))
        print("\\n".join(messages))
        """
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True, cwd=ROOT
    ).stdout.splitlines()
    assert out[0] == "2", out
    assert "tranibor_core" in out[1] and "tranibor_contracts" in out[2]


def test_every_package_has_its_shim_distribution() -> None:
    """A new package without its `compat/choregos-<x>` fails here, whatever its modules are."""
    workspace = tomllib.loads((ROOT / "pyproject.toml").read_text())
    sources = workspace["tool"]["uv"]["sources"]
    root_dependencies = set(workspace["project"]["dependencies"])
    discovered = {
        p.name.removeprefix("tranibor_")
        for src in [*(ROOT / "packages").glob("*/src"), *(ROOT / "apps").glob("*/src")]
        for p in src.glob("tranibor_*")
    }
    assert discovered == {module for _, module, _ in PACKAGES}, "a package is missing from this test's table"
    for dist, module, directory in PACKAGES:
        shim = ROOT / "compat" / f"choregos-{dist}"
        project = tomllib.loads((shim / "pyproject.toml").read_text())
        assert project["project"]["name"] == f"choregos-{dist}"
        assert f"tranibor-{dist}=={project['project']['version']}" in project["project"]["dependencies"]
        assert project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [f"src/choregos_{module}"]
        init = (shim / "src" / f"choregos_{module}" / "__init__.py").read_text()
        assert f'install_alias("choregos_{module}", "tranibor_{module}")' in init
        assert f"choregos-{dist}" in root_dependencies, "the dev environment would not install the shim"
        assert sources[f"choregos-{dist}"] == {"workspace": True}
        real = tomllib.loads((ROOT / directory / "pyproject.toml").read_text())
        assert real["project"]["name"] == f"tranibor-{dist}"
        assert real["project"]["version"] == project["project"]["version"], "one version everywhere"


def test_the_old_console_scripts_and_plugin_groups_are_unchanged() -> None:
    """R2 renames no command and no entry-point group: `choregos` stays beside `tranibor`."""
    cli = tomllib.loads((ROOT / "packages/cli/pyproject.toml").read_text())["project"]["scripts"]
    assert cli == {"tranibor": "tranibor_cli.main:main", "choregos": "tranibor_cli.main:main"}
    ontology = tomllib.loads((ROOT / "packages/ontology/pyproject.toml").read_text())["project"]
    assert set(ontology["entry-points"]) == {"choregos.plugins", "choregos.migrations"}
