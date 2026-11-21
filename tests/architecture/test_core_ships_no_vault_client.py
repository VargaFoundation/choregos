"""The core ships no Vault client (ADR 0103 §5): the Vault transit wrapper is an extension.

`plugins/vault-transit` is the extension (S80-19). Nothing the community distribution runs on its own,
the packages and the two apps, may depend on it, import it or import a Vault library: an operator who
wants Vault installs the extension, and an operator who does not carries no Vault code, no credential
path to it and no failure mode.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE_SOURCES = [*ROOT.glob("packages/*/src"), *ROOT.glob("apps/*/src")]
CORE_PROJECTS = [*ROOT.glob("packages/*/pyproject.toml"), *ROOT.glob("apps/*/pyproject.toml")]
FORBIDDEN_MODULES = ("hvac", "tranibor_vault_transit", "vault")
FORBIDDEN_DISTRIBUTIONS = ("hvac", "tranibor-vault-transit", "vault")


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
    return found


def test_no_core_source_imports_a_vault_client_or_the_extension() -> None:
    assert CORE_SOURCES, "the scan found nothing to scan"
    offenders = sorted(
        str(path.relative_to(ROOT))
        for source in CORE_SOURCES
        for path in source.rglob("*.py")
        if _imports(path) & set(FORBIDDEN_MODULES)
    )
    assert offenders == []


def test_no_core_distribution_depends_on_vault() -> None:
    assert CORE_PROJECTS
    offenders: list[str] = []
    for project in CORE_PROJECTS:
        data = tomllib.loads(project.read_text(encoding="utf-8"))["project"]
        requirements = list(data.get("dependencies", []))
        for extra in data.get("optional-dependencies", {}).values():
            requirements.extend(extra)
        for requirement in requirements:
            name = requirement.split(";")[0].strip().split("[")[0]
            for sign in "<>=!~ ":
                name = name.split(sign)[0]
            if name.lower() in FORBIDDEN_DISTRIBUTIONS:
                offenders.append(f"{project.relative_to(ROOT)}: {requirement}")
    assert offenders == []


def test_the_secret_store_imports_without_the_extension() -> None:
    code = (
        "import sys, tranibor_api.secret_store as s;"
        "s.get_secret_store();"
        "assert 'tranibor_vault_transit' not in sys.modules, 'the API loaded the Vault extension';"
        "assert 'hvac' not in sys.modules"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr


def test_the_extension_is_the_only_place_that_names_vault_transit() -> None:
    extension = ROOT / "plugins" / "vault-transit"
    assert (extension / "pyproject.toml").is_file()
    data = tomllib.loads((extension / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["entry-points"]["choregos.plugins"]["vault-transit"].startswith(
        "tranibor_vault_transit."
    )
