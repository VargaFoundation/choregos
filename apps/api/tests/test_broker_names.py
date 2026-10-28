"""The broker lives in `services/broker.py` in English; `services/courtier.py` only re-exports it (S46-18).

What these tests hold: the id of a governed write is persisted, so it must not change with the move;
every importer that still says the French name keeps resolving to the same object; and the shim
defines nothing, so there is one implementation, not two.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from choregos_api.services import broker, courtier

#: Computed from `identifiant_de_l_appel` as it was before the move (uuid5 of `run:tool:canonical JSON`).
CALL_ID = "c6a66a5a-d541-5b95-a6ab-1cc8ed2a3492"
GOVERNED_WRITE = "runs as a governed action: journaled, done once, undone if a later step fails"
NEEDS_APPROVAL = "needs a human approval: calling it proposes the action, it does not run it"


def test_call_ids_are_unchanged() -> None:
    """The id is persisted as `actions.id`: a replayed call must find the action it already made."""
    arguments = {"model": "x14", "n": 1}

    assert broker.call_id("run-write-1", "supplier__order_pc", arguments) == CALL_ID
    # The arguments are canonical: their order does not change the call.
    assert broker.call_id("run-write-1", "supplier__order_pc", {"n": 1, "model": "x14"}) == CALL_ID
    assert courtier.identifiant_de_l_appel is broker.call_id


def test_the_french_names_still_import() -> None:
    assert courtier.OutilDuCourtier is broker.BrokerTool
    assert courtier.appeler is broker.call
    assert courtier.ecrire_par_une_action is broker.write_through_action
    assert courtier.outils_du_courtier is broker.broker_tools
    assert courtier.proposer_l_appel is broker.propose_call
    assert courtier.ArgumentsRefuses is broker.ArgumentsRefused
    assert courtier.verifier_les_arguments is broker.check_arguments
    assert courtier.texte_du_resultat is broker.result_text
    assert courtier.SEPARATEUR == broker.SEPARATOR == "__"
    # What the importers read under its English name is the same object too.
    assert courtier.bounded_payload is broker.bounded_payload
    assert courtier.bounded_result is broker.bounded_result
    assert courtier.output_refusal is broker.output_refusal
    assert courtier.readable_text is broker.readable_text
    assert courtier.result_limit is broker.result_limit
    assert courtier.selection_of_spec is broker.selection_of_spec
    assert courtier.session_text is broker.session_text
    assert courtier.tools_of_selection is broker.tools_of_selection
    assert courtier.TRUNCATED is broker.TRUNCATED
    assert sorted(courtier.__all__) == sorted(set(courtier.__all__))
    assert set(broker.__all__) <= set(courtier.__all__)


def _tool(*, needs_approval: bool) -> broker.BrokerTool:
    connector = SimpleNamespace(name="supplier", kind="mcp", type="mcp")
    operation = SimpleNamespace(
        name="order_pc",
        access="write",
        description="Order a PC",
        input_schema={},
        output_schema=None,
    )
    return broker.BrokerTool("supplier__order_pc", cast(Any, connector), cast(Any, operation), needs_approval)


def test_a_broker_tool_answers_under_both_names() -> None:
    tool = _tool(needs_approval=False)

    assert tool.nom == tool.name == "supplier__order_pc"
    assert tool.connecteur is tool.connector
    assert tool.ecriture is True
    assert tool.ecriture == tool.is_write
    assert tool.sous_validation is False
    assert tool.sous_validation == tool.needs_approval
    assert tool.annonce() == tool.announcement()
    announced = tool.announcement()
    assert announced["name"] == "supplier__order_pc"
    assert announced["inputSchema"] == {"type": "object"}
    assert announced["description"] == f"[supplier] Order a PC — {GOVERNED_WRITE}"


def test_a_tool_under_approval_announces_the_approval_sentence() -> None:
    tool = _tool(needs_approval=True)

    assert tool.sous_validation is True
    assert tool.annonce() == tool.announcement()
    assert tool.announcement()["description"] == f"[supplier] Order a PC — {NEEDS_APPROVAL}"


def test_the_shim_defines_nothing() -> None:
    """One implementation: a function or a class defined in the shim would be a second copy."""
    tree = ast.parse(Path(courtier.__file__).read_text(encoding="utf-8"))

    defined = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    ]
    assert defined == []
