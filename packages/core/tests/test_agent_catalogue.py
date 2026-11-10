"""The agent catalogue shipped with the platform (ADR 0040, S21-16), and its former names (S45-05)."""

from __future__ import annotations

import importlib

import pytest
import yaml
from choregos_core.agent_catalogue import catalogue_skills, entries, entry, skill_files
from choregos_core.dsl import TEMPLATES_DIR
from choregos_core.instructions import render_instructions

INTERNAL = [e for e in entries() if e.kind == "internal"]

#: Recorded on main before the rename (S45-05): the digest of an entry is what an organisation's
#: installed copy is compared with, so no rename may move one (ADR 0098 §4).
RECORDED_DIGESTS = {
    # Every document moved once, on purpose: the plugins' versions that declare the five components
    # (S60-04, ADR 0127) — software-delivery 1.1.0, netops 1.2.0, authoring 1.1.0, requests 1.1.0.
    "agent-drafter": "sha256:28d4c5df5d91ed095358fe7990760eaa4b30f47ca3ed04c0d384ef39ed129734",
    "architect": "sha256:eff0e642adb1912c3764c289654ca961f0068185b8ead70e3a0ebcb0452297b5",
    "assistant": "sha256:e99375ec41aa60fd5202bdace406ee249c24627295f73db81f194e4f696e4e16",
    "brief-writer": "sha256:8c000f3c88b249f96bff9c4522486f34c2fb83d6ef59b4dde817b993a77e7cfe",
    "chatgpt": "sha256:c8381ed40acbc2888c3c4bf12ebcd9e6a49bf3edab1f7d7c070c685b7ba90af3",
    "ci-fixer": "sha256:48059afd0bff35062762b871ca8a5cc3079da7187e876de7ea54b38760095625",
    "claude-ai": "sha256:56cbd162bc57f803e6e162baf922669d6a50a3d205a521a88b69efc2b3113496",
    "claude-code": "sha256:9b26f873b0cea50cd208f055512bea877f3743addce82438d7c0d590321f836f",
    "claude-desktop": "sha256:39de108874699603fad6781e387cff032c6502304e3df228f11baaca217bdd89",
    "compliance": "sha256:04140763523b84016ab91560a5f1562f7d9d95460e1eb46bd363bca2b1c6a2ea",
    "copilot-vscode": "sha256:5d43fcb4c391dba5250bc056ddb8df3ab03ad735ffb6c610ffac1bf64422172a",
    "cursor": "sha256:105c9636b27da7f6df85564bfe58719db587d4f0ac20281939b10e1c914eef94",
    "developer": "sha256:d5d0d1986526f3936938bd5ad589f329b153374d7305d78e20855011436f3425",
    "devops": "sha256:37a9e6c5aa28850cc724910b2b7470fd1fcdccac825cc81984c72dcb642d8e7a",
    "docs-writer": "sha256:9bae9942e8050d85a6fc389c042d7d92b36c3493d520a1888d5e5a7d8c92f1f9",
    "knowledge-distiller": "sha256:01059be74d34854fc824794dea067b72716c8af4c0aa67477ec956a2a7ce538d",
    "netops": "sha256:7f6a8262fee61e909fc5cbeb78d2aa90eff5441868db3a2c150902c846af8718",
    "planner": "sha256:f93fc8a2b3ae9d5b098951c3a9e87c8ab7c81ae8f7f2bc5da389c94af86cc0b6",
    "prod-verifier": "sha256:64b83b446b62cea8ac398f77d7469b25d0f69af685d4cd13f695bcf055b56b9c",
    "qa": "sha256:56dcd15cac559affd95db5e85bb2beede48da7824a816de07ad7d153a945b22c",
    "release-notes-writer": "sha256:7f71971e7afd9f8b565caf4347d50a47459f4f4f018cba9f31a75242699cad84",
    "researcher": "sha256:bf5aead971bca8c0e9178623afd5dd28e76bf6303a65ecca9c6ea1a2e6fda790",
    "review-responder": "sha256:1abe0879e42dd199e514e91697025e585f6eb776c38de01ff5921019564c1ea5",
    "reviewer": "sha256:494b21d94b9dc9889193a035886e9dad242e15614a5fb8aa5e01c09d8f2e8bff",
    "secops": "sha256:3fddcf91e661b32595719bbc20a67141de31bf6ca291388bc0fd89f1bdc6446b",
    "security-reviewer": "sha256:99c73cbb47a43e8bd3b3eca6c82a8ab0f92acb6cc50de73cf32a1f55ea2035ae",
    "spec-writer": "sha256:53af1e951ee7e39f7bd00a2cc24a22caac8d48c3177726afac8780cad0b14eaf",
    "tester": "sha256:815b82f2e16a3bfb0e6e8f345d5748bb5c6184c6ee865ac67c1445fee4e91f83",
    "triager": "sha256:57a5896da90486707b73633a69d72e4aad63c01f386ac67e65de59ea9cad6a2e",
    "ux-designer": "sha256:ed90d08347d27b93a76bb3e48598638b8854b5b9aa68cc5e7ebef7a150aa32c3",
}


def _workflow_inputs() -> set[str]:
    """Every input the shipped workflows declare: an agent must render with each of them."""
    names: set[str] = set()
    for path in TEMPLATES_DIR.glob("*.yaml"):
        for transition in yaml.safe_load(path.read_text(encoding="utf-8"))["transitions"]:
            names.update(transition.get("inputs") or [])
    return names | {"human_feedback", "test_report", "review_markdown", "security_review_markdown",
                    "research_markdown", "adr_path", "spec_markdown"}  # fmt: skip


@pytest.mark.parametrize("agent", INTERNAL, ids=lambda e: e.slug)
def test_every_catalogue_agent_renders_without_error(agent) -> None:  # type: ignore[no-untyped-def]
    """With the sample variables, without inputs, then with all of them: `StrictUndefined` refuses a
    name the template would read without `.get` — the agent would die at its first run."""
    instructions = agent.document["spec"]["instructions"]
    empty = render_instructions(instructions)
    full = render_instructions(instructions, inputs=dict.fromkeys(_workflow_inputs(), "x"))
    assert "## Work item" in empty and "DEMO-1" in empty
    assert len(full) >= len(empty)


def test_the_ticket_context_precedes_an_internal_agents_instructions() -> None:
    rendered = render_instructions(
        entry("developer").document["spec"]["instructions"],  # type: ignore[union-attr]
        ticket={"key": "SBX-12", "title": "Fix rounding", "body": "Totals are off by one cent."},
        spec="Given an invoice",
        allowed_paths=["src/orders/**"],
        inputs={"test_report": "2 tests fail", "human_feedback": ""},
    )
    assert rendered.index("**SBX-12** — Fix rounding") < rendered.index("You implement the work item")
    assert "## Approved specification\nGiven an invoice" in rendered
    assert "- `src/orders/**`" in rendered
    assert "## Input: test_report\n2 tests fail" in rendered
    assert "Input: human_feedback" not in rendered, "an empty input is not shown"


def test_no_catalogue_agent_fixes_a_runtime() -> None:
    """The runtime comes from the deployment: an agent that required `claude-code` would not run on a
    deployment that only serves opencode (S20-11). The model is the version's to name since ADR 0127
    (a tier or a model; `tests/conformance/test_shipped_agents_are_complete.py` says which agents do)."""
    for agent in entries():
        spec = agent.document["spec"]
        assert "backend" not in spec and "runtime" not in spec, agent.slug


def test_every_role_of_the_package_has_its_agent_in_the_catalogue() -> None:
    playbooks = pytest.importorskip("choregos_playbooks")
    roles = {e.role for e in INTERNAL}
    expected = set(playbooks.KNOWN_ROLES)
    assert expected <= roles, expected - roles


def test_the_skills_an_agent_names_are_in_the_catalogue() -> None:
    known = set(catalogue_skills())
    for agent in entries():
        assert set(agent.skills) <= known, (agent.slug, agent.skills)
    assert skill_files("madr-4")["SKILL.md"].startswith("---\nname: madr-4\n")


def test_an_external_client_says_where_it_calls_from_and_has_no_instructions_nor_model() -> None:
    external = [e for e in entries() if e.kind == "external"]
    assert {e.client for e in external} == {
        "claude-code",
        "claude-desktop",
        "cursor",
        "vscode",
        "chatgpt",
        "claude-ai",
    }
    assert {e.slug for e in external if e.reach == "cloud"} == {"chatgpt", "claude-ai"}
    assert all(not e.document["spec"] for e in external)


def test_the_digest_changes_when_the_text_changes() -> None:
    from dataclasses import replace

    agent = entry("triager")
    assert agent is not None
    other = replace(agent, document={**agent.document, "description": "x"})
    assert other.digest != agent.digest


def test_the_architect_checks_its_number_before_accepting() -> None:
    """#287: sent back by `adr_number_free`, the architect restarts from acceptance; that is where it
    rereads the default branch's numbers and renumbers its own if it is taken."""
    architect = entry("architect")
    assert architect is not None
    instructions = architect.document["spec"]["instructions"]
    accept = render_instructions(instructions, inputs={"adr_path": "docs/adr/0007-queue.md"})
    assert "docs/adr/0007-queue.md" in accept
    assert "adr_number_free" in accept and "git ls-tree" in accept
    assert "highest number on the default branch plus one" in accept


def test_every_entry_has_one_to_ten_valid_tags() -> None:
    import re

    for proposal in entries():
        assert 1 <= len(proposal.tags) <= 10, proposal.slug
        assert len(set(proposal.tags)) == len(proposal.tags), proposal.slug
        assert all(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", tag) for tag in proposal.tags), proposal.slug


def test_the_architect_is_tagged_madr_architecture_design() -> None:
    found = entry("architect")
    assert found is not None
    assert found.tags == ("madr", "architecture", "design")


# ───────────────────────── S45-05: the rename moved nothing, the former names work ─────────────────────────


def test_digests_did_not_move() -> None:
    assert {e.slug: e.digest for e in entries()} == RECORDED_DIGESTS


def test_the_context_file_has_its_new_name() -> None:
    from choregos_core.agent_catalogue import FOLDER

    assert (FOLDER / "_context.md").is_file()
    assert not (FOLDER / "_contexte.md").exists()  # lang: fr-ok


def test_former_package_is_gone() -> None:
    """`choregos_core.catalogue_d_agents` was removed in 0.24.0 (S45-49)."""
    with pytest.raises(ImportError):
        importlib.import_module("choregos_core.catalogue_d_agents")  # lang: fr-ok - removed, asserted gone
    with pytest.raises(ImportError):
        importlib.import_module("choregos_core.catalogue_d_agents.evals")  # lang: fr-ok - asserted gone


@pytest.mark.parametrize(
    "former",
    [
        "EntreeDuCatalogue",  # lang: fr-ok - removed, asserted gone
        "DOSSIER",  # lang: fr-ok - removed, asserted gone
        "entrees",  # lang: fr-ok - removed, asserted gone
        "entree",  # lang: fr-ok - removed, asserted gone
        "skills_du_catalogue",  # lang: fr-ok - removed, asserted gone
        "fichiers_de_la_skill",  # lang: fr-ok - removed, asserted gone
    ],
)
def test_former_names_are_gone(former: str) -> None:
    from choregos_core import agent_catalogue

    assert not hasattr(agent_catalogue, former)
