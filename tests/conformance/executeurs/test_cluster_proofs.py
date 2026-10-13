"""S35-04 — a capability an executor announces must have its test on a real cluster.

ADR 0013 refused an executor announcing a capability it does not IMPLEMENT
(`test_capacites.py`). ADR 0016 asked for more before `agent_sandbox` announced anything: a
capability is proved where it runs, by `tests/cluster`. This is the rule that makes the request
hold: every capability an executor announces is carried by at least one test in `tests/cluster`
marked `@pytest.mark.capability(executor, capability)`, in a module marked `cluster`. Announce a
capability without such a test, and CI is red.

CI has no cluster, so what is checked is that the proof EXISTS, not that it passed; it passes
where `make test-cluster` runs against a cluster that has the runtime (and is skipped, saying
why, where it does not). Two capabilities of `k8s_job` predate the rule and have no cluster test
yet: they are listed below with the reason, and the list can only shrink.
"""

from __future__ import annotations

import ast
import pathlib
from collections.abc import Mapping

import pytest
from choregos_adapters import available, build

from .test_capacites import CONFIGS

pytestmark = pytest.mark.conformance

CLUSTER = pathlib.Path(__file__).resolve().parents[2] / "cluster"

#: Announced before this rule, with no cluster test yet. An entry leaves this list the day its
#: test is written; no entry is ever added — `test_the_exemptions_only_shrink` checks both.
WITHOUT_CLUSTER_TEST: Mapping[tuple[str, str], str] = {
    ("k8s_job", "queue"): "suspended Jobs admitted in arrival order: unit tests only, no cluster test yet",
    ("k8s_job", "renew"): "token replaced while queued: unit tests only, no cluster test yet",
}


def cluster_proofs(directory: pathlib.Path = CLUSTER) -> dict[tuple[str, str], list[str]]:
    """`{(executor, capability): [test ids]}` read from the cluster tests' decorators.

    Read, not imported: the cluster tests need a cluster, CI has none. A test counts only in a
    module that is marked `cluster` as a whole — elsewhere it would run (or be skipped) as a unit
    test and prove nothing about a cluster."""
    proofs: dict[tuple[str, str], list[str]] = {}
    for path in sorted(directory.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if not _module_marked_cluster(tree):
            continue
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) or not node.name.startswith(
                "test_"
            ):
                continue
            for decorator in node.decorator_list:
                pair = _capability(decorator)
                if pair is not None:
                    proofs.setdefault(pair, []).append(f"{path.name}::{node.name}")
    return proofs


def _module_marked_cluster(tree: ast.Module) -> bool:
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets)
            and "mark.cluster" in ast.unparse(node.value)
        ):
            return True
    return False


def _capability(decorator: ast.expr) -> tuple[str, str] | None:
    if not (isinstance(decorator, ast.Call) and ast.unparse(decorator.func) == "pytest.mark.capability"):
        return None
    args = [a.value for a in decorator.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
    return (args[0], args[1]) if len(args) == 2 else None


def missing_proofs(
    announced: Mapping[str, frozenset[str]],
    proofs: Mapping[tuple[str, str], list[str]],
    exempt: Mapping[tuple[str, str], str],
) -> list[tuple[str, str]]:
    """The `(executor, capability)` pairs announced with neither a cluster test nor an exemption."""
    return sorted(
        (name, capability)
        for name, capabilities in announced.items()
        for capability in capabilities
        if (name, capability) not in proofs and (name, capability) not in exempt
    )


def _announced(monkeypatch: pytest.MonkeyPatch) -> dict[str, frozenset[str]]:
    monkeypatch.delenv("CHOREGOS_FAKES", raising=False)  # the real executors, not the fakes
    return {
        name: frozenset(getattr(build("runtime", name, CONFIGS.get(name, {})), "capabilities", frozenset()))
        for name in available("runtime")
        if name != "fake"
    }


def test_every_announced_capability_has_its_cluster_test(monkeypatch: pytest.MonkeyPatch) -> None:
    missing = missing_proofs(_announced(monkeypatch), cluster_proofs(), WITHOUT_CLUSTER_TEST)
    assert missing == [], (
        f"{missing} announced without a test in tests/cluster marked "
        "`@pytest.mark.capability(executor, capability)`: prove it on a cluster before announcing it"
    )


def test_agent_sandbox_proves_all_five() -> None:
    """The executor this rule was written for: nothing of it is exempt."""
    proven = {cap for (name, cap) in cluster_proofs() if name == "agent_sandbox"}
    assert proven == {"queue", "renew", "snapshot", "suspend", "resume"}, proven


def test_the_rule_refuses_an_unproved_capability() -> None:
    """The check above is only worth its red: a capability with no proof is named."""
    proofs = {("agent_sandbox", "queue"): ["test_x.py::test_y"]}
    assert missing_proofs({"agent_sandbox": frozenset({"queue", "suspend"})}, proofs, {}) == [
        ("agent_sandbox", "suspend")
    ]


def test_a_proof_outside_a_cluster_module_does_not_count(tmp_path: pathlib.Path) -> None:
    (tmp_path / "test_unit.py").write_text(
        "import pytest\n\n@pytest.mark.capability('x', 'queue')\ndef test_q():\n    pass\n", encoding="utf-8"
    )
    (tmp_path / "test_real.py").write_text(
        "import pytest\n\npytestmark = pytest.mark.cluster\n\n"
        "@pytest.mark.capability('x', 'suspend')\nasync def test_s():\n    pass\n",
        encoding="utf-8",
    )
    assert cluster_proofs(tmp_path) == {("x", "suspend"): ["test_real.py::test_s"]}


def test_the_exemptions_only_shrink(monkeypatch: pytest.MonkeyPatch) -> None:
    """An exemption whose capability got its cluster test, or is no longer announced, goes."""
    announced, proofs = _announced(monkeypatch), cluster_proofs()
    stale = sorted(
        pair
        for pair in WITHOUT_CLUSTER_TEST
        if pair in proofs or pair[1] not in announced.get(pair[0], frozenset())
    )
    assert stale == [], f"{stale}: remove them from WITHOUT_CLUSTER_TEST"
    assert set(WITHOUT_CLUSTER_TEST) <= {("k8s_job", "queue"), ("k8s_job", "renew")}, "no new exemption"
