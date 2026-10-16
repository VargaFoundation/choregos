"""An executor announces its capabilities, and cannot announce one it does not have.

This is the **seam** through which a more capable sandbox will come in: Agent Substrate,
a snapshot runtime, or something else. It is laid down now, while a single executor fills a
single capability, so that on that day nothing else moves — the orchestrator will ask what
the runtime can do instead of assuming it.

A capability announced and absent is worse than absent: the caller relies on it.
"""

from __future__ import annotations

import inspect

import pytest
from choregos_adapters import available, build
from choregos_adapters.base import CAPACITES_EXECUTEUR

pytestmark = pytest.mark.conformance

#: The method each capability obliges to implement. `queue` imposes none: it plays out in
#: `start` and `status`, and its behaviour is checked by the tests of the executor
#: concerned (ceiling reached, queue in arrival order).
#: `snapshot` imposes none either since ADR 0053: it is a property of `start` (every run gets a
#: sandbox made from the template, never a used one), proved by the executor's own tests.
METHODE_ATTENDUE = {"renew": "renew", "suspend": "suspend", "resume": "resume"}

# Building an executor outside a cluster: without `token`, the Kubernetes client goes and reads
# the service-account token mounted in a pod, and that file does not exist here.
KUBE = {"kubernetes": {"base_url": "https://exemple.invalid", "token": "hors-cluster", "verify": False}}
CONFIGS = {
    "k8s_job": KUBE,
    "tekton": KUBE,
    "local_docker": {},
    "aca": {"subscription_id": "s", "resource_group": "g", "environment_id": "e"},
    "agent_sandbox": KUBE,
    "fake": {},
}


def _executeurs() -> list[str]:
    return sorted(available("runtime"))


def test_le_vocabulaire_reste_ferme() -> None:
    """A capability invented on the fly means nothing to the caller."""
    assert frozenset({"queue", "renew", "suspend", "resume", "snapshot"}) == CAPACITES_EXECUTEUR


@pytest.mark.parametrize("nom", _executeurs())
def test_chaque_executeur_declare_ses_capacites(nom: str) -> None:
    executeur = build("runtime", nom, CONFIGS.get(nom, {}))
    capacites = getattr(executeur, "capabilities", None)
    assert isinstance(capacites, frozenset), f"{nom} announces nothing: a caller would have to guess"
    inconnues = capacites - CAPACITES_EXECUTEUR
    assert not inconnues, f"{nom} announces capabilities outside the vocabulary: {sorted(inconnues)}"


@pytest.mark.parametrize("nom", _executeurs())
def test_ce_qui_est_annonce_est_implemente(nom: str) -> None:
    executeur = build("runtime", nom, CONFIGS.get(nom, {}))
    for capacite in sorted(getattr(executeur, "capabilities", frozenset())):
        methode = METHODE_ATTENDUE.get(capacite)
        if methode is None:
            continue
        fonction = getattr(executeur, methode, None)
        assert callable(fonction), f"{nom} announces `{capacite}` without `{methode}()`"
        assert inspect.iscoroutinefunction(fonction), f"{nom}.{methode}() must be asynchronous"


def test_only_agent_sandbox_starts_every_run_from_a_clean_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    """A FACT, not a rule. Until S35-02 it read "no executor can take a snapshot yet", and it
    turned red the day `agent_sandbox` announced `snapshot`: its purpose (ADR 0013, Decision 4).

    What `snapshot` means is now ADR 0016's and ADR 0053's: every run gets a sandbox made from the
    template and never used before, which is what makes a warm pool defensible again. Freezing a
    RUNNING sandbox and restoring it is still offered by no executor. The day this list changes,
    re-read those three records before adding a name.
    """
    # The real executors, not the fakes another test may have switched on.
    monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
    with_snapshot = [
        name
        for name in _executeurs()
        if "snapshot" in getattr(build("runtime", name, CONFIGS.get(name, {})), "capabilities", frozenset())
    ]
    assert with_snapshot == ["agent_sandbox"], (
        f"{with_snapshot} announce `snapshot`: re-read docs/adr/0013, 0016 and 0053 "
        "(warm pool of never-used sandboxes; no restore of a running one)"
    )
