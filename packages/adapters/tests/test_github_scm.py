"""Le SCM GitHub : branches, PR, checks, merge queue — ce qui part sur le fil."""

from __future__ import annotations

import pytest
from choregos_adapters.errors import UpstreamError
from choregos_adapters.github.client import GitHubClient
from choregos_adapters.scm.github import GitHubScm

from ._transport import Fil

API = "https://api.github.test"
REPO = "varga/billing"


def scm(fil: Fil, **kwargs: object) -> GitHubScm:
    client = GitHubClient(token="t", base_url=API, graphql_url=f"{API}/graphql", client=fil.client())
    return GitHubScm(client, default_repo=REPO, **kwargs)  # type: ignore[arg-type]


async def test_une_branche_existante_n_est_pas_recreee() -> None:
    fil = Fil(
        {("GET", f"/repos/{REPO}/git/ref/heads/choregos/12-x"): (200, {"ref": "refs/heads/choregos/12-x"})}
    )
    await scm(fil).ensure_branch(REPO, "choregos/12-x", "main")
    assert fil.envoyees("POST") == []


async def test_une_branche_absente_part_du_sha_de_la_base() -> None:
    fil = Fil(
        {
            ("GET", f"/repos/{REPO}/git/ref/heads/main"): (200, {"object": {"sha": "abc123"}}),
            ("POST", f"/repos/{REPO}/git/refs"): (201, {}),
        }
    )
    await scm(fil).ensure_branch(REPO, "choregos/12-x", "main")
    assert fil.corps(-1) == {"ref": "refs/heads/choregos/12-x", "sha": "abc123"}


async def test_une_panne_sur_la_branche_n_est_pas_prise_pour_une_absence() -> None:
    fil = Fil({("GET", f"/repos/{REPO}/git/ref/heads/x"): (401, {"message": "Bad credentials"})})
    with pytest.raises(UpstreamError):
        await scm(fil).ensure_branch(REPO, "x", "main")
    assert fil.envoyees("POST") == [], "on ne crée pas une branche sur un 401"


async def test_ouvrir_une_pr_reutilise_celle_qui_existe() -> None:
    fil = Fil({("GET", f"/repos/{REPO}/pulls"): (200, [{"number": 7, "html_url": "https://gh/pr/7"}])})
    ref = await scm(fil).open_pr(REPO, "choregos/12-x", "main", "titre", "corps", draft=True)
    assert ref.number == 7 and ref.url == "https://gh/pr/7"
    assert fil.requetes[0].url.params["head"] == "varga:choregos/12-x"
    assert fil.envoyees("POST") == []


async def test_ouvrir_une_pr_en_brouillon_quand_il_n_y_en_a_pas() -> None:
    fil = Fil(
        {("GET", f"/repos/{REPO}/pulls"): (200, []), ("POST", f"/repos/{REPO}/pulls"): (201, {"number": 8})}
    )
    ref = await scm(fil).open_pr(REPO, "h", "main", "titre", "corps", draft=True)
    assert ref.number == 8 and fil.corps(-1)["draft"] is True


async def test_sortir_du_brouillon_passe_par_graphql() -> None:
    fil = Fil(
        {
            ("PATCH", f"/repos/{REPO}/pulls/8"): (200, {}),
            ("GET", f"/repos/{REPO}/pulls/8"): (200, {"node_id": "PR_x"}),
            ("POST", "/graphql"): (200, {"data": {}}),
        }
    )
    from choregos_core.domain import PrRef

    await scm(fil).update_pr(PrRef(repo=REPO, number=8), body="nouveau corps", draft=False)
    assert fil.envoyees() == [
        ("PATCH", f"/repos/{REPO}/pulls/8"),
        ("GET", f"/repos/{REPO}/pulls/8"),
        ("POST", "/graphql"),
    ]
    assert fil.corps(-1)["variables"] == {"id": "PR_x"}
    assert "markPullRequestReadyForReview" in fil.corps(-1)["query"]


async def test_l_etat_d_une_pr_assemble_checks_relectures_et_fichiers() -> None:
    from choregos_core.domain import PrRef

    fil = Fil(
        {
            ("GET", f"/repos/{REPO}/pulls/8"): (
                200,
                {
                    "html_url": "u",
                    "head": {"sha": "s1", "ref": "choregos/12-x"},
                    "base": {"ref": "main"},
                    "title": "T",
                    "body": None,
                    "draft": False,
                    "merged": False,
                    "mergeable": True,
                    "labels": [{"name": "size:M"}],
                },
            ),
            ("GET", f"/repos/{REPO}/commits/s1/check-runs"): (
                200,
                {
                    "check_runs": [
                        {"name": "ci", "status": "completed", "conclusion": "success", "html_url": "c"}
                    ]
                },
            ),
            ("GET", f"/repos/{REPO}/pulls/8/reviews"): (
                200,
                [
                    {"user": {"login": "marie"}, "state": "APPROVED", "body": "ok"},
                    {"user": {"login": "bot"}, "state": "PENDING"},
                ],
            ),
            ("GET", f"/repos/{REPO}/pulls/8/files"): (
                200,
                [{"filename": "src/a.py"}, {"filename": "src/b.py"}],
            ),
        }
    )
    etat = await scm(fil).get_pr(PrRef(repo=REPO, number=8))
    assert etat.head_sha == "s1" and etat.body == "" and etat.labels == ["size:M"]
    assert [c.conclusion for c in etat.checks] == ["success"]
    assert [(r.reviewer, r.state) for r in etat.reviews] == [("marie", "approved")], (
        "PENDING n'est pas une relecture"
    )
    assert etat.files == ["src/a.py", "src/b.py"]


async def test_la_merge_queue_ou_le_merge_direct_selon_la_configuration() -> None:
    from choregos_core.domain import PrRef

    fil = Fil(
        {
            ("GET", f"/repos/{REPO}/pulls/8"): (200, {"node_id": "PR_x"}),
            ("POST", "/graphql"): (200, {"data": {}}),
        }
    )
    await scm(fil).enqueue_merge(PrRef(repo=REPO, number=8))
    assert fil.corps(-1)["variables"] == {"pullRequestId": "PR_x", "method": "SQUASH"}

    direct = Fil(
        {
            ("GET", f"/repos/{REPO}/pulls/8"): (200, {"node_id": "PR_x"}),
            ("PUT", f"/repos/{REPO}/pulls/8/merge"): (200, {}),
        }
    )
    await scm(direct, use_merge_queue=False, merge_method="REBASE").enqueue_merge(PrRef(repo=REPO, number=8))
    assert direct.corps(-1) == {"merge_method": "rebase"}


async def test_le_check_run_de_perimetre_annote_au_plus_cinquante_fichiers() -> None:
    fil = Fil({("POST", f"/repos/{REPO}/check-runs"): (201, {})})
    hors = [f"src/{i}.py" for i in range(80)]
    await scm(fil).create_check_run(
        REPO, "s1", "choregos/scope", "failure", "80 fichiers hors périmètre", hors
    )
    corps = fil.corps(-1)
    assert corps["head_sha"] == "s1" and corps["conclusion"] == "failure" and corps["status"] == "completed"
    assert len(corps["output"]["annotations"]) == 50, "GitHub refuse plus de 50 annotations par appel"
    assert corps["output"]["annotations"][0]["annotation_level"] == "failure"


async def test_la_comparaison_traduit_les_statuts_de_fichiers() -> None:
    fil = Fil(
        {
            ("GET", f"/repos/{REPO}/compare/main...h"): (
                200,
                {
                    "files": [
                        {"filename": "a", "status": "added", "additions": 3, "deletions": 0},
                        {"filename": "b", "status": "copied", "additions": 1, "deletions": 1, "patch": "@@"},
                    ]
                },
            )
        }
    )
    diff = await scm(fil).compare(REPO, "main", "h")
    assert [(f.path, f.status) for f in diff.files] == [("a", "added"), ("b", "modified")]
    assert diff.files[1].patch == "@@"


async def test_les_relecteurs_se_repartissent_entre_personnes_et_equipes() -> None:
    from choregos_core.domain import PrRef

    fil = Fil({("POST", f"/repos/{REPO}/pulls/8/requested_reviewers"): (201, {})})
    await scm(fil).request_review(PrRef(repo=REPO, number=8), ["marie", "varga/maintainers"])
    assert fil.corps(-1) == {"reviewers": ["marie"], "team_reviewers": ["maintainers"]}
    await scm(fil).request_review(PrRef(repo=REPO, number=8), [])
    assert len(fil.requetes) == 1, "sans relecteur, pas d'appel"


async def test_le_jeton_du_runner_est_le_jeton_statique_ou_un_jeton_d_app_reduit() -> None:
    fil = Fil()
    assert await scm(fil).mint_token(REPO, 900, []) == "t"
    sans = GitHubScm(GitHubClient(base_url=API, client=fil.client()))
    with pytest.raises(UpstreamError, match="aucune App"):
        await sans.mint_token(REPO, 900, [])
