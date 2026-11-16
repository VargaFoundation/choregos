"""The Choregos Claude Code plugin: installable from the repository, secret-free, at the repository's version.

`/plugin marketplace add <owner>/choregos` (the publishing identity's owner) reads
`.claude-plugin/marketplace.json` at the root, then `/plugin install choregos@choregos` installs
`integrations/claude-code`: its MCP gate (ADR 0030) and its skill. The user's token never lives in
the repository: it is a sensitive value of their configuration, kept by Claude Code in their
machine's vault.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "integrations" / "claude-code"


def _json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_marketplace_points_at_the_plugin() -> None:
    marketplace = _json(ROOT / ".claude-plugin" / "marketplace.json")
    assert marketplace["name"] == "choregos"
    (entry,) = marketplace["plugins"]
    assert entry["name"] == "choregos"
    assert (ROOT / entry["source"]).resolve() == PLUGIN.resolve()


def test_the_plugin_has_the_version_of_the_repository() -> None:
    version = re.search(r'^version = "([^"]+)"', (ROOT / "packages/core/pyproject.toml").read_text(), re.M)
    assert version
    assert _json(PLUGIN / ".claude-plugin" / "plugin.json")["version"] == version.group(1)


def test_the_token_is_a_sensitive_value_and_the_mcp_references_only_declared_values() -> None:
    manifest = _json(PLUGIN / ".claude-plugin" / "plugin.json")
    options = manifest["userConfig"]
    assert options["token"]["sensitive"] is True
    mcp = (PLUGIN / ".mcp.json").read_text(encoding="utf-8")
    references = set(re.findall(r"\$\{user_config\.([A-Za-z0-9_]+)\}", mcp))
    assert references == {"mcp_url", "token"}
    assert references <= set(options)


def test_no_literal_secret() -> None:
    for file in [*PLUGIN.rglob("*"), ROOT / ".claude-plugin" / "marketplace.json"]:
        if file.is_file():
            assert "chg_" not in file.read_text(encoding="utf-8"), file


def test_the_skill_has_a_name_and_a_description() -> None:
    text = (PLUGIN / "skills" / "choregos" / "SKILL.md").read_text(encoding="utf-8")
    header = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert header
    fields = dict(line.split(": ", 1) for line in header.group(1).splitlines())
    assert fields["name"] == "choregos"
    assert len(fields["description"]) > 50
    assert "decision_url" in text, "the skill states the rule: never a decision through MCP"


@pytest.mark.skipif(shutil.which("claude") is None, reason="Claude Code is absent")
def test_claude_code_validates_the_plugin() -> None:
    for target in (ROOT, PLUGIN):
        command = ["claude", "plugin", "validate", "--strict", str(target)]
        rendu = subprocess.run(command, capture_output=True, text=True, check=False)
        assert rendu.returncode == 0, rendu.stdout + rendu.stderr
