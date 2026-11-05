"""The access sheet: what the agent touched, and what it was refused."""

from __future__ import annotations

from choregos_core import access_report


def _permission(kind: str, target: str, allowed: bool = True, reason: str = "") -> dict[str, object]:
    return {
        "type": "session/request_permission",
        "payload": {"kind": kind, "target": target, "allowed": allowed, "reason": reason},
    }


def test_refusals_come_first_and_keep_their_reason() -> None:
    """That is what one comes looking for in an audit. "denied" does not read;
    "write forbidden in a sensitive file" does."""
    report = access_report(
        [
            _permission("read", "/workspace/src/cart.py"),
            _permission("read", "/workspace/src/cart.py"),
            _permission("execute", "pytest -q"),
            _permission("write", "/workspace/.env", allowed=False, reason="sensitive file"),
        ]
    )
    assert report.lines[0].target == "/workspace/.env", "the refusal first"
    assert report.lines[0].reasons == ["sensitive file"]
    assert report.refused_lines and report.refused_lines[0].nature == "write"
    # Then by frequency: what the agent touched most shows first.
    assert report.lines[1].target == "/workspace/src/cart.py"
    assert report.lines[1].requests == 2


def test_the_agents_prose_does_not_enter_the_sheet() -> None:
    """The ACP log is mostly text. The sheet answers "what did it touch",
    not "what did it say", otherwise it becomes unreadable again, which is the problem."""
    report = access_report(
        [
            {"type": "session/update", "payload": {"update": {"text": "I am looking at the ticket"}}},
            _permission("read", "/workspace/README.md"),
        ]
    )
    assert [line.target for line in report.lines] == ["/workspace/README.md"]
    assert report.events == 2, "everything is counted, only part of it is kept"


def test_a_catalogue_tool_call_is_an_access() -> None:
    """An agent that queries a third-party API touches the world, even without writing a file."""
    report = access_report(
        [{"type": "choregos.tool.called", "payload": {"tool": "check_address", "provider": "address-api"}}]
    )
    assert report.lines[0].nature == "tool"
    assert report.lines[0].target == "check_address"


def test_an_unknown_nature_stays_visible() -> None:
    """An access we cannot classify must not disappear: it is precisely the one to look at."""
    report = access_report([_permission("telepathy", "somewhere")])
    assert report.lines[0].nature == "other"
    assert report.lines[0].target == "somewhere"


def test_access_report_serves_the_english_keys_only() -> None:
    """The keys the API serves are the S45-02 contract, in English; the former ones, emitted for one
    release, are gone since 0.24.0 (S45-49)."""
    report = access_report(
        [
            _permission("write", "/workspace/.env", allowed=False, reason="sensitive file"),
            _permission("telepathy", "somewhere"),
        ]
    )
    served = report.to_dict()
    assert set(served) == {"events", "inferred", "refusals", "access"}
    for former in ("evenements", "deduites", "acces"):  # lang: fr-ok - removed, asserted gone
        assert former not in served, former
    assert set(served["access"][0]) == {"nature", "target", "requests", "refusals", "reasons"}
    assert served["events"] == 2
    assert served["inferred"] == 0
    assert served["refusals"] == 1
    assert served["access"][0] == {
        "nature": "write",
        "target": "/workspace/.env",
        "requests": 1,
        "refusals": 1,
        "reasons": ["sensitive file"],
    }
    assert served["access"][1]["nature"] == "other"
    assert not hasattr(report.lines[0], "former_dict")
