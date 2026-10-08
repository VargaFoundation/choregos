"""La carte du mode démo est celle que l'API rend pour le gabarit `dev-complex` (S22-02).

La console anime deux parcours de démonstration sur `apps/web/src/mocks/dev-complex.json`. Écrite à la
main, cette carte aurait divergé du gabarit au premier changement : elle est l'empreinte exacte de
`to_graph` et `to_process`, et ce test refuse qu'elle s'en écarte.
"""

from __future__ import annotations

import json
from pathlib import Path

from choregos_core import parse_workflow, to_graph, to_process

RACINE = Path(__file__).resolve().parents[3]


def test_la_carte_du_mode_demo_est_celle_du_gabarit() -> None:
    gabarit = RACINE / "templates/github-software-delivery/workflows/dev-complex.yaml"
    workflow, _ = parse_workflow(gabarit.read_text("utf-8"))
    attendu = {
        "workflow_name": workflow.metadata.name,
        "workflow_version": workflow.metadata.version,
        "initial": workflow.initial_state,
        "graph": to_graph(workflow),
        "process": to_process(workflow),
    }
    demo = json.loads((RACINE / "apps/web/src/mocks/dev-complex.json").read_text("utf-8"))
    assert demo == json.loads(json.dumps(attendu)), (
        "la carte du mode démo a divergé du gabarit : régénérez apps/web/src/mocks/dev-complex.json "
        "avec to_graph et to_process (voir ce test)"
    )
