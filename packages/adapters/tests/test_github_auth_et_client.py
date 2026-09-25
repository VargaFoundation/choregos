"""L'App GitHub et son client : jetons courts et ciblés, quotas respectés, erreurs nommées."""

from __future__ import annotations

import time

import httpx
import jwt
import pytest
from choregos_adapters.errors import UpstreamError
from choregos_adapters.github import client as module_client
from choregos_adapters.github.auth import RUNNER_PERMISSIONS, GitHubAppAuth
from choregos_adapters.github.client import GitHubClient
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from ._transport import Fil, sans_attente

API = "https://api.github.test"


@pytest.fixture(scope="module")
def cle_privee() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    pub = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return pem, pub


def test_le_jwt_d_app_est_court_et_signe_par_la_cle(cle_privee: tuple[str, str]) -> None:
    pem, pub = cle_privee
    token = GitHubAppAuth(app_id="12345", private_key=pem).app_jwt(ttl_s=540)
    claims = jwt.decode(token, pub, algorithms=["RS256"])
    assert claims["iss"] == "12345"
    assert claims["exp"] - claims["iat"] == 600, "540 s de vie + 60 s de dérive d'horloge"


async def test_le_jeton_d_installation_est_cible_sur_un_depot_et_mis_en_cache(
    cle_privee: tuple[str, str],
) -> None:
    pem, _ = cle_privee
    fil = Fil(
        {
            ("GET", "/repos/varga/billing/installation"): (200, {"id": 42}),
            ("POST", "/app/installations/42/access_tokens"): (201, {"token": "ghs_court"}),
        }
    )
    auth = GitHubAppAuth(app_id="1", private_key=pem, base_url=API)
    async with fil.client() as client:
        jeton = await auth.token_for(client, "varga/billing", permissions=RUNNER_PERMISSIONS, ttl_s=900)
        encore = await auth.token_for(client, "varga/billing", permissions=RUNNER_PERMISSIONS)
    assert jeton.token == "ghs_court" and encore is jeton, "même dépôt, mêmes permissions : le cache"
    corps = fil.corps(-1)
    assert corps == {"repositories": ["billing"], "permissions": RUNNER_PERMISSIONS}, (
        "jamais un jeton large : le dépôt et les permissions exactes"
    )
    assert fil.requetes[1].headers["Authorization"].startswith("Bearer ey")
    assert not jeton.expired and jeton.repositories == ("varga/billing",)


async def test_un_jeton_expire_est_redemande(cle_privee: tuple[str, str]) -> None:
    pem, _ = cle_privee
    fil = Fil(
        {("POST", "/app/installations/7/access_tokens"): [(201, {"token": "a"}), (201, {"token": "b"})]}
    )
    auth = GitHubAppAuth(app_id="1", private_key=pem, base_url=API)
    async with fil.client() as client:
        premier = await auth.token_for(client, "varga/x", installation_id=7)
        premier.expires_at = time.time()  # dans la marge de sécurité : périmé
        second = await auth.token_for(client, "varga/x", installation_id=7)
    assert (premier.token, second.token) == ("a", "b")


async def test_le_client_rejoue_les_limites_secondaires_et_les_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    attentes = sans_attente(monkeypatch, module_client)
    fil = Fil(
        {
            ("GET", "/repos/varga/x"): [
                httpx.Response(403, text="You have exceeded a secondary rate limit"),
                httpx.Response(502, text="bad gateway"),
                httpx.Response(429, headers={"retry-after": "3"}, text=""),
                (200, {"full_name": "varga/x"}),
            ]
        }
    )
    client = GitHubClient(token="t", base_url=API, client=fil.client())
    assert await client.request("GET", "/repos/varga/x", repo="varga/x") == {"full_name": "varga/x"}
    assert len(attentes) == 3 and attentes[2] == 3.0
    assert fil.requetes[0].headers["X-GitHub-Api-Version"] == "2022-11-28"


async def test_un_403_ordinaire_est_une_erreur_avec_le_delai_de_reinitialisation() -> None:
    reset = int(time.time()) + 120
    fil = Fil(
        {
            ("GET", "/repos/varga/x"): httpx.Response(
                403, headers={"x-ratelimit-reset": str(reset)}, text="Resource not accessible by integration"
            )
        }
    )
    client = GitHubClient(token="t", base_url=API, client=fil.client())
    with pytest.raises(UpstreamError) as exc:
        await client.request("GET", "/repos/varga/x")
    assert (
        exc.value.status_code == 403
        and exc.value.retry_after is not None
        and 100 <= exc.value.retry_after <= 120
    )
    assert len(fil.requetes) == 1


async def test_sans_authentification_le_client_refuse_avant_d_appeler() -> None:
    fil = Fil()
    with pytest.raises(UpstreamError, match="aucune authentification"):
        await GitHubClient(base_url=API, client=fil.client()).request("GET", "/x")
    assert fil.requetes == []


async def test_graphql_remonte_les_erreurs_du_corps_meme_en_200() -> None:
    fil = Fil(
        {
            ("POST", "/graphql"): [
                (200, {"errors": [{"message": "Could not resolve to a node"}]}),
                (200, {"data": {"node": {"id": "PVT_1"}}}),
            ]
        }
    )
    client = GitHubClient(token="t", base_url=API, graphql_url=f"{API}/graphql", client=fil.client())
    with pytest.raises(UpstreamError, match="Could not resolve"):
        await client.graphql("query { x }", {})
    assert await client.graphql("query { x }", {"a": 1}) == {"node": {"id": "PVT_1"}}
    assert fil.corps(-1) == {"query": "query { x }", "variables": {"a": 1}}


async def test_la_pagination_suit_les_pages_pleines() -> None:
    pleine = [{"n": i} for i in range(100)]
    fil = Fil({("GET", "/repos/varga/x/issues"): [(200, pleine), (200, [{"n": 100}])]})
    client = GitHubClient(token="t", base_url=API, client=fil.client())
    assert (
        len(await client.paginate("/repos/varga/x/issues", repo="varga/x", params={"state": "open"})) == 101
    )
    assert [r.url.params["page"] for r in fil.requetes] == ["1", "2"]
    assert fil.requetes[0].url.params["per_page"] == "100"
