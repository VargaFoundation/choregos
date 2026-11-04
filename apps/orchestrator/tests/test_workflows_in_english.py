"""The orchestrator's workflows, queue routing, worker and configuration are English (S45-22,
ADR 0098), and nothing a recorded history names has moved: the signal and query wire names are the
recorded ones, and every former Python name still resolves."""

from __future__ import annotations

import ast
import importlib
import importlib.util
import pathlib
import sys
from types import ModuleType

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "apps" / "orchestrator" / "src" / "choregos_orchestrator"
WORKFLOWS = PACKAGE / "workflows"

#: The root modules S45-22 owns, next to every module of `workflows/`. `wire_names.py` is S45-03's
#: and holds no French; `activities/` is S45-23's.
OWNED_ROOT = (
    "__init__",
    "config",
    "context_reduction",
    "demo",
    "gitops",
    "local",
    "markdown",
    "org_egress",
    "queues",
    "testing",
    "train_client",
    "worker",
)

#: (workflow type, "signal" or "query", wire name), as recorded before S45-22. A name is the `name=`
#: of its decorator or, without one, the method's name: a rename of a method without `name=` would
#: change what a history and every sender call.
RECORDED_WIRE_NAMES = {
    ("ActionWorkflow", "query", "status"),
    ("ActionWorkflow", "signal", "evidence"),
    ("ActionWorkflow", "signal", "preuve"),
    ("AgentSessionPod", "signal", "close"),
    ("AgentSessionPod", "signal", "touch"),
    ("EvalMatrix", "query", "status"),
    ("FindingsTriage", "query", "status"),
    ("FindingsTriage", "signal", "create_ticket"),
    ("FindingsTriage", "signal", "finding"),
    ("FindingsTriage", "signal", "stop"),
    ("KnowledgeDistill", "query", "status"),
    ("KnowledgeSync", "query", "status"),
    ("KnowledgeSync", "signal", "stop"),
    ("KnowledgeSync", "signal", "sync_now"),
    ("MemoryIngestion", "query", "status"),
    ("MemoryIngestion", "signal", "ab_report_now"),
    ("MemoryIngestion", "signal", "alert"),
    ("MemoryIngestion", "signal", "reimport"),
    ("MemoryIngestion", "signal", "stop"),
    ("ProjectProvisioning", "query", "status"),
    ("ReleaseTrain", "query", "status_query"),
    ("ReleaseTrain", "signal", "abort"),
    ("ReleaseTrain", "signal", "approve"),
    ("ReleaseTrain", "signal", "depart_now"),
    ("ReleaseTrain", "signal", "deploy_event"),
    ("ReleaseTrain", "signal", "freeze"),
    ("ReleaseTrain", "signal", "merged"),
    ("ReleaseTrain", "signal", "reject"),
    ("ReleaseTrain", "signal", "unfreeze"),
    ("TrackerReconciliation", "query", "status"),
    ("TrackerReconciliation", "signal", "reconcile_now"),
    ("TrackerReconciliation", "signal", "stop"),
    ("WorkflowInterpreter", "query", "status"),
    ("WorkflowInterpreter", "signal", "action_settled"),
    ("WorkflowInterpreter", "signal", "child_settled"),
    ("WorkflowInterpreter", "signal", "control"),
    ("WorkflowInterpreter", "signal", "fields_changed"),
    ("WorkflowInterpreter", "signal", "finding"),
    ("WorkflowInterpreter", "signal", "human_decision"),
    ("WorkflowInterpreter", "signal", "inbound"),
    ("WorkflowInterpreter", "signal", "question_asked"),
}

#: Every workflow type, recorded the same way.
RECORDED_WORKFLOW_TYPES = {
    "ActionWorkflow",
    "AgentSessionPod",
    "AgentTrial",
    "EvalMatrix",
    "FindingsTriage",
    "HostedImageBuild",
    "KnowledgeDistill",
    "KnowledgeSync",
    "MemoryIngestion",
    "ProjectProvisioning",
    "ReleaseTrain",
    "TrackerReconciliation",
    "WorkflowInterpreter",
}


def _checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_english_s45_22", ROOT / "tools" / "check_english.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass needs its module registered
    spec.loader.exec_module(module)
    return module


def _owned_files() -> list[pathlib.Path]:
    return [*(PACKAGE / f"{name}.py" for name in OWNED_ROOT), *sorted(WORKFLOWS.glob("*.py"))]


def _decorator(node: ast.AST, kind: str) -> ast.expr | None:
    for decorator in getattr(node, "decorator_list", []):
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        owner = ast.unparse(target.value) if isinstance(target, ast.Attribute) else ""
        if isinstance(target, ast.Attribute) and target.attr == kind and owner == "workflow":
            return decorator
    return None


def _declared_name(decorator: ast.expr, default: str) -> str:
    if isinstance(decorator, ast.Call):
        for keyword in decorator.keywords:
            if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                return str(keyword.value.value)
        if decorator.args and isinstance(decorator.args[0], ast.Constant):
            return str(decorator.args[0].value)
    return default


def _wire_names() -> tuple[set[tuple[str, str, str]], set[str]]:
    names: set[tuple[str, str, str]] = set()
    types: set[str] = set()
    for path in sorted(WORKFLOWS.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ClassDef) or (defn := _decorator(node, "defn")) is None:
                continue
            workflow_type = _declared_name(defn, node.name)
            types.add(workflow_type)
            for member in node.body:
                for kind in ("signal", "query"):
                    if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef) and (
                        decorator := _decorator(member, kind)
                    ):
                        names.add((workflow_type, kind, _declared_name(decorator, member.name)))
    return names, types


def test_workflows_and_root_have_no_french() -> None:
    """The scan of `tools/check_english.py` (prose, declared names and paths), over the owned files,
    finds nothing: a French name that must stay carries `lang: fr-ok` and its reason."""
    checker = _checker()
    findings = [
        str(finding)
        for path in _owned_files()
        for finding in checker.file_findings(path.relative_to(ROOT).as_posix(), path.read_text("utf-8"))
    ]
    assert findings == []


def test_signal_and_query_wire_names_unchanged() -> None:
    names, types = _wire_names()
    assert names == RECORDED_WIRE_NAMES, {
        "gone": sorted(RECORDED_WIRE_NAMES - names),
        "new": sorted(names - RECORDED_WIRE_NAMES),
    }
    assert types == RECORDED_WORKFLOW_TYPES


def test_the_former_french_modules_and_test_helpers_are_gone() -> None:
    from choregos_orchestrator import testing as testing_module

    for former in ("choregos_orchestrator.repartition", "choregos_orchestrator.workflows.planification"):
        assert importlib.util.find_spec(former) is None, former
    for helper in ("workers_by_queue", "with_doubles", "group_by_queue"):
        assert callable(getattr(testing_module, helper))


def test_the_queue_table_routes_by_activity_name() -> None:
    from choregos_orchestrator.queues import ACTIVITIES_BY_QUEUE, QUEUE_BY_ACTIVITY, activity_name, queue_of

    assert set(ACTIVITIES_BY_QUEUE) == {"executor", "tracker", "memory"}
    assert QUEUE_BY_ACTIVITY["start_run"] == "executor"
    assert queue_of("open_pull_request") == "tracker"
    assert queue_of("load_context") is None, "an activity with no declared queue stays on the workflow's"
    assert activity_name("anything") == "anything"
    assert activity_name(object()) is None


def test_the_tools_sidecar_url_has_its_english_name() -> None:
    from choregos_orchestrator.config import OrchestratorSettings

    settings = OrchestratorSettings(tools_mcp_url="http://tools:7777/mcp")
    assert settings.tools_sidecar_url == "http://tools:7777/mcp"


def test_the_train_reads_a_recorded_former_threshold_input() -> None:
    from choregos_orchestrator.workflows import train

    old = train.TrainInput(project_slug="p", env="e", seuil_de_relais=40)  # lang: fr-ok
    assert old.former_idle_threshold() == 40
    assert old.threshold() == 40
    assert train.TrainInput(project_slug="p", env="e").former_idle_threshold() == train.FORMER_RELAY_THRESHOLD


def test_the_action_workflow_names() -> None:
    from choregos_orchestrator.workflows import action

    assert issubclass(action.MissingEvidence, Exception)
    assert action.EFFECT.non_retryable_error_types


@pytest.mark.parametrize(
    ("module", "name", "value"),
    [
        ("interpreter", "MIGRATION_BY_DEFINITION", "migration-par-definition"),
        ("interpreter", "TRANSITION_ACTIONS", "actions-de-transition"),
        ("interpreter", "HUMAN_TASKS", "taches-humaines"),
        ("interpreter", "TRAIN_PER_ENVIRONMENT", "train-par-environnement"),
        ("action", "AWAITED_EVIDENCE_PATCH", "preuve-attendue"),
        ("train", "AUTO_SYNC_WARNS_ITS_ITEMS", "auto-sync-previent"),
    ],
)
def test_the_patch_ids_keep_their_values(module: str, name: str, value: str) -> None:
    """The renamed Python constants hold the recorded ids: a history marks them."""
    assert getattr(importlib.import_module(f"choregos_orchestrator.workflows.{module}"), name) == value
