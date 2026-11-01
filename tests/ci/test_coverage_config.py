"""The coverage configuration is measured, complete and enforced (S47-25)."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _pyproject() -> dict[str, object]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _fail_under() -> int:
    report = _pyproject()["tool"]["coverage"]["report"]  # type: ignore[index]
    return int(report["fail_under"])


def test_every_python_package_is_measured() -> None:
    source = set(_pyproject()["tool"]["coverage"]["run"]["source"])  # type: ignore[index]
    missing = sorted(
        f"packages/{src.parent.name}/src"
        for src in ROOT.glob("packages/*/src")
        if any(src.rglob("*.py")) and f"packages/{src.parent.name}/src" not in source
    )
    assert not missing, f"packages outside the coverage scope: {missing}"


def _comment_above_fail_under() -> str:
    lines = (ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith("fail_under"))
    block: list[str] = []
    for line in reversed(lines[:index]):
        if not line.startswith("#"):
            break
        block.append(line)
    return "\n".join(reversed(block))


def _commands_section() -> str:
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    match = re.search(r"^## Commands.*?(?=^## )", text, re.S | re.M)
    assert match, "AGENTS.md has no '## Commands' section"
    return match.group(0)


def test_the_threshold_comment_and_agents_md_state_the_threshold() -> None:
    expected = re.compile(rf"threshold[^.]*?(?<!\d){_fail_under()} ?%", re.I)
    assert expected.search(_comment_above_fail_under()), "the comment above fail_under differs"
    assert expected.search(_commands_section()), "AGENTS.md '## Commands' differs from fail_under"


def test_make_ci_enforces_coverage() -> None:
    match = re.search(r"^ci:\s*([^#\n]*)", (ROOT / "Makefile").read_text(encoding="utf-8"), re.M)
    assert match, "Makefile has no ci target"
    assert "test-cov" in match.group(1).split()


def test_the_web_suite_has_a_coverage_floor() -> None:
    config = (ROOT / "apps/web/vitest.config.ts").read_text(encoding="utf-8")
    scripts = json.loads((ROOT / "apps/web/package.json").read_text(encoding="utf-8"))["scripts"]
    assert "thresholds" in config
    assert "--coverage" in scripts["test"]
