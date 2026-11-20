# SPDX-License-Identifier: Apache-2.0
"""`choregos decide` against the real API process (S67-10, ADR 0154 §7): what waits for the person is read
from `GET /me/pending`, and decided on the route that decides it, as the person (a session), never with a
pasted token."""

from __future__ import annotations

import httpx

from .conftest import Api, lay_step, project, save_token


def _waiting(api: Api, console: httpx.Client, title: str, *, kind: str = "approval") -> str:
    return lay_step(api, console, project(console), title, kind=kind)[1]


def test_what_waits_is_listed_shown_and_approved_with_a_typed_verb(
    signed_in: Api, console: httpx.Client
) -> None:
    request_id = _waiting(signed_in, console, "Ship 1.2")

    listed = signed_in.choregos("decide", "list")
    assert listed.returncode == 0, listed.stdout + listed.stderr
    assert "Ship 1.2?" in listed.stdout and request_id in listed.stdout, listed.stdout

    shown = signed_in.choregos("decide", "show", request_id)
    assert shown.returncode == 0 and "Ship 1.2?" in shown.stdout, shown.stdout

    # Enter alone is not the gesture: nothing is sent and the step still waits.
    left = signed_in.choregos("decide", "approve", request_id, stdin="\n")
    assert left.returncode == 1 and "left waiting" in left.stdout, left.stdout
    assert request_id in signed_in.choregos("decide", "list").stdout

    done = signed_in.choregos("decide", "approve", request_id, stdin="approve\n")
    assert done.returncode == 0 and "approve sent" in done.stdout, done.stdout + done.stderr
    assert request_id not in signed_in.choregos("decide", "list").stdout, "decided: it no longer waits"


def test_a_rejection_asks_a_reason_and_sends_nothing_without_one(
    signed_in: Api, console: httpx.Client
) -> None:
    request_id = _waiting(signed_in, console, "Drop the badge step")

    empty = signed_in.choregos("decide", "reject", request_id, stdin="\n")
    assert empty.returncode == 1 and "reason is required" in empty.stdout + empty.stderr
    assert request_id in signed_in.choregos("decide", "list").stdout, "no reason: nothing was sent"

    done = signed_in.choregos("decide", "reject", request_id, "--reason", "not this week", stdin="reject\n")
    assert done.returncode == 0 and "reject sent" in done.stdout, done.stdout + done.stderr
    assert request_id not in signed_in.choregos("decide", "list").stdout


def test_a_question_is_answered_and_an_approval_is_not(signed_in: Api, console: httpx.Client) -> None:
    question = _waiting(signed_in, console, "Which region", kind="question")
    wrong = signed_in.choregos("decide", "approve", question)
    assert wrong.returncode == 1 and "`answer`" in wrong.stdout, wrong.stdout
    done = signed_in.choregos("decide", "answer", question, "--answer", "eu-west", stdin="answer\n")
    assert done.returncode == 0 and "answer sent" in done.stdout, done.stdout + done.stderr


def test_a_pasted_token_lists_but_cannot_decide_and_is_told_to_sign_in(
    signed_in: Api, console: httpx.Client
) -> None:
    request_id = _waiting(signed_in, console, "Token must not decide")
    save_token(signed_in, console)

    listed = signed_in.choregos("decide", "list", profile="token")
    assert request_id in listed.stdout, listed.stdout + listed.stderr

    refused = signed_in.choregos("decide", "approve", request_id, profile="token", stdin="approve\n")
    assert refused.returncode == 1, refused.stdout
    assert "choregos login" in refused.stdout + refused.stderr, refused.stdout + refused.stderr
    assert request_id in signed_in.choregos("decide", "list").stdout, (
        "still waiting: the token decided nothing"
    )


def test_decide_has_no_batch_gesture(signed_in: Api) -> None:
    both = signed_in.choregos("decide", "approve", "--all")
    assert both.returncode != 0 and "No such option" in both.stdout + both.stderr
