# SPDX-License-Identifier: Apache-2.0
"""Boucle « definition of done » : tests, lint, typage — et re-prompt borné si c'est rouge.

C'est le mécanisme qui transforme « l'agent dit que c'est fini » en « les commandes du dépôt
passent ». Le nombre d'itérations vient de la politique du projet.
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
    """Résultat d'un passage complet de vérification."""

    checks: list[CheckOutcome] = field(default_factory=list)
    iterations: int = 0

    @property
    def ok(self) -> bool:
        return all(check.ok or check.skipped for check in self.checks)

    def failures(self) -> list[CheckOutcome]:
        return [check for check in self.checks if not check.ok and not check.skipped]

    def get(self, name: str) -> CheckOutcome | None:
        return next((check for check in self.checks if check.name == name), None)

    #: {nom: vrai/faux} pour les faits MESURÉS par une commande (`dod.facts` du projet).
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
            # `None` et non `{}` quand rien n'est mesuré : `merge_evidence` n'écrase que le
            # non-`None`, donc un dictionnaire vide effacerait les faits de l'agent.
            facts=(dict(self.facts) or None),
        )

    def prompt_for_repair(self) -> str:
        """Message envoyé à l'agent quand les vérifications échouent."""
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
    """Commandes du dépôt : celles du projet d'abord, sinon les conventions du langage."""
    project = stage_input.project
    defaults = LANGUAGE_DEFAULTS.get(_detect_language(workspace), {})
    return {
        "tests": project.test_command or defaults.get("test", ""),
        "lint": project.lint_command or defaults.get("lint", ""),
        "typecheck": project.typecheck_command or defaults.get("typecheck", ""),
    }


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
    """Exécute tests, lint et typage. Une commande absente est `skipped`, jamais `ok`."""
    report = DodReport()
    for name, command in commands_for(stage_input, workspace).items():
        if not command:
            report.checks.append(CheckOutcome(name=name, command="", ok=False, skipped=True))
            continue
        result: CommandResult = await run_command(command, cwd=workspace, timeout=timeout)
        report.checks.append(
            CheckOutcome(name=name, command=command, ok=result.ok, output=_truncate(result.output))
        )
    await _mesurer_les_faits(stage_input, workspace, report, timeout=timeout)
    return report


async def _mesurer_les_faits(
    stage_input: StageInput, workspace: Path, report: DodReport, *, timeout: float
) -> None:
    """Exécute les commandes de `dod.facts` : code de sortie 0 = vrai.

    C'est ce qui sépare `evidence_facts` d'un récit. Avant, un fait métier — « le constat est
    résolu », « la pièce d'identité est là » — était écrit par l'agent, donc croyable et pas
    vérifiable. Une commande, elle, se rejoue.

    Les faits vivent à la racine de la configuration du projet, pas sous `repo` : un projet sans
    dépôt doit pouvoir prouver quelque chose (ADR 0012).
    """
    for nom, commande in stage_input.project.fact_commands.items():
        if not commande.strip():
            continue
        resultat = await run_command(commande, cwd=workspace, timeout=timeout)
        report.facts[nom] = resultat.ok
        report.checks.append(
            CheckOutcome(
                name=f"fait:{nom}",
                command=commande,
                ok=resultat.ok,
                output=_truncate(resultat.output),
            )
        )


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 2 :]
    return f"{head}\n… (output truncated) …\n{tail}"


def _parse_test_counts(output: str) -> dict[str, int]:
    """Extrait « N passed, M failed » des sorties de test les plus courantes."""
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
    """Les preuves mesurées par le runner l'emportent sur celles déclarées par l'agent.

    `facts` fusionne **par clé**, pas en bloc : l'agent peut nommer cinq faits dont un seul est
    mesuré par une commande. Écraser le dictionnaire entier perdrait les quatre autres — et la
    perte serait silencieuse, ce qui est le pire des deux.
    """
    data: dict[str, Any] = base.model_dump()
    for field_name, value in measured.model_dump().items():
        if value is None:
            continue
        if field_name == "facts" and isinstance(value, dict):
            fusion = dict(data.get("facts") or {})
            fusion.update(value)
            data["facts"] = fusion
            continue
        data[field_name] = value
    return Evidence.model_validate(data)
