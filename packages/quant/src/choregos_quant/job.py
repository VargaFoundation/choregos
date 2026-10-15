# SPDX-License-Identifier: Apache-2.0
"""Running as a Choregos job step (ADR 0056): paths from the workspace, results in `job.json`.

In a job, the runner gives the engine a workspace and nothing else:

- the campaign is the work item's fields, in `.choregos/fields.json`;
- a step's declared inputs are files in `.choregos/inputs/` (`bars.csv`, `strategies.json`);
- what the engine computes goes back in `.choregos/job.json` = `{summary, facts, outputs}`: the JSON
  documents as inline outputs, the CSV and HTML files in `.choregos/outputs/` under the output's
  name, where the runner stores and hashes them.

The engine writes its files in a scratch directory first and only then publishes them: a crashed
command leaves no half-written output behind, and `result.json` never lands in `outputs/`, where
the runner would store it as an artifact nobody declared.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from choregos_quant.canon import dumps, write_text
from choregos_quant.errors import InputError

#: What each command publishes: its JSON documents become inline outputs, its files are copied.
PUBLISHED: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "snapshot": (("snapshot.json",), ("bars.csv",)),
    "profile": (("universe_profile.json", "anonymisation.json"), ()),
    "backtest": (("metrics.json",), ("results.csv", "report.html")),
}


def workspace() -> Path:
    """The job's workspace, from `CHOREGOS_WORKSPACE`; an error outside a job."""
    root = os.environ.get("CHOREGOS_WORKSPACE")
    if not root:
        raise InputError("no --campaign given and CHOREGOS_WORKSPACE is not set: not running as a job")
    return Path(root)


def paths(command: str, root: Path) -> dict[str, Path]:
    """Where a job finds its campaign and inputs, and where it computes before publishing."""
    meta = root / ".choregos"
    return {
        "campaign": meta / "fields.json",
        "bars": meta / "inputs" / "bars.csv",
        "strategies": meta / "inputs" / "strategies.json",
        "previous": meta / "inputs",  # a re-run of the snapshot step reads what the last one stored
        "out": meta / "quant" / command,
    }


def publish(command: str, out: Path, root: Path) -> dict[str, Any]:
    """Writes `.choregos/job.json` from what `command` computed in `out`, and copies its files."""
    documents, files = PUBLISHED[command]
    result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    outputs: dict[str, Any] = {}
    for name in documents:
        outputs[Path(name).stem] = json.loads((out / name).read_text(encoding="utf-8"))
    published = root / ".choregos" / "outputs"
    published.mkdir(parents=True, exist_ok=True)
    for name in files:
        shutil.copyfile(out / name, published / name)
    facts = dict(result.get("facts") or {})
    doc = {
        "summary": f"choregos-quant {command}: " + ", ".join(f"{k}={facts[k]}" for k in sorted(facts))[:3500],
        "facts": facts,
        "outputs": outputs,
    }
    write_text(root / ".choregos" / "job.json", dumps(doc))
    return doc
