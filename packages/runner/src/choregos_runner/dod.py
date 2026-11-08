# SPDX-License-Identifier: Apache-2.0
"""The "definition of done" loop: tests, lint, typing, and a bounded re-prompt if it is red.

This is the mechanism that turns "the agent says it is finished" into "the repository's commands
pass". The number of iterations comes from the project's policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from choregos_contracts import Evidence, StageInput

from .workspace import CommandResult, run_command

MAX_OUTPUT_CHARS = 4000

LANGUAGE_DEFAULTS: dict[str, dict[str, str]] = {
    "python": {"test": "make test", "lint": "make lint", "typecheck": "make typecheck"},
    "node": {
        "test": "npm test --silent",
        "lint": "npm run lint --silent",
        "typecheck": "npm run typecheck --silent",
    },
    "go": {"test": "go test ./...", "lint": "golangci-lint run", "typecheck": "go vet ./..."},
    "java": {"test": "./gradlew test", "lint": "./gradlew check", "typecheck": ""},
    "dotnet": {"test": "dotnet test", "lint": "dotnet format --verify-no-changes", "typecheck": ""},
}


@dataclass(slots=True)
class CheckOutcome:
    name: str
    command: str
    ok: bool
    skipped: bool = False
    output: str = ""

    @property
    def status(self) -> str:
        if self.skipped:
            return "skipped"
        return "ok" if self.ok else "failed"


@dataclass
class DodReport:
    """Result of one complete verification pass."""

    checks: list[CheckOutcome] = field(default_factory=list)
    iterations: int = 0

    @property
    def ok(self) -> bool:
        return all(check.ok or check.skipped for check in self.checks)

    def failures(self) -> list[CheckOutcome]:
        return [check for check in self.checks if not check.ok and not check.skipped]

    def get(self, name: str) -> CheckOutcome | None:
        return next((check for check in self.checks if check.name == name), None)

    #: {name: true/false} for the facts MEASURED by a command (the project's `dod.facts`).
    facts: dict[str, bool] = field(default_factory=dict)

    def evidence(self) -> Evidence:
        tests = self.get("tests")
        lint = self.get("lint")
        typecheck = self.get("typecheck")
        counts = _parse_test_counts(tests.output if tests else "")
        return Evidence(
            tests_passed=(None if tests is None or tests.skipped else tests.ok),
            tests_run=counts.get("run"),
            tests_failed=counts.get("failed"),
            lint=(lint.status if lint else None),
            typecheck=(typecheck.status if typecheck else None),
            # `None` and not `{}` when nothing is measured: `merge_evidence` only overwrites the
            # non-`None`, so an empty dictionary would erase the agent's facts.
            facts=(dict(self.facts) or None),
        )

    def prompt_for_repair(self) -> str:
        """Message sent to the agent when the verifications fail."""
        blocks = [
            "The repository's checks fail. Fix the cause, without widening the scope.",
            "",
        ]
        for failure in self.failures():
            blocks += [
                f"### {failure.name} — `{failure.command}`",
                "```",
                _truncate(failure.output),
                "```",
                "",
            ]
        blocks.append("Then run the commands again yourself to check, and update `.choregos/result.json`.")
        return "\n".join(blocks)


def commands_for(stage_input: StageInput, workspace: Path) -> dict[str, str]:
    """Repository commands: the project's first, otherwise the language's conventions."""
    project = stage_input.project
    defaults = LANGUAGE_DEFAULTS.get(_detect_language(workspace), {})
    return {
        "tests": _command(project.test_command, defaults.get("test", "")),
        "lint": _command(project.lint_command, defaults.get("lint", "")),
        "typecheck": _command(project.typecheck_command, defaults.get("typecheck", "")),
    }


#: A project command set to this skips the check (S59-04): a repository without a type checker is not
#: sent to `make typecheck`, a target it lacks, which failed every step and sent agents to "fix" it.
NO_COMMAND = "-"


def _command(declared: str | None, default: str) -> str:
    if declared is not None and declared.strip() == NO_COMMAND:
        return ""
    return declared or default


def _detect_language(workspace: Path) -> str:
    markers = {
        "python": ("pyproject.toml", "setup.py", "requirements.txt"),
        "node": ("package.json",),
        "go": ("go.mod",),
        "java": ("build.gradle", "pom.xml"),
        "dotnet": ("global.json",),
    }
    for language, files in markers.items():
        if any((workspace / name).exists() for name in files):
            return language
    return "other"


async def run_checks(stage_input: StageInput, workspace: Path, *, timeout: float = 1800.0) -> DodReport:
    """Run tests, lint and typing. A missing command is `skipped`, never `ok`."""
    report = DodReport()
    for name, command in commands_for(stage_input, workspace).items():
        if not command:
            report.checks.append(CheckOutcome(name=name, command="", ok=False, skipped=True))
            continue
        result: CommandResult = await run_command(command, cwd=workspace, timeout=timeout)
        report.checks.append(
            CheckOutcome(name=name, command=command, ok=result.ok, output=_truncate(result.output))
        )
    await _measure_facts(stage_input, workspace, report, timeout=timeout)
    return report


async def _measure_facts(
    stage_input: StageInput, workspace: Path, report: DodReport, *, timeout: float
) -> None:
    """Run the `dod.facts` commands: exit code 0 = true.

    This is what separates `evidence_facts` from a story. Before, a business fact ("the finding
    is resolved", "the identity document is there") was written by the agent, so believable and not
    verifiable. A command, on the other hand, can be replayed.

    The facts live at the root of the project configuration, not under `repo`: a project without
    a repository must be able to prove something (ADR 0012).
    """
    for name, command in stage_input.project.fact_commands.items():
        if not command.strip():
            continue
        outcome = await run_command(command, cwd=workspace, timeout=timeout)
        report.facts[name] = outcome.ok
        report.checks.append(
            CheckOutcome(
                name=f"fait:{name}",
                command=command,
                ok=outcome.ok,
                output=_truncate(outcome.output),
            )
        )


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 2 :]
    return f"{head}\n… (output truncated) …\n{tail}"


def _parse_test_counts(output: str) -> dict[str, int]:
    """Extract "N passed, M failed" from the most common test outputs."""
    import re

    counts: dict[str, int] = {}
    passed = re.search(r"(\d+)\s+passed", output)
    failed = re.search(r"(\d+)\s+failed", output)
    ran = re.search(r"Ran\s+(\d+)\s+tests?", output) or re.search(r"Tests:\s+(\d+)", output)
    ok_go = re.search(r"^ok\s", output, re.M)
    if passed:
        counts["run"] = int(passed.group(1)) + (int(failed.group(1)) if failed else 0)
    elif ran:
        counts["run"] = int(ran.group(1))
    elif ok_go:
        counts["run"] = output.count("\nok ") + (1 if output.startswith("ok ") else 0)
    if failed:
        counts["failed"] = int(failed.group(1))
    elif "run" in counts:
        counts["failed"] = 0
    return counts


def merge_evidence(base: Evidence, measured: Evidence) -> Evidence:
    """The evidence measured by the runner wins over what the agent declared.

    `facts` merges **by key**, not as a block: the agent may name five facts of which only one is
    measured by a command. Overwriting the whole dictionary would lose the other four, and the
    loss would be silent, which is the worse of the two.
    """
    data: dict[str, Any] = base.model_dump()
    # What the platform measured, field by field (ADR 0045): the console says "observed" for
    # those, "declared" for the rest. The agent's list is ignored: it cannot call itself
    # measured.
    measured_fields: set[str] = set()
    for field_name, value in measured.model_dump().items():
        if value is None or field_name == "measured":
            continue
        if field_name == "facts" and isinstance(value, dict):
            merged_facts = dict(data.get("facts") or {})
            merged_facts.update(value)
            data["facts"] = merged_facts
            measured_fields.update(f"facts.{key}" for key in value)
            continue
        data[field_name] = value
        measured_fields.add(field_name)
    data["measured"] = sorted(measured_fields)
    return Evidence.model_validate(data)
