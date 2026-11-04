"""The repository's own configuration is in English (ADR 0046, S45-34): the Makefile, the root
configuration files, `docker/`, `infra/`, `demo/` and the examples read as English, and the demo's
tool catalogue uses the `tools:` key.

The prose scan is `tools/check_english.py`'s, loaded by its path (under `--import-mode=importlib` a
test module does not import another one). `make help` and `renovate.json` are read here because
the scan does not reach them: the first is the Makefile's rendered output, the second is JSON.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
from types import ModuleType
from typing import Any

import pytest
import yaml
from choregos_core.catalogue import load_catalogue

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The root files, then the directories, this story brings to zero French.
PREFIXES = (
    "Makefile",
    "pyproject.toml",
    "cliff.toml",
    "CODEOWNERS",
    "justfile",
    "compose.yaml",
    "renovate.json",
    "docker/",
    "infra/",
    "examples/",
    "integrations/",
    "demo/",
)


def _checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_english", ROOT / "tools" / "check_english.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass needs its module registered
    spec.loader.exec_module(module)
    return module


checker = _checker()


def _in_a_git_checkout() -> bool:
    try:
        subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"], cwd=ROOT, check=True, capture_output=True
        )
    except (subprocess.CalledProcessError, OSError):
        return False
    return True


@pytest.mark.skipif(not _in_a_git_checkout(), reason="not a git checkout: the tree scan reads `git ls-files`")
def test_root_config_docker_infra_demo_have_no_french() -> None:
    findings = checker.scan_tree(PREFIXES)
    assert findings == [], "French left:\n" + "\n".join(str(finding) for finding in findings)


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
def test_make_help_is_english() -> None:
    out = subprocess.run(
        ["make", "--no-print-directory", "-s", "help"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) > 20, "`make help` lists the targets"
    french = [
        f"{line.strip()}: {checker.french_reason(line)}" for line in lines if checker.french_reason(line)
    ]
    assert french == []


def _descriptions(node: Any) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "description" and isinstance(value, str):
                found.append(value)
            else:
                found += _descriptions(value)
    elif isinstance(node, list):
        for item in node:
            found += _descriptions(item)
    return found


def test_renovate_descriptions_are_english() -> None:
    config = json.loads((ROOT / "renovate.json").read_text(encoding="utf-8"))
    descriptions = _descriptions(config)
    assert descriptions, "renovate.json carries descriptions"
    french = [
        f"{text}: {checker.french_reason(text)}" for text in descriptions if checker.french_reason(text)
    ]
    assert french == []


def test_demo_catalogue_uses_tools() -> None:
    assert not (ROOT / "demo" / "outils").exists(), "the directory is `demo/tools`"
    path = ROOT / "demo" / "tools" / "catalogue.yaml"
    text = path.read_text(encoding="utf-8")
    document = yaml.safe_load(text)
    assert isinstance(document, dict)
    assert "outils" not in document, "the key is `tools:`; `outils:` is read only until 0.23.0"
    assert "tools" in document
    catalogue = load_catalogue(text)
    assert [tool.name for tool in catalogue.tools] == ["verifier_adresse"]
    assert "demo-outils" not in (ROOT / "demo" / "kustomization.yaml").read_text(encoding="utf-8")
