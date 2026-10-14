# SPDX-License-Identifier: Apache-2.0
"""Choregos playbooks: one prompt per role, rendered with Jinja2, versioned and evaluated.

Invariants shared by every role (docs/plan/02 §2.5): write `.choregos/result.json`,
never widen the scope without `request_scope_change`, report with `report_finding`,
stop and `ask_human` rather than guess.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

ROLES_DIR = Path(__file__).parent / "roles"


# Playbooks supplied by the DEPLOYMENT, in addition to the package's own: directories separated
# by `:` in `CHOREGOS_PLAYBOOKS_DIR`. This is what makes the engine usable outside software: a
# `sourcing` or `case_review` role has no business in this package, but the rest of the machine
# (states, transitions, budgets, guarantees, memory) does not change from one domain to another.
# Two forms are accepted, because a ConfigMap mounts a tree badly:
#   <dir>/<role>.md          (flat file, one ConfigMap key)
#   <dir>/<role>/prompt.md   (same shape as the package's roles)
# A deployment role wins over the package's: that is how `implement` is adapted to a house
# without rewriting the platform.
def extra_roles_dirs() -> list[Path]:
    raw = os.environ.get("CHOREGOS_PLAYBOOKS_DIR", "")
    return [Path(p) for p in raw.split(":") if p.strip()]


KNOWN_ROLES = (
    "triage",
    "refine",
    "plan",
    "implement",
    "verify",
    "review",
    "fix_ci",
    "address_review",
    "release_notes",
    "verify_prod",
)

__version__ = "0.1.0"


def _search_dirs() -> list[str]:
    # The deployment's directories FIRST: the first one found wins.
    return [str(d) for d in extra_roles_dirs()] + [str(ROLES_DIR), str(ROLES_DIR.parent)]


@lru_cache(maxsize=1)
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(_search_dirs()),
        undefined=StrictUndefined,
        autoescape=select_autoescape(enabled_extensions=(), default=False),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def playbook_path(role: str) -> Path:
    for directory in extra_roles_dirs():
        for candidate in (directory / f"{role}.md", directory / role / "prompt.md"):
            if candidate.exists():
                return candidate
    path = ROLES_DIR / role / "prompt.md"
    if not path.exists():
        extra = ", ".join(str(d) for d in extra_roles_dirs()) or "none"
        raise FileNotFoundError(
            f"unknown playbook: {role} (package roles: {', '.join(KNOWN_ROLES)}; "
            f"deployment directories: {extra})"
        )
    return path


def playbook_source(role: str) -> str:
    return playbook_path(role).read_text(encoding="utf-8")


def render_playbook(role: str, **variables: Any) -> str:
    """Render a role's prompt: the deployment's if it supplies one, the package's otherwise."""
    path = playbook_path(role)  # raises if the role is unknown
    template = _env().get_template(_template_name(path, role))
    defaults: dict[str, Any] = {
        "ticket": {},
        "spec": "",
        "plan_markdown": "",
        #: The inputs declared by the transition, by name (`{{ inputs.profiles }}`).
        "inputs": {},
        "allowed_paths": [],
        "context": None,
        "project": None,
        "output_contract": OUTPUT_CONTRACT,
        "invariants": INVARIANTS,
    }
    return template.render({**defaults, **variables})


def frame(instructions: str) -> str:
    """A registry agent's instructions, followed by the frame it cannot change (ADR 0033).

    An agent's author writes what to DO; how to answer the platform — the output contract, the
    invariants — is not theirs to decide.
    """
    return (
        f"{instructions.rstrip()}\n\n---\n\n"
        "## What the platform requires (this frame cannot be changed)\n\n"
        f"{INVARIANTS}\n\n{OUTPUT_CONTRACT}\n"
    )


def _template_name(path: Path, role: str) -> str:
    """The name the Jinja loader expects, relative to one of the search directories."""
    for directory in _search_dirs():
        try:
            return str(path.relative_to(directory))
        except ValueError:
            continue
    return f"{role}/prompt.md"


INVARIANTS = """- Write `.choregos/result.json`, conforming to the contract, before you finish.
- Write in English everything a person will read — the summary, the documents you produce, your
  questions, your findings — even when the work item, its fields or its names are in another language.
- Never widen the scope yourself: call `request_scope_change(paths, justification)`.
- A problem out of scope is reported with `report_finding(...)`; it is not fixed.
- Prefer `ask_human(question)` to an assumption that commits the product.
- Conventional commits (`fix(orders): …`), one commit per intent.
- Touch no OTHER file under `.choregos/**`: `result.json` is the only output there that
  belongs to you; the rest is the run's configuration.
- Check `search_memory(query)` before any architecture decision."""

OUTPUT_CONTRACT = """Write `.choregos/result.json` — exactly this shape (the runner validates it against
`choregos/StageResult/v1`; a malformed result is sent back to you for repair, and the
`validate_result` tool tells you BEFORE you finish whether yours passes):

```json
{
  "schema": "choregos/StageResult/v1",
  "status": "done | blocked | needs_human | failed",
  "summary": "one sentence that says what was done",
  "outputs": { "<output name declared by the transition>": "text (Markdown)" },
  "evidence": {
    "tests_passed": true, "tests_run": 0,
    "facts": { "<fact named by the playbook>": 3, "<another>": true }
  },
  "findings": [
    { "title": "…", "type": "bug | perf | security | tech-debt | docs | flaky-test | ux",
      "severity": "low | medium | high | critical", "evidence": "path:line or output" }
  ],
  "scope_changes_requested": [ { "paths": ["…"], "justification": "…" } ],
  "questions": [ { "text": "the question, in one sentence", "options": ["…"] } ]
}
```

`questions`, `findings` and `scope_changes_requested` are lists of OBJECTS, never of
strings. An empty list is `[]`."""

__all__ = [
    "INVARIANTS",
    "KNOWN_ROLES",
    "OUTPUT_CONTRACT",
    "extra_roles_dirs",
    "frame",
    "playbook_path",
    "playbook_source",
    "render_playbook",
]
