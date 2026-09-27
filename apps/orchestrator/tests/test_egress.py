"""Le proxy d'egress d'un projet : livré par le provisioning, pas promis par une annotation.

L'état des lieux du 2026-09-24 : la NetworkPolicy des runners ouvrait un namespace
`choregos-egress` qu'aucun chart ne déployait, et l'allowlist par domaine n'était qu'une
annotation. Ces tests lisent les manifests que le provisioning écrit : un Squid dans le
namespace des runners, sa configuration tirée de la politique, la politique réseau qui
n'ouvre Internet qu'à lui, et l'environnement d'un pod d'agent qui le désigne.
"""

from __future__ import annotations

from types import SimpleNamespace

import yaml
from choregos_contracts import ProjectConfig, RepoConfig
from choregos_core import load_preset
from choregos_orchestrator.activities.stage import env_du_runner
from choregos_orchestrator.gitops import EGRESS_NAME, KUBE_NS, render_project_manifests, squid_config


def _config() -> ProjectConfig:
    return ProjectConfig(
        slug="billing", org="varga", repo=RepoConfig(url="https://github.com/varga/billing.git")
    )


def _docs(texte: str) -> list[dict]:
    return list(yaml.safe_load_all(texte))


def test_le_proxy_est_rendu_avec_l_allowlist_de_la_politique() -> None:
    files = render_project_manifests("billing", _config(), load_preset("team"))
    assert "egress.yaml" in files
    docs = {(d["kind"], d["metadata"]["name"]): d for d in _docs(files["egress.yaml"])}
    conf = docs[("ConfigMap", EGRESS_NAME)]["data"]["squid.conf"]
    for domaine in load_preset("team").sandbox.network.allow_domains:
        assert f".{domaine}" in conf, domaine
    assert "http_access deny all" in conf
    assert "http_access deny CONNECT !SSL_ports" in conf
    assert "egress.yaml" in _docs(files["kustomization.yaml"])[0]["resources"]


def test_le_proxy_tourne_sans_root_sans_capacite_en_lecture_seule() -> None:
    files = render_project_manifests("billing", _config(), load_preset("team"))
    deploiement = next(d for d in _docs(files["egress.yaml"]) if d["kind"] == "Deployment")
    pod = deploiement["spec"]["template"]["spec"]
    conteneur = pod["containers"][0]
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["automountServiceAccountToken"] is False
    assert conteneur["securityContext"]["readOnlyRootFilesystem"] is True
    assert conteneur["securityContext"]["capabilities"] == {"drop": ["ALL"]}
    assert conteneur["command"][0] == "squid", "sans le point d'entrée de l'image, qui veut root"


def test_seul_le_proxy_sort_vers_internet() -> None:
    files = render_project_manifests("billing", _config(), load_preset("team"))
    netpol = {d["metadata"]["name"]: d for d in _docs(files["netpol.yaml"])}
    regles = netpol["allow-platform-egress"]["spec"]["egress"]
    destinations = [to for regle in regles for to in regle["to"]]
    assert {"podSelector": {"matchLabels": {"app.kubernetes.io/name": EGRESS_NAME}}} in destinations
    espaces = [to.get("namespaceSelector", {}).get("matchLabels", {}).get(KUBE_NS) for to in destinations]
    assert "choregos-egress" not in espaces, "le namespace fantôme n'est plus promis"
    sortie = next(d for d in _docs(files["egress.yaml"]) if d["kind"] == "NetworkPolicy")
    assert sortie["spec"]["podSelector"] == {"matchLabels": {"app.kubernetes.io/name": EGRESS_NAME}}
    bloc = sortie["spec"]["egress"][0]["to"][0]["ipBlock"]
    assert bloc["cidr"] == "0.0.0.0/0" and "10.0.0.0/8" in bloc["except"]


def test_sans_domaine_autorise_pas_de_proxy_ni_de_porte() -> None:
    files = render_project_manifests("billing", _config(), load_preset("regulated"))
    assert "egress.yaml" not in files
    assert "egress.yaml" not in _docs(files["kustomization.yaml"])[0]["resources"]
    netpol = {d["metadata"]["name"]: d for d in _docs(files["netpol.yaml"])}
    destinations = [to for regle in netpol["allow-platform-egress"]["spec"]["egress"] for to in regle["to"]]
    assert not any("podSelector" in to for to in destinations)


def test_la_configuration_normalise_les_domaines() -> None:
    conf = squid_config(["github.com", ".pypi.org", "github.com"])
    assert "acl allowed dstdomain .github.com .pypi.org" in conf


def test_le_pod_d_agent_recoit_le_proxy_de_son_namespace() -> None:
    settings = SimpleNamespace(
        runner_env={"TZ": "UTC"}, runner_egress_proxy="http://choregos-egress.{namespace}.svc:3128"
    )
    env = env_du_runner(settings, "proj-billing-runners")
    assert env["HTTPS_PROXY"] == "http://choregos-egress.proj-billing-runners.svc:3128"
    assert env["https_proxy"] == env["HTTPS_PROXY"]
    assert ".svc" in env["NO_PROXY"] and "127.0.0.1" in env["NO_PROXY"]
    assert env["TZ"] == "UTC"


def test_sans_proxy_configure_l_environnement_reste_celui_du_deploiement() -> None:
    env = env_du_runner(SimpleNamespace(runner_env={"TZ": "UTC"}, runner_egress_proxy=""), "choregos")
    assert env == {"TZ": "UTC"}


def test_le_rappel_interne_ne_passe_jamais_par_le_proxy_d_egress() -> None:
    """`NO_PROXY` doit nommer les hôtes que la PLATEFORME donne au runner, pas un suffixe.

    Le suffixe `.svc` couvre `choregos-api.choregos.svc` et **pas** `choregos-api` — or c'est
    la forme courte que la plateforme fabrique elle-même (`callback_url`). Sur le locataire dev
    du 2026-09-27, le premier appel du runner (chercher son `StageInput`) partait donc vers le
    proxy d'egress, qui n'existait pas dans ce namespace : « Name or service not known », sans
    que le message nomme ni l'URL ni le proxy. Le runner en concluait « StageInput introuvable ».

    Le proxy existe pour contrôler ce qui SORT. Le rappel interne ne sort pas.
    """
    settings = SimpleNamespace(
        runner_env={},
        runner_egress_proxy="http://choregos-egress.{namespace}.svc:3128",
        callback_url="http://choregos-api:8000",
        gateway_url="http://litellm:4000",
        memory_url="http://ecphoria:8432",
    )
    sans_proxy = env_du_runner(settings, "choregos")["NO_PROXY"].split(",")
    for hote in ("choregos-api", "litellm", "ecphoria"):
        assert hote in sans_proxy, f"{hote} passerait par le proxy d'egress ({sans_proxy})"


def test_les_hotes_directs_viennent_des_url_de_la_plateforme() -> None:
    """Aucun nom n'est deviné : un changement d'URL suit, sans toucher à cette liste."""
    from choregos_orchestrator.activities.execution import hotes_a_joindre_en_direct

    hotes = hotes_a_joindre_en_direct(
        SimpleNamespace(
            # Un hôte SANS point ni port collé à « api » : `api.x:8443/api/v1/...` déclenche la
            # règle générique de gitleaks, qui y voit une clé. Le test porte sur l'extraction
            # d'un hôte, pas sur la forme du nom.
            callback_url="https://interne-exemple:8443/api/v1/internal",
            gateway_url="http://passerelle:4000",
            memory_url="",
        )
    )
    assert hotes == ["interne-exemple", "passerelle"], hotes
