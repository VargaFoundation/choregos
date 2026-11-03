"""A decision step's call to the gateway's `/v1/systemone` (ADR 0121 §2): the wire, the answers it
accepts and refuses, the HTTP client and its scriptable fake.

The shapes below are the primary sources' (`choregos_adapters.decisions` names them): LiteLLM
1.105.5's `/v1/systemone` request example and pydantic models, and the answers Laya 0.4.21's
`laya-serve` returns, in its default (extended) and its strict (`LAYA_JEV_STRICT`) form.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from choregos_adapters import AdapterSet, DecisionClient
from choregos_adapters.decisions import (
    DecisionCall,
    DecisionUnreachableError,
    InvalidDecisionError,
    read_systemone_answers,
    systemone_request,
    systemone_url,
)
from choregos_adapters.fakes import FakeDecisionClient, FakeGateway
from choregos_adapters.gateway.systemone import SystemOneClient
from choregos_contracts.workflow import ChoiceQuestion, ScoreQuestion, YesNoQuestion

QUESTIONS: dict[str, Any] = {
    "refund": YesNoQuestion(instructions="Is the customer asking for a refund?"),
    "urgency": ChoiceQuestion(instructions="How urgent is this?", options=["low", "high"]),
    "frustration": ScoreQuestion(
        instructions="How frustrated is the customer?", levels=["calm", "annoyed", "angry"]
    ),
}

#: LiteLLM 1.105.5, `proxy/decisions_endpoints/endpoints.py`: the documented `/v1/systemone` body.
LITELLM_EXAMPLE = {
    "model": "jev",
    "state": "Customer wrote: I was charged twice for order #4411 and want one charge refunded today.",
    "questions": {
        "is_refund_request": {"type": "noul", "instructions": "Is the customer asking for a refund?"},
        "urgency": {
            "type": "choice",
            "instructions": "How urgent is this?",
            "criteria": {"low": "can wait a week", "high": "needs action today"},
        },
        "frustration": {
            "type": "score",
            "instructions": "How frustrated is the customer?",
            "criteria": ["calm", "mildly annoyed", "angry"],
        },
    },
}

#: What `laya-serve` answers by default: Laya's full payload plus `x_jev_confidence` (laya/agent.py,
#: laya/serve.py `_add_jev_confidence`). `confidence` is a normalised entropy, not a probability.
LAYA_ANSWER = {
    "model": "multilingual",
    "answers": {
        "refund": {
            "type": "noul",
            "noul": 0.27,
            "confidence": 0.73,
            "answer_confidence": 0.7,
            "action": {"act_probability": 0.9},
        },
        "urgency": {
            "type": "choice",
            "choice": "high",
            "probabilities": {"low": 0.08, "high": 0.92},
            "confidence": 0.6,
            "answer_confidence": 0.88,
            "action": {"act_probability": 0.9},
            "x_jev_confidence": 0.84,
        },
        "frustration": {
            "type": "score",
            "score": 1.3,
            "legend": {"0": "calm", "1": "annoyed", "2": "angry"},
            "probabilities": {"0": 0.1, "1": 0.5, "2": 0.4},
            "confidence": 0.2,
            "answer_confidence": 0.5,
            "action": {"act_probability": 0.9},
        },
    },
    "usage": {"input_tokens": 212, "output_tokens": 3, "truncated": False},
    "routing": {"model": "multilingual"},
}

#: The strict Jev contract (`LAYA_JEV_STRICT`, laya/serve.py `_project_jev_strict`).
STRICT_ANSWER = {
    "model": "multilingual",
    "answers": {
        "refund": {"type": "noul", "noul": 0.81},
        "urgency": {
            "type": "choice",
            "choice": "low",
            "confidence": 0.3,
            "probabilities": {"low": 0.55, "high": 0.45},
        },
        "frustration": {
            "type": "score",
            "score": 0.2,
            "confidence": 0.7,
            "probabilities": {"0": 0.8, "1": 0.2, "2": 0.0},
            "legend": {"0": "calm", "1": "annoyed", "2": "angry"},
        },
    },
    "usage": {"input_tokens": 120, "output_tokens": 3},
}


def _call(**changes: Any) -> DecisionCall:
    values: dict[str, Any] = {
        "base_url": "http://litellm:4000",
        "key": "sk-step-key",
        "model": "platform/decide",
        "state": {"title": "Charged twice", "fields.department": "sales"},
        "questions": QUESTIONS,
        "timeout_s": 3.0,
    }
    values.update(changes)
    return DecisionCall(**values)


# ───────────────────────── the wire ─────────────────────────


def test_the_request_has_the_shape_litellm_documents() -> None:
    body = systemone_request("platform/decide", {"title": "Charged twice"}, QUESTIONS)
    assert body == {
        "model": "platform/decide",
        "state": {"title": "Charged twice"},
        "questions": {
            "refund": {"type": "noul", "instructions": "Is the customer asking for a refund?"},
            # A mapping of label to an optional description: LiteLLM refuses a list here.
            "urgency": {
                "type": "choice",
                "instructions": "How urgent is this?",
                "criteria": {"low": None, "high": None},
            },
            # The levels, lowest first: the score is an index into them.
            "frustration": {
                "type": "score",
                "instructions": "How frustrated is the customer?",
                "criteria": ["calm", "annoyed", "angry"],
            },
        },
    }
    # Field for field, the same keys and types as LiteLLM's own example.
    for ours, theirs in zip(
        body["questions"].values(),
        LITELLM_EXAMPLE["questions"].values(),
        strict=True,  # type: ignore[union-attr]
    ):
        assert set(ours) == set(theirs)
        assert ours["type"] == theirs["type"]
        assert type(ours.get("criteria")) is type(theirs.get("criteria"))
    assert set(body) == set(LITELLM_EXAMPLE)


@pytest.mark.parametrize(
    ("base", "url"),
    [
        ("http://litellm:4000", "http://litellm:4000/v1/systemone"),
        ("http://litellm:4000/", "http://litellm:4000/v1/systemone"),
        ("https://gw.example/v1", "https://gw.example/v1/systemone"),
        ("https://gw.example/v1/systemone", "https://gw.example/v1/systemone"),
    ],
)
async def test_the_route_is_v1_systemone_whatever_the_gateway_url_ends_with(base: str, url: str) -> None:
    assert systemone_url(base) == url


async def test_laya_s_answers_are_read_with_the_probability_of_the_answer() -> None:
    reply = read_systemone_answers(QUESTIONS, LAYA_ANSWER)
    # noul is P(yes): 0.27 says no, with 0.73.
    assert (reply.answers["refund"].value, reply.answers["refund"].probability) == (False, 0.73)
    # The probability of the chosen option, never Laya's entropy-based `confidence` (0.6).
    assert (reply.answers["urgency"].value, reply.answers["urgency"].probability) == ("high", 0.92)
    # The expected index 1.3 falls between two levels: the most probable level is the answer.
    assert (reply.answers["frustration"].value, reply.answers["frustration"].probability) == ("annoyed", 0.5)
    assert reply.usage == {"input_tokens": 212, "output_tokens": 3}
    assert reply.model == "multilingual"


async def test_the_strict_jev_answers_are_read_too() -> None:
    reply = read_systemone_answers(QUESTIONS, STRICT_ANSWER)
    assert (reply.answers["refund"].value, reply.answers["refund"].probability) == (True, 0.81)
    assert (reply.answers["urgency"].value, reply.answers["urgency"].probability) == ("low", 0.55)
    assert (reply.answers["frustration"].value, reply.answers["frustration"].probability) == ("calm", 0.8)


def _with(question: str, **answer: Any) -> dict[str, Any]:
    body = json.loads(json.dumps(STRICT_ANSWER))
    body["answers"][question] = answer
    return body


@pytest.mark.parametrize(
    ("body", "said"),
    [
        ({"usage": {}}, "no `answers`"),
        (
            {**STRICT_ANSWER, "answers": {"refund": {"type": "noul", "noul": 0.5}}},
            "`urgency` was not answered",
        ),
        (
            _with("urgency", type="choice", choice="medium", probabilities={"medium": 0.9}),
            "not one of the options",
        ),
        (
            _with("urgency", type="choice", choice="low", probabilities={"low": 0.6, "spam": 0.4}),
            "not offered",
        ),
        (_with("urgency", type="choice", choice="low", probabilities={"high": 0.6}), "no probability for"),
        (
            _with("urgency", type="choice", choice="low", probabilities={"low": 1.4, "high": 0.0}),
            "outside [0, 1]",
        ),
        (_with("urgency", type="choice", choice="low"), "no `probabilities`"),
        (_with("refund", type="noul", noul=True), "not a number"),
        (_with("refund", type="noul", noul=-0.1), "outside [0, 1]"),
        (_with("refund", type="noul"), "no `noul`"),
        (_with("refund", type="choice", choice="low", probabilities={"low": 1.0}), "answered as `choice`"),
        (
            _with("frustration", type="score", score=1, probabilities={"0": 0.1, "3": 0.9}),
            "levels that were not",
        ),
        (
            _with("frustration", type="score", score=7, probabilities={"0": 0.1, "1": 0.9}),
            "outside the 3 levels",
        ),
        (
            _with("frustration", type="score", score=1, probabilities={"0": 0.1, "1": float("nan")}),
            "not a number",
        ),
    ],
)
async def test_an_answer_that_cannot_be_used_is_refused(body: dict[str, Any], said: str) -> None:
    with pytest.raises(InvalidDecisionError, match=None) as refused:
        read_systemone_answers(QUESTIONS, body)
    assert said in str(refused.value)


# ───────────────────────── over HTTP ─────────────────────────


async def test_the_client_posts_to_the_gateway_with_the_step_s_key() -> None:
    seen: list[httpx.Request] = []

    def gateway(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=LAYA_ANSWER)

    client = SystemOneClient(client=httpx.AsyncClient(transport=httpx.MockTransport(gateway)))
    reply = await client.decide(_call())
    assert reply.answers["urgency"].value == "high"
    (request,) = seen
    assert (request.method, str(request.url)) == ("POST", "http://litellm:4000/v1/systemone")
    assert request.headers["authorization"] == "Bearer sk-step-key"
    assert json.loads(request.content) == systemone_request(
        "platform/decide", {"title": "Charged twice", "fields.department": "sales"}, QUESTIONS
    )


@pytest.mark.parametrize(
    ("respond", "error", "said"),
    [
        (lambda r: httpx.Response(400, text="Budget has been exceeded!"), DecisionUnreachableError, "400"),
        (lambda r: httpx.Response(503, text="no healthy deployment"), DecisionUnreachableError, "503"),
        (lambda r: httpx.Response(200, text="<html>proxy</html>"), InvalidDecisionError, "not JSON"),
    ],
)
async def test_a_gateway_that_does_not_answer_is_named(
    respond: Any, error: type[Exception], said: str
) -> None:
    client = SystemOneClient(client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    with pytest.raises(error) as raised:
        await client.decide(_call())
    assert said in str(raised.value)


async def test_a_timeout_is_an_unreachable_gateway() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    client = SystemOneClient(client=httpx.AsyncClient(transport=httpx.MockTransport(slow)))
    with pytest.raises(DecisionUnreachableError, match="ReadTimeout"):
        await client.decide(_call())


# ───────────────────────── the fake ─────────────────────────


async def test_the_fake_answers_as_scripted_and_records_the_call() -> None:
    fake = FakeDecisionClient().answer(urgency=("low", 0.62), refund=(False, 0.8))
    reply = await fake.decide(_call())
    assert (reply.answers["urgency"].value, reply.answers["urgency"].probability) == ("low", 0.62)
    assert (reply.answers["refund"].value, reply.answers["refund"].probability) == (False, 0.8)
    # Unscripted: the first level, at the default probability.
    assert reply.answers["frustration"].value == "calm"
    assert fake.calls[0].key == "sk-step-key"
    assert fake.requests[0]["state"] == {"title": "Charged twice", "fields.department": "sales"}


async def test_the_fake_refuses_what_a_real_answer_would_be_refused_for() -> None:
    fake = FakeDecisionClient().answer(urgency=("medium", 0.9)).answer(refund=(True, 1.4))
    with pytest.raises(InvalidDecisionError, match="not one of the options"):
        await fake.decide(_call())
    with pytest.raises(InvalidDecisionError, match=r"outside \[0, 1\]"):
        await fake.decide(_call())
    fake.respond({"answers": {}})
    with pytest.raises(InvalidDecisionError, match="was not answered"):
        await fake.decide(_call())
    fake.fail_next("gateway down", status_code=503)
    with pytest.raises(DecisionUnreachableError, match="gateway down"):
        await fake.decide(_call())


async def test_behind_a_fake_gateway_only_a_key_it_minted_for_the_model_is_served_and_billed() -> None:
    gateway = FakeGateway()
    fake = FakeDecisionClient(gateway)
    with pytest.raises(DecisionUnreachableError, match="401"):
        await fake.decide(_call(key="sk-made-up"))
    chat = await gateway.mint_key({}, 0.05, 300, ["platform/standard"])
    with pytest.raises(DecisionUnreachableError, match="not allowed to access model"):
        await fake.decide(_call(key=chat.key))
    key = await gateway.mint_key({}, 0.05, 300, ["platform/decide"])
    await fake.decide(_call(key=key.key))
    spend = await gateway.spend(key.key_id)
    assert (spend.tokens_in, spend.tokens_out, spend.requests) == (180, 4, 1)


async def test_the_fakes_ask_through_the_fake_gateway() -> None:
    adapters = AdapterSet.fakes()
    assert isinstance(adapters.decisions, DecisionClient)
    assert isinstance(adapters.decisions, FakeDecisionClient)
    assert adapters.decisions.gateway is adapters.gateway


async def test_the_platform_asks_over_http_unless_fakes_are_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_adapters.registry import default_decision_client

    monkeypatch.setenv("CHOREGOS_FAKES", "0")
    assert isinstance(default_decision_client(), SystemOneClient)
    monkeypatch.setenv("CHOREGOS_FAKES", "1")
    assert isinstance(default_decision_client(), FakeDecisionClient)
