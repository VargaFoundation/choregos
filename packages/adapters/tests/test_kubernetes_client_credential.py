"""S65-03: the pod's service-account token goes to its own cluster's API server and nowhere else.

`KubernetesClient` fell back to the mounted service-account token whenever it was given none,
whatever the address it was pointed at. The token is this cluster's credential: another API server
is spoken to with the token the caller gave, or with none.
"""

from __future__ import annotations

import pytest
from tranibor_adapters.executor import tekton
from tranibor_adapters.executor.tekton import KubernetesClient, is_own_cluster

from ._transport import Wire

MOUNTED = "the-pod-s-own-token"


@pytest.fixture
def mounted_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tekton, "_in_cluster_token", lambda: MOUNTED)
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)


async def _authorization(client: KubernetesClient, wire: Wire) -> str | None:
    await client.request("GET", "/api/v1/namespaces")
    return wire.requests[-1].headers.get("authorization")


@pytest.mark.usefixtures("mounted_token")
@pytest.mark.parametrize(
    "base_url",
    [
        "https://kubernetes.default.svc",
        "https://kubernetes.default.svc/",
        "https://kubernetes.default.svc.cluster.local:443",
        "https://kubernetes.default",
        "https://KUBERNETES.default.svc",
    ],
)
async def test_its_own_api_server_gets_the_mounted_token(base_url: str) -> None:
    wire = Wire()
    assert await _authorization(KubernetesClient(base_url, client=wire.client()), wire) == f"Bearer {MOUNTED}"


@pytest.mark.usefixtures("mounted_token")
async def test_the_default_address_is_its_own_api_server() -> None:
    wire = Wire()
    assert await _authorization(KubernetesClient(client=wire.client()), wire) == f"Bearer {MOUNTED}"


@pytest.mark.usefixtures("mounted_token")
@pytest.mark.parametrize(
    "base_url",
    [
        "https://k8s.elsewhere.example",
        "https://kubernetes.default.svc.elsewhere.example",
        "https://not-kubernetes.default.svc",
        "https://10.0.0.1:6443",
        "http://127.0.0.1:8001",
    ],
)
async def test_another_api_server_never_gets_the_mounted_token(base_url: str) -> None:
    wire = Wire()
    client = KubernetesClient(base_url, client=wire.client())
    assert client.token is None
    assert await _authorization(client, wire) is None


@pytest.mark.usefixtures("mounted_token")
async def test_another_api_server_gets_the_token_it_was_given() -> None:
    wire = Wire()
    client = KubernetesClient("https://k8s.elsewhere.example", token="its-own", client=wire.client())
    assert await _authorization(client, wire) == "Bearer its-own"


@pytest.mark.usefixtures("mounted_token")
async def test_the_address_the_kubelet_gives_the_pod_is_its_own_api_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
    wire = Wire()
    assert await _authorization(KubernetesClient("https://10.96.0.1:443", client=wire.client()), wire) == (
        f"Bearer {MOUNTED}"
    )
    assert not is_own_cluster("https://10.96.0.10:443")


def test_an_address_without_a_host_is_not_its_own_api_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "")
    assert not is_own_cluster("")
    assert not is_own_cluster("kubernetes.default.svc")  # no scheme: no host to read
