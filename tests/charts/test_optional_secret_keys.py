"""Which secret keys are required at start-up, and which must not be?

On 2026-09-26 the dev tenant started its whole control plane except the API: it stayed in
`CreateContainerConfigError` on "couldn't find key generic-webhook-secret in Secret
choregos/choregos-api-secrets". The chart hard-required a key that the tenant's `InfisicalSecret`
template did not map: an unused feature (the generic webhook) kept the API from existing.

The rule that came out of it, and that this test holds:

  * a **structural** key (session secret, private key of the run tokens) stays required:
    starting without it would be worse than not starting: cookies signed with an empty key, or
    two processes that each leave on an ephemeral pair;
  * a **feature** key (webhooks, GitHub App, derivable public key) is `optional: true`: absent,
    the value is empty, and the code already refuses an empty one (`verify_shared_secret`
    returns False; the GitHub webhook refuses in staging/prod).

The test reads the render, not the template: the manifest decides whether a pod starts.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART = ROOT / "charts" / "choregos"

#: Without them, the API must NOT start.
STRUCTURAL = {"session-secret", "run-token-private-key"}
#: Without them, a feature is off: this is not a broken deployment.
FEATURE = {
    "run-token-public-key",
    "github-webhook-secret",
    "generic-webhook-secret",
    "github-app-id",
    "github-app-private-key",
    # The App's OAuth client (ADR 0073): absent, a multi-organisation claim is refused.
    "github-app-client-id",
    "github-app-client-secret",
    # The key sealing the connector secrets (ADR 0058): absent, typing a secret answers 409.
    "secrets-keks",
    # The previous session key during a rotation (S47-01): absent, there is no rotation.
    "session-secret-previous",
}

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


#: The API's own secret (`global.apiSecretRef`): the other references point at secrets held by
#: others (database, OIDC, gateway), outside this classification.
API_SECRET = "choregos-api-secrets"


def _secret_refs(env: str) -> dict[str, bool]:
    """{key: optional} for the API's `secretKeyRef`s that target ITS secret."""
    rendered = subprocess.run(
        ["helm", "template", "choregos", str(CHART), "-f", str(CHART / "values" / f"{env}.yaml")],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    found: dict[str, bool] = {}
    for doc in yaml.safe_load_all(rendered.stdout):
        if not doc or doc.get("kind") != "Deployment" or doc["metadata"]["name"] != "choregos-api":
            continue
        spec: dict[str, Any] = doc["spec"]["template"]["spec"]
        for container in spec.get("containers", []) + spec.get("initContainers", []):
            for variable in container.get("env", []) or []:
                ref = (variable.get("valueFrom") or {}).get("secretKeyRef")
                if ref and ref.get("name") == API_SECRET:
                    found[ref["key"]] = bool(ref.get("optional"))
    return found


@pytest.mark.parametrize("env", ("local", "dev", "staging", "prod"))
def test_the_structural_keys_stay_required(env: str) -> None:
    refs = _secret_refs(env)
    if not refs:
        pytest.skip(f"{env} does not render the API deployment")
    for key in STRUCTURAL:
        assert key in refs, f"{env}: {key} is not read at all any more"
        assert refs[key] is False, (
            f"{env}: {key} became optional. An API that starts without it signs with an "
            "empty key or issues tokens it will not be able to verify."
        )


@pytest.mark.parametrize("env", ("local", "dev", "staging", "prod"))
def test_the_feature_keys_are_optional(env: str) -> None:
    """A tenant with no GitHub App and no generic webhook must see its API start."""
    refs = _secret_refs(env)
    if not refs:
        pytest.skip(f"{env} does not render the API deployment")
    required = sorted(key for key in FEATURE if refs.get(key) is False)
    assert not required, (
        f"{env}: {required} are hard-required. Their absence would keep the API from existing "
        "while the code treats them as features that are off."
    )


def test_no_unknown_key_slipped_into_the_api_secret() -> None:
    """A key added without picking a side would break a tenant at the next deployment:
    this test forces the decision here, not in a namespace."""
    refs = _secret_refs("prod")
    unknown = set(refs) - STRUCTURAL - FEATURE
    assert not unknown, (
        f"unclassified keys: {sorted(unknown)}. Structural (required) or feature "
        "(optional)? Decide here, and write it in this test."
    )
