#!/usr/bin/env python3
"""Archive l'historique d'un workflow Temporal réel pour le rejouer en CI.

    uv run python tools/replay_record.py wi-staffing-RH-1b wi-panier-DEMO-1b
    uv run python tools/replay_record.py --address 127.0.0.1:7233 --all-interpreters

Les historiques vont dans `tests/replay/histories/<workflow_id>.json`, et
`tests/replay/test_replay.py` les rejoue contre le code courant : si une décision change,
le replay échoue AVANT que ça n'arrive à un ticket en cours. Le dossier était vide depuis
la création du dépôt — le test skippait, et la garantie centrale d'un produit bâti sur
Temporal n'existait que dans le README (état des lieux du 2026-09-24).

Ce qu'un historique contient : les entrées et sorties de chaque activité, donc les tickets,
les contextes, les résultats d'agent. N'archiver que des workflows de DÉMONSTRATION.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

RACINE = pathlib.Path(__file__).resolve().parents[1]
HISTORIES = RACINE / "tests" / "replay" / "histories"


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("workflow_ids", nargs="*", help="identifiants de workflows à archiver")
    parser.add_argument("--address", default="127.0.0.1:7233", help="serveur Temporal (port-forward)")
    parser.add_argument("--namespace", default="default")
    parser.add_argument(
        "--all-interpreters",
        action="store_true",
        help="archive tous les WorkflowInterpreter terminés (démonstration seulement)",
    )
    args = parser.parse_args()

    from temporalio.client import Client

    client = await Client.connect(args.address, namespace=args.namespace)
    ids = list(args.workflow_ids)
    if args.all_interpreters:
        async for wf in client.list_workflows(
            'WorkflowType="WorkflowInterpreter" AND ExecutionStatus!="Running"'
        ):
            ids.append(wf.id)
    if not ids:
        parser.error("aucun workflow : donner des identifiants ou --all-interpreters")

    HISTORIES.mkdir(parents=True, exist_ok=True)
    for workflow_id in dict.fromkeys(ids):
        handle = client.get_workflow_handle(workflow_id)
        history = await handle.fetch_history()
        cible = HISTORIES / f"{workflow_id}.json"
        # `to_json` rend une chaîne ; on la range indentée pour des diffs lisibles en PR.
        cible.write_text(json.dumps(json.loads(history.to_json()), indent=1, ensure_ascii=False) + "\n")
        print(f"{workflow_id}: {len(history.events)} événements → {cible.relative_to(RACINE)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
