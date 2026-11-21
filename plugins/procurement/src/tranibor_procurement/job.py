# SPDX-License-Identifier: Apache-2.0
"""Running as a Tranibor job step (ADR 0056): the workspace in, `.choregos/job.json` out.

The runner gives the command a workspace: the work item's fields in `.choregos/fields.json`, the files
a step declares in `.choregos/inputs/`. What the command computes goes back in `.choregos/job.json` =
`{summary, facts, outputs}`, written whole or not at all.

The exit code is 0 whenever the contract is kept, even if no file could be read: the facts say what
could not, and the workflow's gate decides. It is non-zero for a broken contract only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Final

#: The most an inline output may weigh. The runner sets no bound of its own (`tranibor_runner.job`
#: keeps what `job.json` says); the value is what a workflow engine moves comfortably between steps.
MAX_INLINE_BYTES: Final = 2 * 1024 * 1024


class ContractError(Exception):
    """The step cannot honour its contract: the command exits 2."""


def workspace() -> Path:
    """The job's workspace: `TRANIBOR_WORKSPACE`, else the directory the runner started the job in."""
    root = os.environ.get("TRANIBOR_WORKSPACE")
    return Path(root) if root else Path.cwd()


def inputs_dir(root: Path) -> Path:
    path = root / ".choregos" / "inputs"
    if not path.is_dir():
        raise ContractError(
            f"no inputs directory at {path}: the step declares no input, or this is not a job"
        )
    return path


def read_fields(root: Path) -> dict[str, Any]:
    path = root / ".choregos" / "fields.json"
    if not path.is_file():
        return {}
    try:
        fields = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError(f"{path} is unreadable: {exc}") from exc
    if not isinstance(fields, dict):
        raise ContractError(f"{path} is not a JSON object")
    return fields


def publish(root: Path, *, summary: str, facts: dict[str, Any], outputs: dict[str, Any]) -> dict[str, Any]:
    """Writes `.choregos/job.json`; refuses an inline output past the bound, and writes nothing then."""
    doc = {"summary": summary, "facts": facts, "outputs": outputs}
    body = json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    for name, value in outputs.items():
        size = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
        if size > MAX_INLINE_BYTES:
            raise ContractError(
                f"the output `{name}` weighs {size} bytes, the most an inline output may weigh is "
                f"{MAX_INLINE_BYTES}: lower --max-chars-per-file or hand fewer files"
            )
    target = root / ".choregos" / "job.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    scratch = target.with_suffix(".json.tmp")
    scratch.write_text(body, encoding="utf-8")
    scratch.replace(target)
    return doc
