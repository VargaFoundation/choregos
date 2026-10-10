"""Policy engine : budgets, tentatives, périmètre, trains (couverture visée : 100 %)."""

from __future__ import annotations

import pathlib

import pytest
import yaml
from choregos_contracts import Policy, Size
from choregos_core import (
    PolicyEngine,
    ValidationError,
    engine_for,
    load_preset,
    parse_policy,
    policy_warnings,
    preset_yaml,
)
from choregos_core.policy import PRESET_NAMES

RACINE = pathlib.Path(__file__).resolve().parents[3]
POLITIQUES_DES_GABARITS = sorted((RACINE / "templates").glob("*/policy.yaml"))


@pytest.mark.parametrize("name", PRESET_NAMES)
def test_presets_parse(name: str) -> None:
    policy = load_preset(name)
    assert policy.metadata.name == name
    assert preset_yaml(name).startswith("#")


def test_budget_for_by_role_and_size() -> None:
    e = PolicyEngine(load_preset("solo"))
    assert e.stage_usd("implement", Size.S) == 5
    assert e.stage_usd("implement", Size.XL) == 45
    assert e.stage_usd("role_inconnu", Size.M) == 8  # retombe sur `default`
    budget = e.budget_for("implement", Size.L)
    assert budget.usd == 25
    assert budget.max_turns == 120
    assert budget.max_minutes == 90


def test_budget_turns_factor() -> None:
    e = PolicyEngine(load_preset("solo"))
    assert e.budget_for("implement", Size.XL, turns_factor=1.5).max_turns == 180


def test_ticket_budget_and_alerts() -> None:
    e = PolicyEngine(load_preset("solo"))
    assert e.budget_ticket(Size.M) == 25
    assert e.budget_ticket(None) == 25
    assert not e.over_ticket_budget(24, Size.M)
    assert e.over_ticket_budget(26, Size.M)
    assert e.should_alert(20, Size.M)
    assert not e.should_alert(19, Size.M)
    assert e.daily_budget() == 150


def test_policy_without_sections_falls_back_on_platform_defaults() -> None:
    policy = Policy.model_validate(
        {"apiVersion": "choregos/v1", "kind": "Policy", "metadata": {"name": "p", "version": 1}}
    )
    e = PolicyEngine(policy)
    assert e.budget_ticket(Size.L) == 60  # défauts de la plateforme
    assert e.stage_usd("implement", Size.S) == 3


def test_attempts_and_dod() -> None:
    e = PolicyEngine(load_preset("solo"))
    assert e.attempts_for("implement") == 3
    assert e.attempts_for("verify") == 2
    assert e.dod_iterations() == 3


def test_scope_auto_grant() -> None:
    e = PolicyEngine(load_preset("solo"))
    assert e.scope_auto_grant(["src/a.py", "src/b.py"])
    assert not e.scope_auto_grant(["a.py", "b.py", "c.py", "d.py"])
    assert not e.scope_auto_grant([".choregos/workflow.yaml"])
    assert e.path_denied(".github/workflows/ci.yml")
    assert not e.path_denied("src/app.py")
    assert e.max_diff() == (60, 3000)
    assert "kubectl" in e.deny_commands()
    assert "github.com" in e.allow_domains()


def test_findings_and_memory() -> None:
    e = PolicyEngine(load_preset("solo"))
    assert e.max_findings_per_run() == 5
    assert e.dedupe_threshold() == 0.86
    assert e.auto_agent_ready(Size.S)
    assert not e.auto_agent_ready(Size.L)
    assert e.notify_finding("critical")
    assert not e.notify_finding("low")
    assert e.memory_enabled()
    assert e.memory_budget("implement") == 4000
    assert e.memory_budget("triage") == 2000


def test_memory_disabled_gives_zero_budget() -> None:
    policy = load_preset("solo").model_copy(deep=True)
    policy.memory.enabled = False
    assert PolicyEngine(policy).memory_budget("implement") == 0


def test_review_policy() -> None:
    assert PolicyEngine(load_preset("team")).cross_backend_review()
    assert not PolicyEngine(load_preset("solo")).cross_backend_review()


# ───────────── approbations et relecture humaine : déclarées dans le workflow (ADR 0044) ─────────────

#: Une politique d'avant l'ADR 0044, telle que les presets l'écrivaient : elle reste valide.
POLITIQUE_D_AVANT = """
apiVersion: choregos/v1
kind: Policy
metadata: { name: ancienne, version: 3 }
approvals:
  spec: { required: by_size, group: product-owners, sizes: [M, L, XL], timeout_hours: 72 }
  merge: { required: never }
  prod: { required: always, group: release-captains, timeout_hours: 4 }
  scope_change: { required: by_risk, group: maintainers, risks: [high] }
review: { cross_backend: true, require_human_for_risk: [medium, high] }
"""


@pytest.mark.parametrize("name", PRESET_NAMES)
def test_un_preset_ne_promet_ni_approbation_ni_relecture_humaine(name: str) -> None:
    """Les presets écrivaient « approbation de la spec », « review humaine au merge », « prod :
    always » : aucune de ces gardes n'existe. Ils ne les écrivent plus, et ne s'avertissent pas."""
    brut = yaml.safe_load(preset_yaml(name))
    assert "approvals" not in brut
    assert "require_human_for_risk" not in (brut.get("review") or {})
    assert policy_warnings(load_preset(name)) == []


@pytest.mark.parametrize("chemin", POLITIQUES_DES_GABARITS, ids=lambda c: c.parent.name)
def test_la_politique_d_un_gabarit_ne_promet_rien_non_plus(chemin: pathlib.Path) -> None:
    texte = chemin.read_text(encoding="utf-8")
    brut = yaml.safe_load(texte)
    assert "approvals" not in brut
    assert "require_human_for_risk" not in (brut.get("review") or {})
    assert policy_warnings(parse_policy(texte)) == []


def test_les_politiques_des_gabarits_sont_trouvees() -> None:
    """Sans elle, le test paramétré ci-dessus ne jouerait aucun cas et passerait en silence."""
    assert any(c.parent.name == "github-software-delivery" for c in POLITIQUES_DES_GABARITS)


def test_une_politique_qui_les_renseigne_reste_valide_et_s_avertit() -> None:
    """Le contrat ne change pas (ADR 0001) : la politique se charge, et chaque clé qui promet une
    garde est nommée, en anglais, avec ce qu'il faut faire à la place."""
    policy = parse_policy(POLITIQUE_D_AVANT)
    issues = policy_warnings(policy)
    assert [i.path for i in issues] == [
        "approvals.spec",
        "approvals.prod",
        "approvals.scope_change",
        "review.require_human_for_risk",
    ]
    assert {i.code for i in issues} == {"policy.approval_not_enforced", "policy.review_not_enforced"}
    for issue in issues:
        assert "is not enforced" in issue.message
        assert "Declare a human transition in the workflow" in issue.message
    # `required: never` ne promet rien : pas d'avertissement pour `approvals.merge`.
    assert "approvals.merge" not in {i.path for i in issues}


def test_le_defaut_du_contrat_ne_s_avertit_pas() -> None:
    """`review.require_human_for_risk` vaut `[high]` par défaut dans le contrat : une politique qui
    n'en dit rien ne doit pas recevoir d'avertissement pour une clé qu'elle n'a pas écrite."""
    policy = parse_policy("apiVersion: choregos/v1\nkind: Policy\nmetadata: {name: p, version: 1}\n")
    assert policy.review.require_human_for_risk  # le défaut est là…
    assert policy_warnings(policy) == []  # … et il ne s'avertit pas
    vide = parse_policy(
        "apiVersion: choregos/v1\nkind: Policy\nmetadata: {name: p, version: 1}\n"
        "review: {require_human_for_risk: []}\n"
    )
    assert policy_warnings(vide) == []


def test_le_moteur_ne_decide_d_aucune_approbation() -> None:
    """`approval_for` et `human_review_required` n'étaient appelés par personne : un moteur qui
    répond « approbation requise » sans que rien ne la demande est une garde fantôme."""
    assert not hasattr(PolicyEngine, "approval_for")
    assert not hasattr(PolicyEngine, "human_review_required")


def test_train_config() -> None:
    e = PolicyEngine(load_preset("team"))
    assert set(e.train_envs()) == {"dev", "staging", "prod"}
    prod = e.train("prod")
    assert prod is not None and prod.batch_max == 8
    assert prod.approval is not None and prod.approval.required
    assert e.train("inconnu") is None


def test_engine_for_accepts_preset_yaml_and_object() -> None:
    assert engine_for("preset:team").policy.metadata.name == "team"
    assert engine_for(preset_yaml("solo")).policy.metadata.name == "solo"
    assert engine_for(load_preset("solo")).policy.metadata.name == "solo"


def test_parse_policy_rejects_garbage() -> None:
    with pytest.raises(ValidationError, match="policy"):
        parse_policy("- juste une liste")
    with pytest.raises(ValidationError):
        parse_policy("apiVersion: choregos/v1\nkind: Policy\nmetadata: {name: X, version: 0}\n")
    with pytest.raises(ValidationError):
        parse_policy("a: [oups\n")


def test_unknown_preset_raises() -> None:
    with pytest.raises(FileNotFoundError, match="preset de politique inconnu"):
        load_preset("licorne")
