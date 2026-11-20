"""S65-04m: the 0.28 upgrade notes say that `container` became the default, and how to keep `process`."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENT = ROOT / "docs" / "deployment.md"
HEADING = "## Upgrading to 0.28"


def _section() -> str:
    text = DEPLOYMENT.read_text(encoding="utf-8")
    assert HEADING in text, f"docs/deployment.md has no {HEADING!r}"
    return text.split(HEADING, 1)[1].split("\n## ", 1)[0]


def test_the_028_notes_name_the_setting_its_new_default_and_the_way_back() -> None:
    section = _section()
    for name in (
        "global.runner.agentIsolation",
        "TRANIBOR_RUNNER_AGENT_ISOLATION",
        "`container`",
        "`process`",
        "agent_isolation_unavailable",
        "tekton",
        "aca",
        "1.29",
    ):
        assert name in section, f"the 0.28 upgrade notes do not name {name}"


def test_the_charts_default_is_the_one_the_notes_announce() -> None:
    values = (ROOT / "charts" / "choregos" / "values.yaml").read_text(encoding="utf-8")
    assert "    agentIsolation: container\n" in values
    helpers = (ROOT / "charts" / "choregos" / "templates" / "_helpers.tpl").read_text(encoding="utf-8")
    assert '.agentIsolation | default "container"' in helpers
