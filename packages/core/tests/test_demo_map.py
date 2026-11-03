"""The demo mode's map is the one the API renders for the `dev-complex` template (S22-02).

The console animates two demonstration journeys on `apps/web/src/mocks/dev-complex.json`. Written by
hand, this map would have diverged from the template at the first change: it is the exact imprint of
`to_graph` and `to_process`, and this test refuses to let it drift.
"""

from __future__ import annotations

import json
from pathlib import Path

from choregos_core import parse_workflow, to_graph, to_process

ROOT = Path(__file__).resolve().parents[3]


def test_the_demo_mode_map_is_the_templates() -> None:
    template = ROOT / "templates/github-software-delivery/workflows/dev-complex.yaml"
    workflow, _ = parse_workflow(template.read_text("utf-8"))
    expected = {
        "workflow_name": workflow.metadata.name,
        "workflow_version": workflow.metadata.version,
        "initial": workflow.initial_state,
        "graph": to_graph(workflow),
        "process": to_process(workflow),
    }
    demo = json.loads((ROOT / "apps/web/src/mocks/dev-complex.json").read_text("utf-8"))
    assert demo == json.loads(json.dumps(expected)), (
        "the demo mode map has diverged from the template: regenerate apps/web/src/mocks/dev-complex.json "
        "with to_graph and to_process (see this test)"
    )
