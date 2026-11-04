"""The upgrade section of docs/deployment.md names every operator-visible setting of 0.21."""

from __future__ import annotations

from pathlib import Path

DEPLOYMENT = Path(__file__).resolve().parents[2] / "docs" / "deployment.md"
HEADING = "## Upgrading to 0.21"

NAMES = (
    "CHOREGOS_SESSION_SECRET",
    "CHOREGOS_SESSION_SECRET_PREVIOUS",
    "choregos-api.trustedProxyCidrs",
    "choregos-api.corsOrigins",
    "choregos-api.oidcEndpoints",
    "read_only",
    "allowed_backends",
    "global.mcpGateway",
    "CHOREGOS_MCP_GATEWAY_INTERNAL_HOSTS",
    "CHOREGOS_RUNNER_CONFINEMENT",
    "global.plugins",
)


def _section() -> str:
    text = DEPLOYMENT.read_text(encoding="utf-8")
    assert HEADING in text, f"docs/deployment.md has no {HEADING!r}"
    body = text.split(HEADING, 1)[1]
    return body.split("\n## ", 1)[0]


def test_the_upgrade_section_names_every_breaking_setting() -> None:
    section = _section()
    missing = [name for name in NAMES if name not in section]
    assert not missing, f"the upgrade section does not name: {missing}"
