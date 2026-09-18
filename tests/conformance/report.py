"""Rapport de conformité : `python -m tests.conformance.report --output conformance.json`.

Le rapport sert à deux choses : publier l'état des backends dans le front (`platform/backends`)
et **désactiver automatiquement** un backend qui ne respecte plus le contrat.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from choregos_runner.backends import backend_version, known_backends


async def build_report(backends: list[str]) -> dict[str, Any]:
    from choregos_contracts import (
        AgentRef,
        Budget,
        Callbacks,
        LaunchSpec,
        ModelRef,
        Permissions,
        PlaybookRef,
        ProjectRef,
        RepoRef,
        StageInput,
        TransitionRef,
        WorkItemRef,
    )

    from .backends.test_acp_conformance import FAKE_AGENT, run_suite

    entries: list[dict[str, Any]] = []
    for name in backends:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            stage_input = StageInput(
                run_id="conformance",
                attempt=1,
                project=ProjectRef(slug="conformance", org="varga"),
                work_item=WorkItemRef(key="varga/conformance#1", title="créer hello.py"),
                transition=TransitionRef(id="t", role="implement", **{"from": "ready"}, to="in_progress"),
                repo=RepoRef(url=str(workspace), base_branch="main", work_branch="choregos/1"),
                agent=AgentRef(backend=name, launch=LaunchSpec(command=[sys.executable, str(FAKE_AGENT)])),
                model=ModelRef(
                    litellm_model="platform/standard", base_url="http://localhost:4000", api_format="openai"
                ),
                gateway_key="sk-conformance",
                budget=Budget(usd=1, max_turns=3, max_minutes=2),
                allowed_paths=["hello.py", "test_hello.py"],
                playbook=PlaybookRef(ref="implement@conformance", prompt="crée hello.py"),
                permissions=Permissions(write_paths=["hello.py", "test_hello.py"]),
                callbacks=Callbacks(api_url="http://localhost:0/internal", run_token="tok"),
            )
            result = await run_suite(name, stage_input, workspace)
        entries.append({**result.to_dict(), "version": backend_version(name)})
    return {
        "backends": entries,
        "disabled": [entry["backend"] for entry in entries if entry["failed"]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Rapport de conformité des backends ACP")
    parser.add_argument("--output", type=Path, default=Path("conformance.json"))
    parser.add_argument("--backends", default=",".join(known_backends()))
    args = parser.parse_args()

    report = asyncio.run(build_report([b.strip() for b in args.backends.split(",") if b.strip()]))
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for entry in report["backends"]:
        status = "✓" if not entry["failed"] else "✗"
        print(
            f"{status} {entry['backend']:<14} {len(entry['passed'])}/{entry['total']} {entry['failed'] or ''}"
        )
    if report["disabled"]:
        print(f"\nbackends à désactiver : {', '.join(report['disabled'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
