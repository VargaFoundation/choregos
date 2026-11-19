"""The nightly real-model proof spends a real key, and only where it should (S80-13).

`.github/workflows/live-model.yml` hands the repository's ANTHROPIC_API_KEY to the chart's LiteLLM
and runs `tests/live/test_litellm_live.py` through it. What these checks hold: no pull request can
reach the key or spend it, the run is the repository's own and from main, the key is in the
environment of the one step that starts the gateway and nowhere else, a failure stays red, and the
gateway the night starts routes every alias the tests call to the cheapest model, with no retry.
"""

from __future__ import annotations

import ast
import pathlib
import re
from typing import Any

import yaml
from tranibor_core.publishing import PUBLISHING

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "live-model.yml"
STACK = ROOT / "tests" / "live" / "model"
SUITE = ROOT / "tests" / "live" / "test_litellm_live.py"
SECRET = "secrets.ANTHROPIC_API_KEY"
CHEAPEST = "anthropic/claude-haiku-5-5"


def _workflow() -> dict[Any, Any]:
    return dict(yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")))


def _triggers(workflow: dict[Any, Any]) -> dict[str, Any]:
    # PyYAML reads the bare key `on` as the boolean True.
    return dict(workflow.get("on", workflow.get(True)) or {})


def _gateway() -> dict[str, Any]:
    return dict(_workflow()["jobs"]["gateway"])


def _compose() -> dict[str, Any]:
    return dict(yaml.safe_load((STACK / "compose.yaml").read_text(encoding="utf-8")))


def _litellm_config() -> dict[str, Any]:
    return dict(yaml.safe_load((STACK / "litellm.yaml").read_text(encoding="utf-8")))


def test_it_runs_at_night_and_on_demand_never_on_a_pull_request() -> None:
    triggers = _triggers(_workflow())
    assert set(triggers) == {"schedule", "workflow_dispatch"}, triggers


def test_its_night_is_not_the_other_nights() -> None:
    """The nightly (02:00) and the single-node proof (02:30) already share the early hours."""
    ours = {entry["cron"] for entry in _triggers(_workflow())["schedule"]}
    others: set[str] = set()
    for path in WORKFLOWS.glob("*.yml"):
        if path != WORKFLOW:
            for entry in _triggers(yaml.safe_load(path.read_text(encoding="utf-8"))).get("schedule") or []:
                others.add(entry["cron"])
    assert ours and not ours & others, (ours, others)


def test_only_the_repository_itself_from_main_spends_the_key() -> None:
    upstream = PUBLISHING.source_repository.removeprefix("https://github.com/")
    guard = str(_gateway().get("if", ""))
    assert f"github.repository == '{upstream}'" in guard, guard
    assert "github.ref == 'refs/heads/main'" in guard, guard


def test_it_reads_the_repository_and_nothing_more() -> None:
    workflow = _workflow()
    assert workflow["permissions"] == {"contents": "read"}
    assert "permissions" not in _gateway(), "the job that holds the key needs no write permission"


def test_the_key_is_in_the_environment_of_one_step_the_one_that_starts_the_gateway() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert len(re.findall(re.escape(SECRET), text)) == 1, "the key is named once, in one step"
    workflow = _workflow()
    assert SECRET not in str(workflow.get("env", {}))
    for name, job in workflow["jobs"].items():
        assert SECRET not in str(job.get("env", {})), f"job {name} puts the key in every step"
    holders = [step for step in _gateway()["steps"] if SECRET in str(step.get("env", {}))]
    assert len(holders) == 1, holders
    (holder,) = holders
    assert set(holder["env"]) == {"ANTHROPIC_API_KEY"}
    assert "docker compose" in holder["run"] and " up " in holder["run"], holder["run"]
    # A vanished secret is an error, not a night of calls that fail for another reason.
    assert '[ -z "$ANTHROPIC_API_KEY" ]' in holder["run"] and "exit 1" in holder["run"]
    # Before the gateway starts, Anthropic says whether it accepts the key and serves the model,
    # with calls that spend nothing; the key goes to curl on stdin, never on its command line.
    assert "https://api.anthropic.com/v1/$path" in holder["run"]
    assert "models/claude-haiku-5-5" in holder["run"]
    assert holder["run"].index("api.anthropic.com") < holder["run"].index("docker compose")
    assert "-H @-" in holder["run"] and not re.search(r"-H ['\"]?x-api-key", holder["run"])
    # Nothing prints it: no echo, no env dump, no `set -x`.
    assert not re.search(r"echo[^\n]*\$\{?ANTHROPIC_API_KEY|\benv\b\s*$|printenv|set -x", holder["run"], re.M)


def test_a_failure_stays_red_and_a_skip_is_a_failure() -> None:
    gateway = _gateway()
    assert gateway.get("timeout-minutes"), "a hung gateway must not hold a runner for six hours"
    for job in _workflow()["jobs"].values():
        assert not job.get("continue-on-error")
        assert not any(step.get("continue-on-error") for step in job.get("steps", []))
    (suite,) = [step for step in gateway["steps"] if "pytest" in str(step.get("run", ""))]
    assert "tests/live/test_litellm_live.py" in suite["run"]
    assert 'test "${PIPESTATUS[0]}" -eq 0' in suite["run"]
    assert "! grep -qi skipped" in suite["run"]
    assert gateway["env"]["CHOREGOS_LIVE_REQUIRED"] == "litellm"


def test_only_a_failed_night_opens_the_nightly_issue() -> None:
    alert = _workflow()["jobs"]["alert"]
    assert alert["needs"] == ["gateway"]
    assert alert["if"] == "failure() && github.event_name == 'schedule'"
    assert alert["permissions"] == {"issues": "write"}


def test_the_job_reaches_the_gateway_the_stack_starts() -> None:
    env = _gateway()["env"]
    litellm = _compose()["services"]["litellm"]
    (port,) = litellm["ports"]
    host_port = port.split(":")[1]
    assert port.startswith("127.0.0.1:"), "the gateway is not published beyond the runner"
    assert env["CHOREGOS_LIVE_LITELLM_URL"] == f"http://127.0.0.1:{host_port}"
    assert env["CHOREGOS_LIVE_LITELLM_KEY"] == litellm["environment"]["LITELLM_MASTER_KEY"]
    # The provider key reaches LiteLLM, and only LiteLLM.
    services = _compose()["services"]
    assert [name for name, service in services.items() if "ANTHROPIC_API_KEY" in str(service)] == ["litellm"]


def _suite_aliases() -> set[str]:
    aliases: set[str] = set()
    for node in ast.parse(SUITE.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            names = {target.id for target in node.targets if isinstance(target, ast.Name)}
            if names & {"MODEL", "PRICED_MODEL"}:
                aliases.add(str(node.value.value))
    return aliases


def test_every_alias_the_suite_calls_is_on_the_cheapest_model_without_retry() -> None:
    config = _litellm_config()
    routed = {entry["model_name"]: entry["litellm_params"] for entry in config["model_list"]}
    aliases = _suite_aliases()
    assert aliases == {"platform/standard", "platform/cheap"}, aliases
    assert aliases <= set(routed)
    for alias, params in routed.items():
        assert params["model"] == CHEAPEST, f"{alias} routes to {params['model']}"
        assert params["api_key"] == "os.environ/ANTHROPIC_API_KEY", "the key is read, never written"
        assert 0 < params["max_tokens"] <= 1024, f"{alias}: a call that sets no cap must have one"
    assert config["router_settings"]["num_retries"] == 0, "a failed call is never billed twice"
