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
    "agent-drafter": "sha256:ce17d429cab0d7b56ab229c33c8922d1a906849e370fdf6a612a9099902d44d8",
    "architect": "sha256:687e23ee5cfc454162ac4790c01fa5aa12c3302caa0c8359eaf5c7a12d9549a5",
    "assistant": "sha256:76285a0ad952fc42f1a7e4c16f7308de0e4c37a5505db09c45a0ce1a7ecd0185",
    "brief-writer": "sha256:81e32e0bdebfc64b7d1ee775e8a2d1fed59eba1c8c782cbf38a8c9d5c66402a7",
    "ci-fixer": "sha256:fe1c0de08f5fea190a91a264c3568d7875c52ec2b8bac718ca382890c74feec5",
    "compliance": "sha256:8bfc12fa6e6b40721b56e47c691016527445351bb447a20107b113ecb3f8333d",
    "developer": "sha256:c6969085476d0412ba8274d6286e6658183dbeccc93e3b4fd97b187e708409af",
    "devops": "sha256:5602b90306956c67f30a3c254ebf3b1b88d3e756a98a5433d81f87cc1f8788f9",
    "docs-writer": "sha256:5281d3c41f5c5a330fb58cf337e9b8a3d9bf2c50a6c2470f19fdd1742079b737",
    "knowledge-distiller": "sha256:c57e523e597fca4c1cad72c8bd964dbb0c5d24ffb67f4a3996d7c079bf8dd82f",
    # netops 1.1.0 adds `spec.tools: {scripts: offer}` (S57-15): its document moved once, on purpose.
    "netops": "sha256:b83b14d97c204780a556b4a977c0bd9646b5c743b0ada05303fc71e2a498d856",
    "planner": "sha256:bfabe1f2ea7a328d8a8d055970ed08c0b9666c62a62b50332978a40bc259b756",
    "prod-verifier": "sha256:ae1d31dc27b2e16ca5659ece30b8f997c1ceaa361ab59d9385d40366b8595232",
    "qa": "sha256:c7126abb73243a2e185181475d8a70c008e3186eb93c278fe12eb2512d76a82a",
    "release-notes-writer": "sha256:ab90f4588951fafca944c39b369631b11df5cd800fb4f1d6a52a84646400060e",
    "researcher": "sha256:8f8405ee5098ef9b8d68a542214fa1059d9406d64a8147e5be7119ddf2332cfe",
    "review-responder": "sha256:4ef15dacf007bcf8d8666da6b9c0b19702cbbba84453bddebea792a002adf638",
    "reviewer": "sha256:ad51010b5e057fc135b58d0ad2c7b89520c676970dffa40aa192252898eabdf7",
    "secops": "sha256:7e1043ab613c759be5ac10bab480199ce314a9f58861e03bdba5b68555b8313b",
    "security-reviewer": "sha256:5fafd9eb8fb44c7e55991509e7ac3cf59a9f872d630415f67f5e922be14f1b97",
    "spec-writer": "sha256:ec3c00790c61e27cda27847721f4aa23da82d503bc7a7ee56e75caa4ea85170f",
    "tester": "sha256:46dabdda1d21170fb4039d36fe9d85909fe2bf7b7c3a62313fc1deb9665f0316",
    "triager": "sha256:5ca995f010f09ff4f68b19c921df404d673bfa5dc426fff5fd4255e72af8c8b6",
    "ux-designer": "sha256:40d5919dc966dff35b9c35d63b43b52bc5a4869530be4d2b9c47b2d5ba861d11",
    "chatgpt": "sha256:c8381ed40acbc2888c3c4bf12ebcd9e6a49bf3edab1f7d7c070c685b7ba90af3",
    "claude-ai": "sha256:56cbd162bc57f803e6e162baf922669d6a50a3d205a521a88b69efc2b3113496",
    "claude-code": "sha256:9b26f873b0cea50cd208f055512bea877f3743addce82438d7c0d590321f836f",
    "claude-desktop": "sha256:39de108874699603fad6781e387cff032c6502304e3df228f11baaca217bdd89",
    "copilot-vscode": "sha256:5d43fcb4c391dba5250bc056ddb8df3ab03ad735ffb6c610ffac1bf64422172a",
    "cursor": "sha256:105c9636b27da7f6df85564bfe58719db587d4f0ac20281939b10e1c914eef94",
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


def test_no_catalogue_agent_fixes_a_runtime_or_a_model() -> None:
    """The model and the runtime come from the project and the deployment: an agent that required
    `claude-code` would not run on a deployment that only serves opencode (S20-11)."""
    for agent in entries():
        spec = agent.document["spec"]
        assert "model" not in spec and "backend" not in spec, agent.slug


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
