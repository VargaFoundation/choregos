"""Self-managed GitLab: a base URL under a path prefix, a private CA, the oldest supported version.

The tracker, the SCM and the CI are built the way the registry builds them (`build_client`), then
their transport is swapped for a simulated one. This proves the paths, the token header and the
version gate; it does not prove a real self-managed instance accepts them.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from tranibor_adapters.ci.gitlab import GitLabCi
from tranibor_adapters.errors import ConfigurationError
from tranibor_adapters.scm.gitlab import GitLabScm
from tranibor_adapters.tracker.gitlab import GitLabTracker
from tranibor_adapters.tracker.gitlab_instance import (
    MIN_GITLAB_VERSION,
    build_client,
    normalize_base_url,
    parse_version,
)
from tranibor_core.domain import NewItem

PREFIXED = "https://tools.corp.example:8443/gitlab"
ENCODED = "ops%2Fbilling"


def served(cfg: dict[str, Any], handler: Any) -> Any:
    client = build_client({"token": "glpat-x", **cfg})
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


def instance(version: str, seen: list[httpx.Request] | None = None) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.path.endswith("/api/v4/version"):
            return httpx.Response(200, json={"version": version, "revision": "abc"})
        if request.url.path.endswith("/issues") and request.method == "POST":
            return httpx.Response(201, json={"iid": 3})
        return httpx.Response(200, json={"id": 1, "path_with_namespace": "ops/billing"})

    return handler


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "https://gitlab.com"),
        ("https://git.corp.example/", "https://git.corp.example"),
        (PREFIXED + "/", PREFIXED),
        ("http://10.0.0.5:8080/gl", "http://10.0.0.5:8080/gl"),
    ],
)
def test_base_urls_are_normalised(raw: str | None, expected: str) -> None:
    assert normalize_base_url(raw) == expected


@pytest.mark.parametrize("raw", ["git.corp.example", "ftp://x/y", "https://x/y?a=1", "https://"])
def test_a_malformed_base_url_is_refused(raw: str) -> None:
    with pytest.raises(ConfigurationError):
        normalize_base_url(raw)


def test_versions_parse_with_their_edition_suffix() -> None:
    assert parse_version("16.3.2-ee") == (16, 3, 2)
    assert parse_version("15.0") == (15, 0)
    assert parse_version("nightly") is None and parse_version(None) is None


async def test_every_adapter_calls_under_the_path_prefix_with_the_token() -> None:
    seen: list[httpx.Request] = []
    cfg = {"base_url": PREFIXED}
    tracker = GitLabTracker(served(cfg, instance("16.0.1-ee", seen)), "ops/billing")
    scm = GitLabScm(served(cfg, instance("16.0.1-ee", seen)), "ops/billing")
    ci = GitLabCi(served(cfg, instance("16.0.1-ee", seen)), "ops/billing")

    assert await tracker.create_item(NewItem(title="t", body="b", labels=[])) == "ops/billing#3"
    for adapter in (tracker, scm, ci):
        verdict = await adapter.test()
        assert verdict["ok"] is True and verdict["version"] == "16.0.1-ee"

    assert seen and all(r.url.path.startswith("/gitlab/api/v4/") for r in seen)
    assert all(r.url.port == 8443 and r.headers["private-token"] == "glpat-x" for r in seen)
    assert any(r.url.raw_path == f"/gitlab/api/v4/projects/{ENCODED}/issues".encode() for r in seen)


async def test_the_oldest_supported_version_is_15_0() -> None:
    assert MIN_GITLAB_VERSION == (15, 0)
    ok = await GitLabTracker(served({}, instance("15.0.0")), "ops/billing").test()
    assert ok["ok"] is True
    old = await GitLabTracker(served({}, instance("14.10.5-ee")), "ops/billing").test()
    assert old["ok"] is False and "15.0" in old["reason"] and "14.10.5-ee" in old["reason"]
    # The same gate holds for the SCM and the CI.
    assert (await GitLabScm(served({}, instance("13.12.0")), "ops/billing").test())["ok"] is False
    assert (await GitLabCi(served({}, instance("9.9.9")), "ops/billing").test())["ok"] is False


async def test_a_refused_token_and_an_unreachable_host_are_verdicts_not_crashes() -> None:
    refused = served({}, lambda r: httpx.Response(401, json={"message": "401 Unauthorized"}))
    assert (await GitLabTracker(refused, "ops/billing").test())["reason"].startswith("401")

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate")

    verdict = await GitLabTracker(served({}, boom), "ops/billing").test()
    assert verdict["ok"] is False and "CERTIFICATE_VERIFY_FAILED" in verdict["reason"]


def test_webhook_verification_is_unchanged_on_a_self_managed_instance() -> None:
    tracker = GitLabTracker(
        build_client({"token": "t", "base_url": PREFIXED}), "ops/billing", webhook_secret="s"
    )
    assert tracker.verify_webhook({"X-Gitlab-Token": "s"}, b"{}")
    assert not tracker.verify_webhook({"X-Gitlab-Token": "wrong"}, b"{}")
    assert not tracker.verify_webhook({}, b"{}")


def _pem(path: Path) -> Path:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Corp Test CA")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return path


def test_a_ca_bundle_is_loaded_and_a_bad_one_is_refused(tmp_path: Path) -> None:
    good = _pem(tmp_path / "ca.pem")
    client = build_client({"token": "t", "base_url": PREFIXED, "ca_bundle": str(good)})
    assert client.base_url == PREFIXED
    with pytest.raises(ConfigurationError, match="not a readable file"):
        build_client({"token": "t", "ca_bundle": str(tmp_path / "missing.pem")})
    junk = tmp_path / "junk.pem"
    junk.write_text("not a certificate")
    with pytest.raises(ConfigurationError, match="no usable certificate"):
        build_client({"token": "t", "ca_bundle": str(junk)})


def test_the_registry_builds_the_three_adapters_from_a_self_managed_config(tmp_path: Path) -> None:
    from tranibor_adapters import registry

    ca = str(_pem(tmp_path / "ca.pem"))
    cfg = {"base_url": PREFIXED + "/", "project": "ops/billing", "token": "t", "ca_bundle": ca}
    for family, name in (("tracker", "gitlab-issues"), ("scm", "gitlab"), ("ci", "gitlab")):
        registry.validate_config(family, name, cfg)  # the schema knows `ca_bundle`
        adapter = registry.build(family, name, cfg)
        assert adapter.client.base_url == PREFIXED
        assert adapter.client._headers["PRIVATE-TOKEN"] == "t"
