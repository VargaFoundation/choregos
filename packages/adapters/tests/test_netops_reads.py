"""The netops connectors read (S33-08, ADR 0052): the REAL connector, against a fake speaking the
real protocol — pfrest v2 for pfSense, the Kubernetes API for Cilium, `hubble observe -o jsonpb`
for the flows, the forge's API for nginx.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import ssl
import stat
from pathlib import Path
from typing import Any

import httpx
import pytest
from choregos_adapters.errors import AdapterError, ConfigurationError, UpstreamError
from choregos_adapters.fakes.netops import DROPPED_FLOW_TRACE, FakeCiliumKubernetes, FakePfSense, FakeRepo
from choregos_adapters.netops import (
    CiliumNetwork,
    FlowFilter,
    GitHubRepoReader,
    HubbleCli,
    NginxConfig,
    PfSenseFirewall,
    ReadOnlyKubernetes,
    RecordedFlows,
    tls_verification,
)

#: Built at run time: no key-shaped literal for the secret scanner to argue about.
KEY = "-".join(("pfsense", "test", "key", "4c1e"))


def _pfsense(fake: FakePfSense, key: str = KEY) -> PfSenseFirewall:
    return PfSenseFirewall(base_url="https://fw.example.test", api_key=key, transport=fake.transport())


# ───────────────────────────── pfSense ─────────────────────────────


async def test_pfsense_reads_rules_with_the_key_in_its_header_and_hands_back_data_only() -> None:
    fake = FakePfSense(api_key=KEY)
    rules = await _pfsense(fake).executer("list_firewall_rules", {"interface": "wan"})
    assert [r["descr"] for r in rules["rules"]] == ["WAN to the reverse proxy", "Default deny on WAN"]
    assert rules["count"] == 2
    assert "response_id" not in json.dumps(rules), "the envelope stays in the connector"
    assert KEY not in json.dumps(rules)
    assert fake.received == [("GET", "/firewall/rules")]


@pytest.mark.parametrize(
    ("operation", "field", "expected"),
    [
        ("list_aliases", "aliases", 2),
        ("list_port_forwards", "port_forwards", 1),
        ("list_interfaces", "interfaces", 2),
        ("gateway_status", "gateways", 1),
    ],
)
async def test_pfsense_reads_each_declared_collection(operation: str, field: str, expected: int) -> None:
    result = await _pfsense(FakePfSense()).executer(operation, {})
    assert len(result[field]) == expected


async def test_pfsense_refuses_a_wrong_key_and_an_undeclared_operation() -> None:
    fake = FakePfSense(api_key=KEY)
    with pytest.raises(UpstreamError) as refused:
        await _pfsense(fake, "someone-else").executer("list_aliases", {})
    assert refused.value.status_code == 401
    with pytest.raises(AdapterError, match="no operation"):
        await _pfsense(fake).executer("delete_everything", {})
    with pytest.raises(AdapterError, match="no API key"):
        PfSenseFirewall(base_url="https://fw", api_key="")


async def test_pfsense_alias_filter_is_sent_as_a_query() -> None:
    result = await _pfsense(FakePfSense()).list_aliases(name="admin_ports")
    assert [a["name"] for a in result["aliases"]] == ["admin_ports"]


# ───────────────────────────── TLS ─────────────────────────────


def _ca_pem() -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "netops test CA")])
    now = dt.datetime.now(dt.UTC)
    certificate = (
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
    return certificate.public_bytes(serialization.Encoding.PEM).decode()


def test_tls_is_verified_by_default_and_trusts_the_ca_it_is_given() -> None:
    assert tls_verification("pfsense", {}) is True
    context = tls_verification("pfsense", {"ca_cert": _ca_pem()})
    assert isinstance(context, ssl.SSLContext) and context.verify_mode == ssl.CERT_REQUIRED
    with pytest.raises(ConfigurationError, match="not a valid PEM"):
        tls_verification(
            "pfsense", {"ca_cert": "-----BEGIN CERTIFICATE-----\nnope\n-----END CERTIFICATE-----"}
        )


@pytest.mark.parametrize("environment", ["staging", "prod"])
def test_turning_tls_verification_off_is_refused_in_staging_and_prod(
    environment: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHOREGOS_ENV", environment)
    with pytest.raises(ConfigurationError, match="refused"):
        PfSenseFirewall.from_config({"base_url": "https://fw", "api_key": KEY, "verify_tls": False})


def test_turning_tls_verification_off_in_dev_is_said_out_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    from structlog.testing import capture_logs

    monkeypatch.setenv("CHOREGOS_ENV", "dev")
    with capture_logs() as logs:
        firewall = PfSenseFirewall.from_config(
            {"base_url": "https://fw", "api_key": KEY, "verify_tls": False}
        )
    assert firewall.tls_verified is False
    assert [entry["event"] for entry in logs] == ["netops.tls_verification_disabled"]


# ───────────────────────────── Cilium ─────────────────────────────


def _cilium(kubernetes: FakeCiliumKubernetes) -> CiliumNetwork:
    client = ReadOnlyKubernetes("https://k8s.example.test", "reader-token", transport=kubernetes.transport())
    return CiliumNetwork(client, RecordedFlows.from_file(DROPPED_FLOW_TRACE))


async def test_cilium_lists_policies_without_their_managed_fields() -> None:
    cilium = _cilium(FakeCiliumKubernetes(token="reader-token"))
    shop = await cilium.executer("list_network_policies", {"namespace": "shop"})
    assert [p["name"] for p in shop["policies"]] == ["api-ingress", "api-egress"]
    assert shop["policies"][0]["spec"]["ingress"][0]["fromEndpoints"] == [{"matchLabels": {"app": "gateway"}}]
    assert "managedFields" not in json.dumps(shop)
    clusterwide = await cilium.executer("list_clusterwide_policies", {})
    assert [(p["kind"], p["name"]) for p in clusterwide["policies"]] == [
        ("CiliumClusterwideNetworkPolicy", "allow-dns")
    ]


async def test_cilium_is_read_only_by_construction() -> None:
    """No verb but GET leaves the connector: the client has no other method, and the fake saw none."""
    kubernetes = FakeCiliumKubernetes(token="reader-token")
    cilium = _cilium(kubernetes)
    for operation in ("list_network_policies", "list_clusterwide_policies", "get_flows"):
        await cilium.executer(operation, {})
    assert {method for method, _ in kubernetes.received} == {"GET"}
    verbs = {name for name in vars(ReadOnlyKubernetes) if not name.startswith("_")}
    assert verbs == {"get"}
    with pytest.raises(AdapterError, match="never borrows"):
        ReadOnlyKubernetes("https://k8s", "")


async def test_the_recorded_trace_shows_frontend_dropped_by_policy_on_its_way_to_api() -> None:
    cilium = _cilium(FakeCiliumKubernetes())
    dropped = await cilium.executer("get_flows", {"namespace": "shop", "verdict": "DROPPED"})
    assert dropped["count"] == dropped["dropped"] == 3
    flow = dropped["flows"][0]
    assert flow["drop_reason"] == "POLICY_DENIED"
    assert (flow["direction"], flow["protocol"]) == ("INGRESS", "TCP")
    assert flow["source"]["pod"].startswith("frontend-") and "k8s:app=frontend" in flow["source"]["labels"]
    assert flow["destination"]["pod"].startswith("api-") and flow["destination"]["port"] == 8080
    allowed = await cilium.executer("get_flows", {"pod": "gateway", "verdict": "FORWARDED"})
    assert {f["destination"]["port"] for f in allowed["flows"]} == {8080}, "gateway reaches api:8080"
    assert (await cilium.executer("get_flows", {"last": 2}))["count"] == 2


def test_an_unknown_verdict_is_refused_before_hubble_is_asked() -> None:
    with pytest.raises(AdapterError, match="unknown verdict"):
        FlowFilter(verdict="MAYBE")


def _fake_hubble(tmp_path: Path, script: str) -> Path:
    binary = tmp_path / "hubble"
    binary.write_text(script, encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return binary


async def test_hubble_cli_is_called_with_the_filters_and_its_jsonpb_parsed(tmp_path: Path) -> None:
    """The path a real deployment takes: a `hubble` binary, its arguments, its jsonpb on stdout."""
    arguments = tmp_path / "arguments"
    binary = _fake_hubble(
        tmp_path,
        f'#!/bin/sh\nprintf "%s\\n" "$@" > {arguments}\n'
        f'for f in "$@"; do case "$f" in *ca.pem) cp "$f" {tmp_path}/ca-seen.pem;; esac; done\n'
        f"cat {DROPPED_FLOW_TRACE}\n",
    )
    cli = HubbleCli(server="relay.test:443", binary=str(binary), ca_cert="PEM-OF-THE-CA", server_name="relay")
    flows = await cli.flows(FlowFilter(namespace="shop", verdict="DROPPED", since="10m", last=50))
    assert len(flows) == 7, "the CLI filters; the connector parses what it says"
    passed = arguments.read_text(encoding="utf-8").split("\n")
    assert passed[:6] == ["observe", "--server", "relay.test:443", "--output", "jsonpb", "--last"]
    for flag, value in (("--namespace", "shop"), ("--verdict", "DROPPED"), ("--since", "10m")):
        assert passed[passed.index(flag) + 1] == value
    assert "--tls" in passed and passed[passed.index("--tls-server-name") + 1] == "relay"
    assert (tmp_path / "ca-seen.pem").read_text(encoding="utf-8") == "PEM-OF-THE-CA"
    assert not Path(passed[passed.index("--tls-ca-cert-files") + 1]).exists(), "removed after the call"


async def test_hubble_cli_failures_are_named(tmp_path: Path) -> None:
    failing = _fake_hubble(tmp_path, "#!/bin/sh\necho 'connection refused' >&2\nexit 1\n")
    with pytest.raises(UpstreamError, match="connection refused"):
        await HubbleCli(server="relay", binary=str(failing)).flows(FlowFilter())
    with pytest.raises(UpstreamError, match="not installed"):
        await HubbleCli(server="relay", binary=str(tmp_path / "absent")).flows(FlowFilter())


# ───────────────────────────── nginx ─────────────────────────────


async def test_nginx_reads_its_configuration_from_the_repository_and_stays_in_its_root() -> None:
    repo = FakeRepo()
    nginx = NginxConfig(repo, root="nginx")
    listed = await nginx.executer("list_nginx_files", {})
    assert listed["files"] == ["nginx/conf.d/shop.conf", "nginx/nginx.conf"]
    read = await nginx.executer("read_nginx_file", {"path": "conf.d/shop.conf"})
    assert read["path"] == "nginx/conf.d/shop.conf" and "proxy_pass http://shop_api" in read["content"]
    for outside in ("../secrets/tls.key", "/../etc/passwd", "nginx/../../etc/shadow"):
        with pytest.raises(AdapterError, match="outside the configuration root"):
            await nginx.executer("read_nginx_file", {"path": outside})
    assert repo.reads == ["nginx/conf.d/shop.conf"]


async def test_the_github_reader_uses_the_trees_and_contents_api() -> None:
    from choregos_adapters.github import GitHubClient

    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}?{request.url.query.decode()}")
        if "/git/trees/" in request.url.path:
            tree = [{"path": "nginx/nginx.conf", "type": "blob"}, {"path": "README.md", "type": "blob"}]
            return httpx.Response(200, json={"tree": tree})
        content: dict[str, Any] = {"encoding": "base64", "content": base64.b64encode(b"events {}\n").decode()}
        return httpx.Response(200, json=content)

    client = GitHubClient(token="t", client=httpx.AsyncClient(transport=httpx.MockTransport(answer)))
    nginx = NginxConfig(GitHubRepoReader(client, "acme/gitops", "main"))
    assert (await nginx.list_nginx_files())["files"] == ["nginx/nginx.conf"]
    assert (await nginx.read_nginx_file("nginx.conf"))["content"] == "events {}\n"
    assert seen == [
        "GET /repos/acme/gitops/git/trees/main?recursive=1",
        "GET /repos/acme/gitops/contents/nginx/nginx.conf?ref=main",
    ]
