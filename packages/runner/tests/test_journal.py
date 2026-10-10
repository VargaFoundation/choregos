"""Le journal ACP du runner : par lots, sauf ce que la console montre en direct (S25-01)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from choregos_runner.client import InternalClient
from choregos_runner.runner import EventJournal


class _Client:
    """Le client interne, réduit à ce que le journal appelle : chaque lot envoyé est gardé."""

    def __init__(self) -> None:
        self.lots: list[list[dict[str, Any]]] = []

    async def post_events(self, events: list[dict[str, Any]]) -> int:
        self.lots.append(list(events))
        return len(events)


def _journal(tmp_path: Path) -> tuple[EventJournal, _Client]:
    client = _Client()
    return EventJournal(client=cast(InternalClient, client), path=tmp_path / "journal.jsonl"), client


def _maj(genre: str, **champs: Any) -> dict[str, Any]:
    return {"sessionId": "s1", "update": {"sessionUpdate": genre, **champs}}


def _message(texte: str) -> dict[str, Any]:
    return _maj("agent_message_chunk", content={"type": "text", "text": texte})


async def test_un_message_attend_son_lot(tmp_path: Path) -> None:
    journal, client = _journal(tmp_path)
    for i in range(3):
        await journal.record("session/update", _message(str(i)))
    assert client.lots == [], "trois messages restent dans le tampon"
    await journal.flush()
    assert [e["seq"] for e in client.lots[0]] == [1, 2, 3]


async def test_un_plan_part_sans_attendre_avec_ce_qui_attendait(tmp_path: Path) -> None:
    journal, client = _journal(tmp_path)
    await journal.record("session/update", _message("je lis"))
    entrees = [{"content": "lire le code", "priority": "high", "status": "in_progress"}]
    await journal.record("session/update", _maj("plan", entries=entrees))
    assert len(client.lots) == 1, "le plan est parti tout de suite"
    assert [e["seq"] for e in client.lots[0]] == [1, 2], "avec le message qui le précédait, dans l'ordre"
    assert client.lots[0][1]["payload"]["update"]["entries"] == entrees
    # Rien n'est perdu ni doublé : le transcript a les deux événements.
    lignes = (tmp_path / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lignes) == 2


async def test_le_lot_plein_part_toujours(tmp_path: Path) -> None:
    journal, client = _journal(tmp_path)
    journal.batch_size = 2
    await journal.record("run.started", {"role": "implement"})
    await journal.record("session/update", _maj("tool_call", toolCallId="t1", title="lire"))
    assert [len(lot) for lot in client.lots] == [2]
