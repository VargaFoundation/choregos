"""`require()` of the live tests: a vanished secret of a required service fails, it does not skip (S47-24).

`tests/live` is opt-in (`norecursedirs`) and the nightly `live` job ran it with every missing secret
turned into a skip: a night where the GitLab token had vanished stayed green. `CHOREGOS_LIVE_REQUIRED`
names the services that must run. These checks load `tests/live/conftest.py` by path, as the tests
under `tests/live` import it, and prove the rule on the missing variables only.

`pytest.skip` and `pytest.fail` raise outcome exceptions that, left to escape a test body, mark the
test itself skipped or failed. `_outcome` catches them, so a wrong outcome is an assertion failure here
and never a quiet skip of the check.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
from types import ModuleType

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONFTEST = ROOT / "tests" / "live" / "conftest.py"


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """The live `conftest` module, with no live variable left in the environment."""
    for name in [name for name in os.environ if name.startswith("CHOREGOS_LIVE_")]:
        monkeypatch.delenv(name)
    spec = importlib.util.spec_from_file_location("choregos_live_conftest", CONFTEST)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _outcome(live: ModuleType, *names: str) -> tuple[str, str]:
    """What `require(*names)` does: ("fail" | "skip", message), or ("ran", "") when it returns."""
    try:
        live.require(*names)
    except pytest.fail.Exception as failed:
        return "fail", str(failed)
    except pytest.skip.Exception as skipped:
        return "skip", str(skipped)
    return "ran", ""


def test_a_required_service_fails_instead_of_skipping(
    live: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    gitlab = ("CHOREGOS_LIVE_GITLAB_TOKEN", "CHOREGOS_LIVE_GITLAB_PROJECT")
    monkeypatch.setenv("CHOREGOS_LIVE_REQUIRED", "gitlab")
    outcome, message = _outcome(live, *gitlab)
    assert outcome == "fail", message
    assert "gitlab" in message
    assert all(name in message for name in gitlab), message

    monkeypatch.delenv("CHOREGOS_LIVE_REQUIRED")
    assert _outcome(live, *gitlab)[0] == "skip"


def test_an_optional_companion_still_skips(live: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """The realtime tests ask for the LiteLLM variables and `CHOREGOS_LIVE_REALTIME_MODEL`: the model
    is an optional companion, so its absence must not turn a run that requires `litellm` red."""
    asked = ("CHOREGOS_LIVE_LITELLM_URL", "CHOREGOS_LIVE_LITELLM_KEY", "CHOREGOS_LIVE_REALTIME_MODEL")
    monkeypatch.setenv("CHOREGOS_LIVE_REQUIRED", "litellm")
    monkeypatch.setenv("CHOREGOS_LIVE_LITELLM_URL", "http://127.0.0.1:4000")
    monkeypatch.setenv("CHOREGOS_LIVE_LITELLM_KEY", "sk-test")
    outcome, message = _outcome(live, *asked)
    assert outcome == "skip", message
    assert "CHOREGOS_LIVE_REALTIME_MODEL" in message

    # A missing LiteLLM variable in the same call is what makes it fail, not the companion.
    monkeypatch.delenv("CHOREGOS_LIVE_LITELLM_KEY")
    assert _outcome(live, *asked)[0] == "fail"


def test_a_service_that_is_not_required_still_skips(
    live: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHOREGOS_LIVE_REQUIRED", "gitlab, Jira")
    assert _outcome(live, "CHOREGOS_LIVE_ECPHORIA_URL", "CHOREGOS_LIVE_ECPHORIA_TOKEN")[0] == "skip"


def test_all_requires_every_service(live: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHOREGOS_LIVE_REQUIRED", "all")
    outcome, message = _outcome(live, "CHOREGOS_LIVE_ECPHORIA_URL")
    assert outcome == "fail", message
    assert "ecphoria" in message


def test_a_required_service_with_its_variables_set_returns_them(
    live: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHOREGOS_LIVE_REQUIRED", "gitlab")
    monkeypatch.setenv("CHOREGOS_LIVE_GITLAB_TOKEN", "glpat-test")
    monkeypatch.setenv("CHOREGOS_LIVE_GITLAB_PROJECT", "group/project")
    assert live.require("CHOREGOS_LIVE_GITLAB_TOKEN", "CHOREGOS_LIVE_GITLAB_PROJECT") == {
        "CHOREGOS_LIVE_GITLAB_TOKEN": "glpat-test",
        "CHOREGOS_LIVE_GITLAB_PROJECT": "group/project",
    }


def test_the_service_is_the_third_token_of_the_name(
    live: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert live.service_of("CHOREGOS_LIVE_GITLAB_TOKEN") == "gitlab"
    assert live.service_of("CHOREGOS_LIVE_AZURE_ENV_ID") == "azure"
    assert live.service_of("CHOREGOS_LIVE_OTEL") == "otel"
    monkeypatch.setenv("CHOREGOS_LIVE_REQUIRED", " GitLab,,litellm ,")
    assert live.required_services() == {"gitlab", "litellm"}
