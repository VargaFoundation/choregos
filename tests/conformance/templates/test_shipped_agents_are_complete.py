"""Every internal agent the platform ships declares its five components (ADR 0127 §7, S60-04).

S60-05 will refuse a new version of an internal agent that leaves a component to its project; it can only
do so once nothing the platform ships does. Two sources ship internal agents: the templates (their own
`agents/*.yaml`) and the offline plugins of `plugins/offline/` (the API image's catalogue, ADR 0093).

The offline archives are the signed bytes of `VargaFoundation/choregos-agents`: they move only by
`make plugins-lock`, with the network. Until each is refreshed to the version that declares (the
`choregos-agents` PR of S60-04), it sits in `PENDING_REFRESH`. That list is a ratchet in both directions:
an archive that is in it must still be incomplete (so the entry cannot outlive the refresh), and an
archive that is not in it must be complete. Refreshing a plugin empties its line; the list ends at `{}`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from choregos_core.agent_catalogue import entries
from choregos_core.agent_completeness import missing_components

pytestmark = pytest.mark.conformance

TEMPLATES = Path(__file__).resolve().parents[3] / "templates"
AGENT_FILES = sorted(TEMPLATES.glob("*/agents/*.yaml"))
#: plugin -> the version of its archive in `plugins/offline/` that predates S60-04.
PENDING_REFRESH = {
    "authoring": "1.0.0",
    "netops": "1.1.0",
    "requests": "1.0.1",
    "software-delivery": "1.0.0",
}
INTERNAL = [e for e in entries() if e.kind == "internal"]


def test_the_templates_ship_agents() -> None:
    assert {p.parent.parent.name for p in AGENT_FILES} == {"investment-research", "joiners-leavers"}


@pytest.mark.parametrize("path", AGENT_FILES, ids=lambda p: f"{p.parent.parent.name}/{p.stem}")
def test_a_template_agent_declares_its_five_components(path: Path) -> None:
    document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert missing_components(document["spec"]) == []


@pytest.mark.parametrize("agent", INTERNAL, ids=lambda e: f"{e.plugin}@{e.plugin_version}/{e.slug}")
def test_an_offline_plugin_agent_declares_its_five_components(agent: Any) -> None:
    missing = missing_components(agent.document["spec"])
    if PENDING_REFRESH.get(agent.plugin) == agent.plugin_version:
        assert missing, f"{agent.plugin} {agent.plugin_version} is complete: drop it from PENDING_REFRESH"
    else:
        assert missing == [], f"{agent.slug} leaves {missing} to its project"


def test_the_pending_list_names_archives_that_are_still_shipped() -> None:
    shipped = {(e.plugin, e.plugin_version) for e in INTERNAL}
    assert set(PENDING_REFRESH.items()) <= shipped, set(PENDING_REFRESH.items()) - shipped
