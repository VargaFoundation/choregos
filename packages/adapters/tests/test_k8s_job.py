"""Exécuteur `k8s_job` : ce que le Job porte, vérifié sans cluster."""

from __future__ import annotations

from typing import Any

import pytest
from choregos_adapters.executor.k8s_job import RUNNER_POD_LABELS, KubernetesJobExecutor
from choregos_adapters.registry import default_executor_kind
from choregos_contracts import ExecutorKind
from choregos_core.domain import ExecRef, StageJobSpec


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs.get("json")))
        return None  # rien n'existe encore : le Job et son secret sont créés


def _spec() -> StageJobSpec:
    return StageJobSpec(
        run_id="r-1",
        project_slug="demo",
        namespace="choregos",
        runner_image="harbor.example/ghcr-proxy/vargafoundation/choregos-runner:0.1.0",
        api_url="http://choregos-api:8000",
        run_token="jeton",
    )


async def test_le_pod_runner_porte_un_label_stable_pour_les_politiques_reseau() -> None:
    """Les politiques réseau sélectionnent les runners par famille : sans label stable,
    un pod runner ne sort vers rien — ou il faut ouvrir tout le namespace."""
    client = _RecordingClient()
    await KubernetesJobExecutor(client=client).start(_spec())  # type: ignore[arg-type]
    job = next(body for method, path, body in client.calls if method == "POST" and path.endswith("/jobs"))
    labels = job["spec"]["template"]["metadata"]["labels"]
    assert labels["choregos/run-id"] == "r-1"
    assert RUNNER_POD_LABELS.items() <= labels.items()


async def test_le_pod_runner_ne_monte_aucun_jeton_kubernetes() -> None:
    client = _RecordingClient()
    await KubernetesJobExecutor(client=client).start(_spec())  # type: ignore[arg-type]
    job = next(body for method, path, body in client.calls if method == "POST" and path.endswith("/jobs"))
    pod = job["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert pod["serviceAccountName"] == "choregos-runner"


async def test_les_limites_du_pod_runner_suivent_la_configuration() -> None:
    """Sous un LimitRange à 4 Gi par conteneur, un plafond de 6 Gi fait refuser le pod."""
    client = _RecordingClient()
    executor = KubernetesJobExecutor(client=client, cpu_limit="1500m", memory_limit="4Gi")  # type: ignore[arg-type]
    await executor.start(_spec())
    job = next(body for method, path, body in client.calls if method == "POST" and path.endswith("/jobs"))
    limits = job["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]
    assert limits == {"cpu": "1500m", "memory": "4Gi"}


def test_un_projet_sans_runtime_prend_l_executeur_du_deploiement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHOREGOS_EXECUTOR_KIND", "k8s_job")
    assert default_executor_kind() == "k8s_job"
    monkeypatch.delenv("CHOREGOS_EXECUTOR_KIND")
    assert default_executor_kind() == "tekton"


def test_la_memoire_prend_l_url_et_le_jeton_du_deploiement(monkeypatch: pytest.MonkeyPatch) -> None:
    """`CHOREGOS_MEMORY_URL` était posé par le chart et ignoré : chaque projet visait
    `ecphoria.choregos-memory`, un service qui n'existe que dans une installation."""
    from choregos_adapters import build

    monkeypatch.delenv("CHOREGOS_FAKES", raising=False)  # la CI tourne en fakes
    monkeypatch.setenv("CHOREGOS_MEMORY_URL", "http://ecphoria:8432")
    monkeypatch.setenv("CHOREGOS_MEMORY_TOKEN", "cle")
    memory = build("memory", "ecphoria", {})
    assert memory.base_url == "http://ecphoria:8432"
    assert memory.token == "cle"


async def test_les_secrets_d_agent_sont_montes_par_reference() -> None:
    """Par `envFrom`, jamais recopiés : une valeur dans la spec du Job serait lisible par
    quiconque peut lire un pod, et resterait dans l'historique de l'API."""
    client = _RecordingClient()
    executor = KubernetesJobExecutor(client=client, env_from_secrets=["agent-creds", "git-creds"])  # type: ignore[arg-type]
    await executor.start(_spec())
    job = next(body for method, path, body in client.calls if method == "POST" and path.endswith("/jobs"))
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert container["envFrom"] == [
        {"secretRef": {"name": "agent-creds"}},
        {"secretRef": {"name": "git-creds"}},
    ]
    assert not any("agent-creds" in str(entry.get("value", "")) for entry in container["env"])


async def test_sans_secret_declare_le_pod_n_a_pas_d_envfrom() -> None:
    client = _RecordingClient()
    await KubernetesJobExecutor(client=client).start(_spec())  # type: ignore[arg-type]
    job = next(body for method, path, body in client.calls if method == "POST" and path.endswith("/jobs"))
    assert "envFrom" not in job["spec"]["template"]["spec"]["containers"][0]


async def test_la_passerelle_directe_rend_la_cle_fournie_et_ne_mesure_rien() -> None:
    """Sans passerelle, il n'y a ni plafond ni coût : `spend()` rend zéro, qui veut dire
    « non mesuré » — le tableau des coûts affichera 0, et c'est la contrepartie assumée."""
    from choregos_adapters.gateway.direct import DirectGateway

    gateway = DirectGateway(key="jeton-fourni", models=["anthropic/claude-sonnet-5"])
    minted = await gateway.mint_key({"run": "r-1"}, budget_usd=1.0, ttl_s=60, models=["m"])
    assert minted.key == "jeton-fourni"
    assert (await gateway.spend(minted.key_id)).cost_usd == 0.0
    assert [m.model_name for m in await gateway.list_models()] == ["anthropic/claude-sonnet-5"]


def test_sans_cle_de_run_le_backend_ne_touche_pas_aux_variables_d_authentification() -> None:
    """L'agent apporte alors ses propres identifiants (abonnement) : écrire une clé vide
    les écraserait, et l'échec ressemblerait à un refus du fournisseur."""
    from choregos_contracts import ModelRef
    from choregos_runner.backends.base import Backend

    model = ModelRef(litellm_model="anthropic/claude-sonnet-5", base_url="https://api.anthropic.com")
    assert Backend.anthropic_env(model, None) == {"ANTHROPIC_MODEL": "anthropic/claude-sonnet-5"}
    assert Backend.openai_env(model, None) == {"OPENAI_MODEL": "anthropic/claude-sonnet-5"}
    avec = Backend.anthropic_env(model, "sk-run")
    assert avec["ANTHROPIC_AUTH_TOKEN"] == "sk-run"


async def test_la_passerelle_directe_donne_un_identifiant_de_cle_par_run() -> None:
    """`key_id` porte un index unique en base : un identifiant constant ferait échouer le
    deuxième run sur une violation de contrainte."""
    from choregos_adapters.gateway.direct import DirectGateway

    gateway = DirectGateway(key="jeton")
    une = await gateway.mint_key({"run_id": "r-1"}, budget_usd=1, ttl_s=60, models=[])
    deux = await gateway.mint_key({"run_id": "r-2"}, budget_usd=1, ttl_s=60, models=[])
    sans = await gateway.mint_key({}, budget_usd=1, ttl_s=60, models=[])
    assert une.key_id != deux.key_id
    assert sans.key_id.startswith("direct:")


async def test_un_run_rejoue_reecrit_son_jeton() -> None:
    """Le nom du secret est déterministe : rejouer un run retombe dessus. Le laisser tel
    quel ferait tourner l'agent avec un jeton périmé, et l'API répondrait « Signature
    verification failed » — un message qui accuse la signature, pas le secret."""

    class _AvecSecret(_RecordingClient):
        async def request(self, method: str, path: str, **kwargs: Any) -> Any:
            await super().request(method, path, **kwargs)
            return {"kind": "Secret"} if method == "GET" and "/secrets/" in path else None

    client = _AvecSecret()
    await KubernetesJobExecutor(client=client).start(_spec())  # type: ignore[arg-type]
    ecritures = [(m, p) for m, p, _ in client.calls if m in {"PUT", "POST"} and "/secrets" in p]
    assert ecritures and ecritures[0][0] == "PUT"


class _ClusterClient:
    """Un cluster de poche : il garde les Jobs créés et répond aux listes et aux patchs."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self.horloge = 0

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        body = kwargs.get("json")
        if method == "POST" and path.endswith("/jobs"):
            self.horloge += 1
            body["metadata"]["creationTimestamp"] = f"2026-09-24T10:00:{self.horloge:02d}Z"
            body.setdefault("status", {})
            self.jobs[body["metadata"]["name"]] = body
            return body
        if method == "GET" and path.endswith("/jobs"):
            return {"items": list(self.jobs.values())}
        if path.rsplit("/", 1)[-1] in self.jobs and "/jobs/" in path:
            nom = path.rsplit("/", 1)[-1]
            if method == "GET":
                return self.jobs[nom]
            if method == "PATCH":
                assert kwargs["headers"]["Content-Type"] == "application/merge-patch+json"
                self.jobs[nom]["spec"].update(body["spec"])
                return self.jobs[nom]
        return None

    def actifs(self) -> list[str]:
        return [n for n, j in self.jobs.items() if not j["spec"].get("suspend")]


def _spec_pour(run_id: str) -> StageJobSpec:
    spec = _spec()
    return spec.model_copy(update={"run_id": run_id})


async def test_au_dela_du_plafond_le_job_attend_sans_pod() -> None:
    """Dix tickets qui démarrent ensemble font dix pods qui tirent la même image en même
    temps, et un quota de namespace qui refuse le onzième sans rien expliquer. Un Job
    `suspend: true` n'a AUCUN pod : il attend, visible, sans rien consommer."""
    client = _ClusterClient()
    executeur = KubernetesJobExecutor(client=client, max_active=2)  # type: ignore[arg-type]
    for i in range(4):
        await executeur.start(_spec_pour(f"r-{i}"))

    assert client.actifs() == ["run-r-0", "run-r-1"], client.actifs()
    assert all(client.jobs[f"run-r-{i}"]["spec"]["suspend"] for i in (2, 3))

    etat = await executeur.status(
        ExecRef(kind=ExecutorKind.K8S_JOB, name="run-r-2", namespace="choregos", run_id="r-2")
    )
    assert etat.state == "pending"
    assert "en attente d'une place" in etat.message


async def test_la_file_avance_dans_l_ordre_d_arrivee() -> None:
    """Sans ordre, le dernier posé passerait devant à chaque relevé et un run malchanceux
    attendrait indéfiniment."""
    client = _ClusterClient()
    executeur = KubernetesJobExecutor(client=client, max_active=2)  # type: ignore[arg-type]
    for i in range(4):
        await executeur.start(_spec_pour(f"r-{i}"))

    client.jobs["run-r-0"]["status"] = {"succeeded": 1}  # une place se libère

    # Le dernier arrivé demande son état : il ne doit PAS passer devant le troisième.
    dernier = ExecRef(kind=ExecutorKind.K8S_JOB, name="run-r-3", namespace="choregos", run_id="r-3")
    assert "en attente" in (await executeur.status(dernier)).message
    assert client.jobs["run-r-3"]["spec"]["suspend"]

    troisieme = ExecRef(kind=ExecutorKind.K8S_JOB, name="run-r-2", namespace="choregos", run_id="r-2")
    await executeur.status(troisieme)
    assert not client.jobs["run-r-2"]["spec"].get("suspend"), "le plus ancien en attente passe"


async def test_sans_plafond_rien_ne_change() -> None:
    """Un déploiement qui ne demande rien ne change pas de régime du jour au lendemain."""
    client = _ClusterClient()
    executeur = KubernetesJobExecutor(client=client)  # type: ignore[arg-type]
    for i in range(5):
        await executeur.start(_spec_pour(f"r-{i}"))
    assert len(client.actifs()) == 5
