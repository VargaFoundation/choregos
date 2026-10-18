"""L'exécuteur Docker local : les commandes qu'il lance, sans démon Docker."""

from __future__ import annotations

import io
import json
import tarfile
from typing import Any

import pytest
from choregos_adapters.errors import UpstreamError
from choregos_adapters.executor.local_docker import LocalDockerExecutor
from choregos_core.domain import ExecRef, StageJobSpec


class _Docker:
    """Un `docker` simulé : `reponses` par sous-commande, `appels` enregistrés."""

    def __init__(self, **reponses: tuple[int, str, str]) -> None:
        self.reponses = reponses
        self.appels: list[list[str]] = []
        self.envs: list[dict[str, str] | None] = []

    async def __call__(
        self, *args: str, timeout: float = 120.0, env: dict[str, str] | None = None
    ) -> tuple[int, str, str]:
        self.appels.append(list(args))
        self.envs.append(env)
        return self.reponses.get(args[0], (0, "", ""))


def executeur(docker: _Docker) -> LocalDockerExecutor:
    ex = LocalDockerExecutor(network="choregos_net")
    ex._run = docker  # type: ignore[method-assign]
    return ex


def spec() -> StageJobSpec:
    return StageJobSpec(
        run_id="R-1",
        project_slug="billing",
        namespace="local",
        runner_image="runner:dev",
        api_url="http://api:8000",
        run_token="jeton",
        env={"HTTPS_PROXY": "http://p:3128"},
    )


async def test_start_runs_the_container_with_the_same_variables_as_in_a_cluster() -> None:
    docker = _Docker(inspect=(1, "", "no such container"))
    ref = await executeur(docker).start(spec())
    assert ref.name == "choregos-run-r-1"
    run = next(a for a in docker.appels if a[0] == "run")
    assert "--network" in run and run[run.index("--network") + 1] == "choregos_net"
    envs = [run[i + 1] for i, a in enumerate(run) if a == "--env"]
    assert "CHOREGOS_RUN_TOKEN" in envs and "HTTPS_PROXY=http://p:3128" in envs
    assert run[-2:] == ["runner:dev", "run"]


async def test_the_run_secrets_are_named_on_the_command_line_and_never_valued_there() -> None:
    """#439: `ps` on the host shows argv; the values travel in docker's own environment."""
    docker = _Docker(inspect=(1, "", "no such container"))
    secret = spec().model_copy(update={"gateway_key": "gw-secret", "remote_token": "remote-secret"})
    await executeur(docker).start(secret)
    index = next(i for i, a in enumerate(docker.appels) if a[0] == "run")
    argv = " ".join(docker.appels[index])
    for value in ("jeton", "gw-secret", "remote-secret"):  # lang: fr-ok
        assert value not in argv
    env = docker.envs[index] or {}
    assert env["CHOREGOS_RUN_TOKEN"] == "jeton"  # lang: fr-ok
    assert set(env.values()) == {"jeton", "gw-secret", "remote-secret"}  # lang: fr-ok
    named = [docker.appels[index][i + 1] for i, a in enumerate(docker.appels[index]) if a == "--env"]
    assert set(env) <= set(named)


async def test_docker_receives_the_environment_it_is_given_on_top_of_its_own() -> None:
    """The real subprocess call: without `env=`, `--env NAME` would hand the container nothing."""
    ex = LocalDockerExecutor(docker="sh")
    script = 'echo "$CHOREGOS_RUN_TOKEN:${PATH:+path}"'
    code, stdout, _ = await ex._run("-c", script, env={"CHOREGOS_RUN_TOKEN": "t"})
    assert code == 0 and stdout.strip() == "t:path"


async def test_starting_a_run_removes_the_finished_containers_beyond_the_newest_ten() -> None:
    """#439: every run used to leave its container, and its workspace, behind for good."""
    names = "\n".join(f"choregos-run-{i}" for i in range(12))
    docker = _Docker(inspect=(1, "", "no such container"), ps=(0, names, ""))
    await executeur(docker).start(spec())
    ps = next(a for a in docker.appels if a[0] == "ps")
    assert "label=choregos.run-id" in ps and "status=exited" in ps
    assert ["rm", "--force", "choregos-run-10", "choregos-run-11"] in docker.appels
    assert docker.appels[-1][0] == "run"


async def test_a_failed_listing_or_few_finished_containers_remove_nothing() -> None:
    for ps in ((1, "", "denied"), (0, "choregos-run-a\nchoregos-run-b", "")):
        docker = _Docker(inspect=(1, "", "no such container"), ps=ps)
        await executeur(docker).start(spec())
        assert not [a for a in docker.appels if a[0] == "rm"]
        assert docker.appels[-1][0] == "run"


async def test_demarrer_est_idempotent_si_le_conteneur_existe() -> None:
    docker = _Docker(inspect=(0, "[]", ""))
    await executeur(docker).start(spec())
    assert [a[0] for a in docker.appels] == ["inspect"]


async def test_un_echec_de_demarrage_est_nomme() -> None:
    docker = _Docker(inspect=(1, "", ""), run=(125, "", "image not found"))
    with pytest.raises(UpstreamError, match="image not found"):
        await executeur(docker).start(spec())


@pytest.mark.parametrize(
    ("etat", "attendu"),
    [
        ({"Running": True}, "running"),
        ({"Running": False, "ExitCode": 0}, "succeeded"),
        ({"Running": False, "ExitCode": 2, "Error": "boom"}, "failed"),
    ],
)
async def test_l_etat_vient_de_docker_inspect(etat: dict[str, Any], attendu: str) -> None:
    docker = _Docker(inspect=(0, json.dumps([{"State": etat}]), ""))
    statut = await executeur(docker).status(ExecRef(kind="local_docker", name="c"))
    assert statut.state == attendu
    if attendu == "failed":
        assert statut.exit_code == 2 and statut.message == "boom"


def _tar(contenu: bytes) -> bytes:
    """What `docker cp <container>:<file> -` writes on stdout: a tar holding the file."""
    tampon = io.BytesIO()
    with tarfile.open(fileobj=tampon, mode="w") as archive:
        info = tarfile.TarInfo("result.json")
        info.size = len(contenu)
        archive.addfile(info, io.BytesIO(contenu))
    return tampon.getvalue()


def lecteur(code: int, sortie: bytes) -> tuple[LocalDockerExecutor, list[list[str]]]:
    ex = LocalDockerExecutor(network="choregos_net")
    appels: list[list[str]] = []

    async def brut(*args: str, timeout: float = 120.0) -> tuple[int, bytes, str]:
        appels.append(list(args))
        return code, sortie, ""

    ex._run_raw = brut  # type: ignore[method-assign]
    return ex, appels


async def test_the_result_is_read_from_the_stopped_container_and_bad_json_gives_none() -> None:
    """`docker exec` needs a running container; the result is read once the run has ENDED."""
    bon = json.dumps({"schema": "choregos/StageResult/v1", "status": "done", "summary": "ok"}).encode()
    ex, appels = lecteur(0, _tar(bon))
    resultat = await ex.fetch_result(ExecRef(kind="local_docker", name="c"))
    assert resultat is not None and resultat.status == "done"
    assert appels == [["cp", "c:/workspace/.choregos/result.json", "-"]]
    ex, _ = lecteur(0, _tar(b"{not json"))
    assert await ex.fetch_result(ExecRef(kind="local_docker", name="c")) is None
    ex, _ = lecteur(1, b"")
    assert await ex.fetch_result(ExecRef(kind="local_docker", name="c")) is None


async def test_the_run_container_drops_capabilities_and_waits_for_the_image_pull() -> None:
    docker = _Docker(inspect=(1, "", "no such container"))
    timeouts: list[float] = []

    async def enregistre(
        *args: str, timeout: float = 120.0, env: dict[str, str] | None = None
    ) -> tuple[int, str, str]:
        timeouts.append(timeout)
        return await docker(*args, timeout=timeout, env=env)

    ex = LocalDockerExecutor(network="choregos_net")
    ex._run = enregistre  # type: ignore[method-assign]
    await ex.start(spec())
    run = next(a for a in docker.appels if a[0] == "run")
    assert run[run.index("--cap-drop") + 1] == "ALL"
    assert run[run.index("--security-opt") + 1] == "no-new-privileges"
    assert max(timeouts) >= 600, "a first pull of the runner image takes minutes"


async def test_les_journaux_et_l_annulation() -> None:
    docker = _Docker(logs=(0, "a\nb\n", "c"))
    ex = executeur(docker)
    assert [ligne async for ligne in ex.logs(ExecRef(kind="local_docker", name="c"))] == ["a", "b", "c"]
    await ex.cancel(ExecRef(kind="local_docker", name="c"))
    assert docker.appels[-1] == ["rm", "-f", "c"]
