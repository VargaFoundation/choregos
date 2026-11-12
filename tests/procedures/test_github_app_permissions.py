# SPDX-License-Identifier: Apache-2.0
"""The GitHub procedures list the permissions the adapters need: one constant, compared (ADR 0067)."""

from __future__ import annotations

import re

import pytest
from choregos_adapters.github import APP_EVENTS, APP_PERMISSIONS, DEFAULT_PERMISSIONS, RUNNER_PERMISSIONS
from choregos_core import procedures as core

GITHUB_PROCEDURES = ("connector.tracker.github-issues", "connector.scm.github")
LINE = re.compile(r"^- `([a-z_]+)`: (read|write)$", re.MULTILINE)


def test_what_a_token_asks_for_stays_inside_what_the_app_is_granted() -> None:
    for permissions in (DEFAULT_PERMISSIONS, RUNNER_PERMISSIONS):
        for name, level in permissions.items():
            granted = APP_PERMISSIONS.get(name)
            assert granted == level or (granted == "write" and level == "read"), name


@pytest.mark.parametrize("proc_id", GITHUB_PROCEDURES)
def test_the_procedure_lists_exactly_the_apps_permissions(proc_id: str) -> None:
    permissions = next(s for s in core.load().procedures[proc_id].sections if s.key == "permissions")
    listed = dict(LINE.findall(permissions.markdown))
    assert listed == APP_PERMISSIONS


@pytest.mark.parametrize("proc_id", GITHUB_PROCEDURES)
def test_the_procedure_lists_the_events_the_platform_parses(proc_id: str) -> None:
    permissions = next(s for s in core.load().procedures[proc_id].sections if s.key == "permissions")
    line = next(
        (ln for ln in permissions.markdown.splitlines() if ln.startswith("Subscribe to these events:")), ""
    )
    assert line, "the procedure names no events"
    assert tuple(re.findall(r"`([a-z0-9_]+)`", line)) == APP_EVENTS


def test_the_deployment_guide_lists_what_the_manifest_asks_for() -> None:
    """S67-31 (ADR 0161): the page listed four permissions and five events, the adapters used seven and
    seven; an App made from the page lacked `statuses` and `organization_projects`. The manifest the
    platform builds takes the constants, and the guide's table is held to them here."""
    from pathlib import Path

    guide = (Path(__file__).resolve().parents[2] / "docs" / "deployment.md").read_text(encoding="utf-8")
    section = guide.split("## The GitHub App\n", 1)[1].split("\n## ", 1)[0]
    permissions = next(line for line in section.splitlines() if line.startswith("| `default_permissions` |"))
    listed = dict(re.findall(r"`([a-z_]+)` (read|write)", permissions))
    assert listed == APP_PERMISSIONS
    events = next(line for line in section.splitlines() if line.startswith("| `default_events` |"))
    named = events.split("(", 1)[0]
    assert tuple(re.findall(r"`([a-z0-9_]+)`", named))[1:] == APP_EVENTS
