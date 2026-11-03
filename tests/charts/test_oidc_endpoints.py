"""The chart can give the API an IdP's three endpoints, all or none (S47-04, ADR 0100)."""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"
ENDPOINTS = (
    "CHOREGOS_OIDC_AUTHORIZATION_ENDPOINT",
    "CHOREGOS_OIDC_TOKEN_ENDPOINT",
    "CHOREGOS_OIDC_USERINFO_ENDPOINT",
)

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _render(*json_sets: str) -> subprocess.CompletedProcess[str]:
    command = ["helm", "template", "choregos", str(CHART)]
    for value in json_sets:
        command += ["--set-json", value]
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _api_env(*json_sets: str) -> dict[str, dict[str, Any]]:
    rendered = _render(*json_sets)
    assert rendered.returncode == 0, rendered.stderr[-600:]
    docs = [doc for doc in yaml.safe_load_all(rendered.stdout) if doc]
    (api,) = [
        container
        for doc in docs
        if doc["kind"] in {"Deployment", "Rollout"} and doc["metadata"]["name"] == "choregos-api"
        for container in doc["spec"]["template"]["spec"]["containers"]
        if container["name"] == "api"
    ]
    return {item["name"]: item for item in api.get("env") or []}


def test_explicit_endpoints_are_rendered_together() -> None:
    env = _api_env(
        'choregos-api.oidcEndpoints={"authorization":"https://sso.example/auth",'
        '"token":"https://sso.example/token","userinfo":"https://sso.example/me"}'
    )
    assert [env[name]["value"] for name in ENDPOINTS] == [
        "https://sso.example/auth",
        "https://sso.example/token",
        "https://sso.example/me",
    ]
    assert env["CHOREGOS_OIDC_REQUIRE_EMAIL_VERIFIED"]["value"] == "true"


def test_a_partial_set_of_endpoints_fails_the_render() -> None:
    rendered = _render('choregos-api.oidcEndpoints={"authorization":"https://sso.example/auth"}')
    assert rendered.returncode != 0
    assert "oidcEndpoints" in rendered.stderr


def test_by_default_no_endpoint_variable_is_rendered() -> None:
    env = _api_env()
    assert not [name for name in ENDPOINTS if name in env]
    assert env["CHOREGOS_OIDC_REQUIRE_EMAIL_VERIFIED"]["value"] == "true"


def test_an_extra_env_cannot_set_them_a_second_time() -> None:
    for name in (*ENDPOINTS, "CHOREGOS_OIDC_REQUIRE_EMAIL_VERIFIED", "CHOREGOS_OIDC_RELINK_VERIFIED_EMAIL"):
        rendered = _render(f'global.extraEnv=[{{"name":"{name}","value":"x"}}]')
        assert rendered.returncode != 0, name
        assert "choregos-api.oidc" in rendered.stderr


def test_the_two_switches_render_true_or_false_only() -> None:
    off = _api_env("choregos-api.oidcRequireEmailVerified=false")
    assert off["CHOREGOS_OIDC_REQUIRE_EMAIL_VERIFIED"]["value"] == "false"
    assert _api_env()["CHOREGOS_OIDC_RELINK_VERIFIED_EMAIL"]["value"] == "false"
    on = _api_env("choregos-api.oidcRelinkVerifiedEmail=true")
    assert on["CHOREGOS_OIDC_RELINK_VERIFIED_EMAIL"]["value"] == "true"
