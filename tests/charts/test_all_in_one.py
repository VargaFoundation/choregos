"""S80-18, ADR 0197: the all-in-one profile renders every solution the platform requires.

`charts/choregos/values/all-in-one.yaml` embeds the object store (RustFS) and the OIDC provider (Keycloak)
beside the data stores the chart already embedded, draws every Secret, turns External Secrets off, the egress
proxy and the NetworkPolicies on, and wires the URLs between them. These tests read the RENDER, as a
`helm template` produces it: what they prove is the wiring and the guards, not that the images start (no
cluster ran this, and no Keycloak or RustFS was started).
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"
PROFILE = CHART / "values" / "all-in-one.yaml"
SITE = ["--set", "global.bootstrap.org=acme", "--set", "global.bootstrap.admins=root@acme.example"]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _render(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    command = ["helm", "template", "choregos", str(CHART), "-n", "choregos", *arguments]
    return subprocess.run(command, capture_output=True, text=True, check=check)


def _docs(*arguments: str) -> list[dict[str, Any]]:
    return [d for d in yaml.safe_load_all(_render(*arguments).stdout) if d]


def _aio(*extra: str) -> list[dict[str, Any]]:
    return _docs("-f", str(PROFILE), *SITE, *extra)


def _one(documents: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any]:
    found = [d for d in documents if d["kind"] == kind and d["metadata"]["name"] == name]
    assert len(found) == 1, f"{kind}/{name}: {len(found)} rendered"
    return found[0]


def _names(documents: list[dict[str, Any]], kind: str) -> set[str]:
    return {d["metadata"]["name"] for d in documents if d["kind"] == kind}


def _env(deployment: dict[str, Any], container: int = 0) -> dict[str, Any]:
    env = deployment["spec"]["template"]["spec"]["containers"][container]["env"]
    return {e["name"]: e.get("value", e.get("valueFrom")) for e in env}


def _secret_ref(entry: Any) -> tuple[str, str]:
    ref = entry["secretKeyRef"]
    return ref["name"], ref["key"]


def test_every_piece_the_platform_requires_is_rendered() -> None:
    documents = _aio()
    deployments = _names(documents, "Deployment") | _names(documents, "StatefulSet")
    assert {
        "choregos-api",
        "choregos-web",
        "choregos-postgresql",
        "choregos-temporal",
        "choregos-litellm",
        "choregos-ecphoria",
        "choregos-objectstore",
        "choregos-keycloak",
    } <= deployments
    assert _names(documents, "Job") >= {"choregos-objectstore-bucket"}
    assert _names(documents, "Ingress") >= {"choregos-keycloak"}
    # The scheduled backup, as the single-node class of installation requires.
    assert _names(documents, "CronJob") >= {"choregos-backup"}


def test_every_secret_is_drawn_by_the_chart_and_nothing_asks_a_vault() -> None:
    documents = _aio()
    assert _names(documents, "Secret") >= {
        "choregos-api-secrets",
        "choregos-object-store",
        "choregos-oidc",
        "choregos-keycloak",
        "choregos-postgresql",
        "choregos-gateway",
        "choregos-memory",
    }
    assert not [d for d in documents if d["kind"] == "ExternalSecret"]
    api_secret = _one(documents, "Secret", "choregos-api-secrets")["stringData"]
    assert api_secret["session-secret"] and "PRIVATE KEY" in api_secret["run-token-private-key"]
    # The bench's empty OIDC secret is NOT what the profile gives: it is drawn.
    assert len(_one(documents, "Secret", "choregos-oidc")["stringData"]["client-secret"]) >= 32
    store = _one(documents, "Secret", "choregos-object-store")["stringData"]
    assert store["access-key"] and len(store["secret-key"]) >= 32


def test_the_api_reaches_the_embedded_store_and_the_embedded_issuer() -> None:
    documents = _aio()
    env = _env(_one(documents, "Deployment", "choregos-api"))
    assert env["CHOREGOS_OBJECT_STORE_ENDPOINT"] == "http://choregos-objectstore.choregos.svc:9000"
    assert env["CHOREGOS_OBJECT_STORE_URL"] == "s3://choregos"
    assert _secret_ref(env["CHOREGOS_OBJECT_STORE_ACCESS_KEY"]) == ("choregos-object-store", "access-key")
    assert env["CHOREGOS_OIDC_ISSUER"] == "https://auth.choregos.example.com/realms/choregos"
    assert _secret_ref(env["CHOREGOS_OIDC_CLIENT_SECRET"]) == ("choregos-oidc", "client-secret")
    service = _one(documents, "Service", "choregos-objectstore")
    assert service["spec"]["ports"][0]["port"] == 9000


def test_the_object_store_and_its_bucket_job_use_the_same_keys_as_the_api() -> None:
    documents = _aio()
    store = _one(documents, "Deployment", "choregos-objectstore")
    env = _env(store)
    assert _secret_ref(env["RUSTFS_ACCESS_KEY"]) == ("choregos-object-store", "access-key")
    assert _secret_ref(env["RUSTFS_SECRET_KEY"]) == ("choregos-object-store", "secret-key")
    assert store["spec"]["strategy"]["type"] == "Recreate" and store["spec"]["replicas"] == 1
    claim = store["spec"]["template"]["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"]
    assert claim in _names(documents, "PersistentVolumeClaim")
    job = _one(documents, "Job", "choregos-objectstore-bucket")
    job_env = _env({"spec": job["spec"]})
    assert job_env["RCLONE_CONFIG_S3_ENDPOINT"] == "http://choregos-objectstore.choregos.svc:9000"
    assert _secret_ref(job_env["RCLONE_CONFIG_S3_ACCESS_KEY_ID"]) == ("choregos-object-store", "access-key")
    script = job["spec"]["template"]["spec"]["containers"][0]["args"][0]
    assert 'rclone mkdir "S3:$b"' in script and '"choregos"' in script
    # The backup copies the files the workflows produced.
    backup = _one(documents, "CronJob", "choregos-backup")
    assert "OBJECTS_PATH" in json.dumps(backup)
    assert "http://choregos-objectstore.choregos.svc:9000" in json.dumps(backup)


def test_the_realm_agrees_with_the_api() -> None:
    documents = _aio()
    realm = json.loads(_one(documents, "ConfigMap", "choregos-keycloak-realm")["data"]["realm.json"])
    assert realm["realm"] == "choregos"
    clients = {c["clientId"]: c for c in realm["clients"]}
    console = clients["choregos"]
    assert console["publicClient"] is False
    assert "https://choregos.example.com/api/v1/auth/callback" in console["redirectUris"]
    # The secret is a substitution, never written into the ConfigMap.
    assert console["secret"] == "${CHOREGOS_OIDC_CLIENT_SECRET}"
    assert "choregos-cli" in clients and clients["choregos-cli"]["publicClient"] is True
    mapper = console["protocolMappers"][0]
    assert mapper["config"]["claim.name"] == "groups" and mapper["config"]["full.path"] == "false"
    assert {g["name"] for g in realm["groups"]} >= {"choregos:acme:org-admins", "choregos:acme:viewers"}
    admin = realm["users"][0]
    assert admin["email"] == "root@acme.example" and admin["emailVerified"] is True
    assert admin["groups"] == ["/choregos:acme:org-admins"]
    assert realm["sslRequired"] == "external"
    # Keycloak receives the secret the API reads, and its hostname is the issuer's host.
    keycloak = _one(documents, "Deployment", "choregos-keycloak")
    env = _env(keycloak)
    assert _secret_ref(env["CHOREGOS_OIDC_CLIENT_SECRET"]) == ("choregos-oidc", "client-secret")
    assert _secret_ref(env["CHOREGOS_REALM_ADMIN_PASSWORD"]) == ("choregos-keycloak", "realm-admin-password")
    args = keycloak["spec"]["template"]["spec"]["containers"][0]["args"]
    assert "--hostname=https://auth.choregos.example.com" in args and "--import-realm" in args
    ingress = _one(documents, "Ingress", "choregos-keycloak")
    assert ingress["spec"]["rules"][0]["host"] == "auth.choregos.example.com"


def test_keycloak_uses_the_embedded_database_which_makes_it() -> None:
    documents = _aio()
    env = _env(_one(documents, "Deployment", "choregos-keycloak"))
    assert env["KC_DB_URL_HOST"] == "choregos-postgresql" and env["KC_DB_URL_DATABASE"] == "keycloak"
    assert _secret_ref(env["KC_DB_PASSWORD"]) == ("choregos-postgresql", "password")
    init = _one(documents, "ConfigMap", "choregos-postgresql-init")["data"]
    assert "CREATE DATABASE keycloak" in init["03-keycloak.sql"]


def test_every_new_image_is_pinned_by_tag_and_digest() -> None:
    documents = _aio()
    new = {"choregos-objectstore", "choregos-keycloak", "choregos-objectstore-bucket"}
    seen = 0
    for d in documents:
        if d["metadata"]["name"] not in new or d["kind"] not in ("Deployment", "Job"):
            continue
        spec = d["spec"]["template"]["spec"]
        for container in spec["containers"]:
            image = container["image"]
            repo, _, digest = image.partition("@")
            assert ":" in repo.rsplit("/", 1)[1], f"{image}: no tag"
            assert digest.startswith("sha256:") and len(digest) == 71, f"{image}: no digest"
            seen += 1
    assert seen == 3


def test_the_egress_proxy_and_the_network_policies_are_on_and_the_executor_is_k8s_job() -> None:
    documents = _aio()
    assert {"choregos-egress"} <= _names(documents, "Deployment")
    policies = _names(documents, "NetworkPolicy")
    assert {
        "choregos-default-deny-egress",
        "choregos-runner-egress",
        "choregos-objectstore-ingress",
    } <= policies
    runner = json.dumps(_one(documents, "NetworkPolicy", "choregos-runner-egress"))
    # A runner never reaches the object store or the identity provider.
    assert "choregos-objectstore" not in runner and "choregos-keycloak" not in runner
    store_policy = _one(documents, "NetworkPolicy", "choregos-objectstore-ingress")
    allowed = json.dumps(store_policy["spec"]["ingress"][0])
    assert "choregos-api" in allowed and "choregos-runner" not in allowed
    env = _env(_one(documents, "Deployment", "choregos-api"))
    assert env["CHOREGOS_EXECUTOR_KIND"] == "k8s_job"


@pytest.mark.no_operator
def test_it_renders_on_a_cluster_without_the_agent_sandbox_operator() -> None:
    assert _aio()


def test_the_api_policy_reaches_the_embedded_pieces() -> None:
    documents = _aio()
    egress = json.dumps(_one(documents, "NetworkPolicy", "choregos-allow-platform"))
    assert "choregos-objectstore" in egress and "choregos-keycloak" in egress


def test_a_site_brings_its_own_keycloak_and_its_own_s3() -> None:
    documents = _aio(
        "--set",
        "global.oidc.embedded=false",
        "--set",
        "global.oidc.generateSecret=false",
        "--set",
        "global.oidc.issuer=https://sso.example.org/realms/x",
        "--set",
        "global.objectStore.embedded=false",
        "--set",
        "global.objectStore.generateSecret=false",
        "--set",
        "global.objectStore.endpoint=https://s3.example.org",
        "--set",
        "global.objectStore.secretRef=their-s3",
    )
    names = {d["metadata"]["name"] for d in documents}
    assert not {
        n for n in names if "keycloak" in n or "objectstore" in n or n in ("choregos-oidc", "their-s3")
    }
    env = _env(_one(documents, "Deployment", "choregos-api"))
    assert env["CHOREGOS_OIDC_ISSUER"] == "https://sso.example.org/realms/x"
    assert env["CHOREGOS_OBJECT_STORE_ENDPOINT"] == "https://s3.example.org"
    assert _secret_ref(env["CHOREGOS_OBJECT_STORE_ACCESS_KEY"]) == ("their-s3", "access-key")
    # Still all in one for the rest: the platform's own Secrets are drawn.
    assert "choregos-api-secrets" in names
    # Their own PostgreSQL: the embedded Keycloak needs a database of its own then, and says so.
    assert "choregos-keycloak.database.host" in _refused(*SITE, "--set", "global.database.embedded=false")
    own_db = _aio(
        "--set",
        "global.database.embedded=false",
        "--set",
        "choregos-keycloak.database.host=pg.example.org",
        "--set",
        "choregos-keycloak.database.secretRef=their-pg",
    )
    assert "choregos-postgresql" not in _names(own_db, "StatefulSet")
    kc = _env(_one(own_db, "Deployment", "choregos-keycloak"))
    assert kc["KC_DB_URL_HOST"] == "pg.example.org" and _secret_ref(kc["KC_DB_USERNAME"])[0] == "their-pg"


def test_without_the_mode_nothing_of_it_is_rendered() -> None:
    for arguments in (
        [],
        [
            "-f",
            str(CHART / "values" / "single-node.yaml"),
            "--set",
            "backup.target.s3.url=s3://b/p",
            "--set",
            "backup.target.s3.endpoint=http://s3:1",
        ],
    ):
        documents = _docs(*arguments)
        names = {d["metadata"]["name"] for d in documents}
        assert not {n for n in names if "keycloak" in n or "objectstore" in n or n == "choregos-object-store"}
        assert "choregos-oidc" not in names
        text = json.dumps(documents)
        assert "03-keycloak.sql" not in text
        env = _env(_one(documents, "Deployment", "choregos-api"))
        assert env["CHOREGOS_OIDC_ISSUER"] == "https://keycloak.choregos.local/realms/choregos"
        assert env["CHOREGOS_OBJECT_STORE_ENDPOINT"] == ""
        assert "choregos-api-secrets" not in _names(documents, "Secret")


def _refused(*arguments: str) -> str:
    result = _render("-f", str(PROFILE), *arguments, check=False)
    assert result.returncode != 0
    return result.stderr


def test_the_guards() -> None:
    assert "global.bootstrap.org" in _refused()
    assert "externalSecrets" in _refused(*SITE, "--set", "global.externalSecrets.enabled=true")
    assert "backup.enabled" in _refused(*SITE, "--set", "backup.enabled=false")
    assert "networkPolicies" in _refused(*SITE, "--set", "networkPolicies.enabled=false")
    assert "global.egress.enabled" in _refused(*SITE, "--set", "global.egress.enabled=false")
    assert "global.oidc.secretRef" in _refused(*SITE, "--set", "global.oidc.secretRef=")
    assert "global.objectStore.secretRef" in _refused(*SITE, "--set", "global.objectStore.secretRef=")
    # The bench values stay refused under the mode.
    assert "global.devSecrets" in _refused(*SITE, "--set", "global.devSecrets=true")


def test_the_mode_does_not_relax_the_guard_without_its_switch() -> None:
    values = ["--set", "global.environment=prod", "--set", "global.database.embedded=true"]
    result = _render(*values, check=False)
    assert result.returncode != 0 and "global.database.embedded" in result.stderr
