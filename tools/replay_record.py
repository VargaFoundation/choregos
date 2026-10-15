#!/usr/bin/env python3
"""Archives the history of a real Temporal workflow to replay it in CI.

    uv run python tools/replay_record.py wi-staffing-RH-1b wi-panier-DEMO-1b
    uv run python tools/replay_record.py --address 127.0.0.1:7233 --all-interpreters

Histories go to `tests/replay/histories/<workflow_id>.json`, and
`tests/replay/test_replay.py` replays them against the current code: if a decision changes,
the replay fails BEFORE it happens to a work item in flight. The folder had been empty since
the repository was created — the test skipped, and the central guarantee of a product built on
Temporal existed only in the README (state of play of 2026-09-24).

What a history contains: the inputs and outputs of every activity, so the work items, the
contexts, the agents' results. Archive DEMONSTRATION workflows only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
HISTORIES = ROOT / "tests" / "replay" / "histories"


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("workflow_ids", nargs="*", help="identifiers of the workflows to archive")
    parser.add_argument("--address", default="127.0.0.1:7233", help="Temporal server (port-forward)")
    parser.add_argument("--namespace", default="default")
    parser.add_argument(
        "--all-interpreters",
        action="store_true",
        help="archives every finished WorkflowInterpreter (demonstration only)",
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
        parser.error("no workflow: give identifiers or --all-interpreters")

    HISTORIES.mkdir(parents=True, exist_ok=True)
    for workflow_id in dict.fromkeys(ids):
        handle = client.get_workflow_handle(workflow_id)
        history = await handle.fetch_history()
        target = HISTORIES / f"{workflow_id}.json"
        # `to_json` returns a string; it is stored indented for readable diffs in a PR.
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1, ensure_ascii=False) + "\n")
        print(f"{workflow_id}: {len(history.events)} events → {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
