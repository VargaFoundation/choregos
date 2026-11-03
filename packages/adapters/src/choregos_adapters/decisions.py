# SPDX-License-Identifier: Apache-2.0
"""A decision step's call to a decision model, on the gateway's `POST /v1/systemone` (ADR 0121 §2).

The wire is TypeSafe's "System One" format, which LiteLLM serves since 1.104.2 and Laya's own server
(`laya-serve`) speaks. Read from the primary sources, not guessed:

- LiteLLM 1.105.5, `litellm/types/decisions.py` (`DecisionsRequest`, `NoulQuestion`, `ChoiceQuestion`,
  `ScoreQuestion`, `DecisionsResponse`, `NoulAnswer`, `ChoiceAnswer`, `ScoreAnswer`,
  `DecisionsUsage`) and `litellm/proxy/decisions_endpoints/endpoints.py` (the `/v1/systemone` route,
  behind `user_api_key_auth`: a virtual key as `Authorization: Bearer`);
- Laya 0.4.21, `laya/serve.py` (`/v1/systemone`, `_project_jev_strict`) and `laya/agent.py`
  (`_validate_question`, the answers `system_one` builds).

The request is `{"model", "state", "questions": {<id>: <question>}}`, and our three question types
map to it as:

- `yes_no` → `{"type": "noul", "instructions"}`;
- `choice` → `{"type": "choice", "instructions", "criteria": {<option>: null, ...}}` — LiteLLM takes
  `criteria` as a mapping of label to an optional description (Laya also takes a list; LiteLLM does
  not, so the mapping is what goes out);
- `score` → `{"type": "score", "instructions", "criteria": [<level>, ...]}`, lowest level first.

The response is `{"model", "answers": {<id>: <answer>}, "usage": {"input_tokens", "output_tokens"}}`:

- `noul`: `{"noul": p}`, the probability that the answer is yes. The answer is `p >= 0.5`, and its
  probability is that of the side reported, `max(p, 1 - p)` (Laya's own reading, `structured.py`);
- `choice`: `{"choice": <label>, "probabilities": {<label>: p, ...}, "confidence"}`. The answer is
  `choice`, its probability `probabilities[choice]`. `confidence` is NOT used: Laya computes it as a
  normalised entropy over the options, not as the probability of the answer;
- `score`: `{"score": s, "probabilities": {"0": p, ...}, "legend": {"0": <level>, ...},
  "confidence"}`. `s` is the expected level index (Σ i·pᵢ in Laya), which may fall between two
  levels: the answer is the most probable level, and its probability that level's.

An answer that leaves a question unanswered, names an option or a level that was not offered, or
carries a probability outside [0, 1] is refused (`InvalidDecisionError`): the platform never guesses an
answer the model did not give.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from choregos_contracts.workflow import ChoiceQuestion, ScoreQuestion, YesNoQuestion

from .errors import AdapterError, UpstreamError

#: The route a decision step calls on its project's gateway.
SYSTEMONE_PATH = "/v1/systemone"

Question = YesNoQuestion | ChoiceQuestion | ScoreQuestion


class InvalidDecisionError(AdapterError):
    """The decision model answered, and the answer cannot be used as it is."""


class DecisionUnreachableError(UpstreamError):
    """The gateway did not give an answer: refused the call, failed, or did not answer in time."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__("systemone", message, status_code=status_code)


@dataclass(frozen=True, slots=True)
class DecisionCall:
    """One decision: what is asked, of which model, on which gateway, with which key."""

    #: The gateway's URL, as a run of the project is given it (`…:4000` or `…:4000/v1`).
    base_url: str
    #: The virtual key minted for this step, sent as `Authorization: Bearer`.
    key: str
    model: str
    #: What the step names, already masked: `{"title": …, "fields.department": …}`.
    state: Mapping[str, Any]
    questions: Mapping[str, Question]
    timeout_s: float = 15.0


@dataclass(frozen=True, slots=True)
class DecisionAnswer:
    """One question's answer: `True`/`False`, an option or a level, and its probability."""

    value: bool | str
    probability: float


@dataclass(frozen=True, slots=True)
class DecisionReply:
    """Every question's answer, and what the gateway says it cost."""

    answers: dict[str, DecisionAnswer]
    model: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


def systemone_url(base_url: str) -> str:
    """`http://gw:4000` and `http://gw:4000/v1` both call `http://gw:4000/v1/systemone`."""
    base = base_url.rstrip("/")
    if base.endswith(SYSTEMONE_PATH):
        return base
    if base.endswith("/v1"):
        return f"{base}/systemone"
    return f"{base}{SYSTEMONE_PATH}"


def wire_question(question: Question) -> dict[str, Any]:
    """One of our questions in the System One format (see the module's docstring)."""
    if isinstance(question, YesNoQuestion):
        return {"type": "noul", "instructions": question.instructions}
    if isinstance(question, ChoiceQuestion):
        return {
            "type": "choice",
            "instructions": question.instructions,
            "criteria": dict.fromkeys(question.options),
        }
    return {"type": "score", "instructions": question.instructions, "criteria": list(question.levels)}


def systemone_request(
    model: str, state: Mapping[str, Any], questions: Mapping[str, Question]
) -> dict[str, Any]:
    """The body of `POST /v1/systemone`."""
    return {
        "model": model,
        "state": dict(state),
        "questions": {name: wire_question(question) for name, question in questions.items()},
    }


def read_systemone_answers(questions: Mapping[str, Question], body: Any) -> DecisionReply:
    """Every asked question's answer from a System One response, or `InvalidDecisionError`.

    An answer to a question that was not asked is ignored; a missing one is refused.
    """
    if not isinstance(body, Mapping) or not isinstance(body.get("answers"), Mapping):
        raise InvalidDecisionError("the response has no `answers` object")
    answers: Mapping[str, Any] = body["answers"]
    read: dict[str, DecisionAnswer] = {}
    for name, question in questions.items():
        answer = answers.get(name)
        if not isinstance(answer, Mapping):
            raise InvalidDecisionError(f"the question `{name}` was not answered")
        if isinstance(question, YesNoQuestion):
            read[name] = _yes_no(name, answer)
        elif isinstance(question, ChoiceQuestion):
            read[name] = _choice(name, question, answer)
        else:
            read[name] = _score(name, question, answer)
    reported = body.get("usage")
    usage: Mapping[str, Any] = reported if isinstance(reported, Mapping) else {}
    model = body.get("model")
    return DecisionReply(
        answers=read,
        model=str(model) if isinstance(model, str) and model else None,
        usage={
            key: int(value)
            for key in ("input_tokens", "output_tokens")
            if isinstance(value := usage.get(key), int) and not isinstance(value, bool) and value >= 0
        },
    )


def _probability(name: str, value: Any, what: str) -> float:
    """A probability in [0, 1], or `InvalidDecisionError` naming the question."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise InvalidDecisionError(f"the question `{name}`: {what} is not a number ({value!r})")
    if not 0.0 <= float(value) <= 1.0:
        raise InvalidDecisionError(f"the question `{name}`: {what} is outside [0, 1] ({value!r})")
    return float(value)


def _typed(name: str, answer: Mapping[str, Any], expected: str) -> None:
    kind = answer.get("type")
    if kind is not None and kind != expected:
        raise InvalidDecisionError(
            f"the question `{name}` was asked as `{expected}` and answered as `{kind}`"
        )


def _yes_no(name: str, answer: Mapping[str, Any]) -> DecisionAnswer:
    _typed(name, answer, "noul")
    if "noul" not in answer:
        raise InvalidDecisionError(f"the question `{name}` has no `noul` probability")
    yes = _probability(name, answer["noul"], "`noul`")
    return DecisionAnswer(value=yes >= 0.5, probability=round(max(yes, 1.0 - yes), 6))


def _probabilities(name: str, answer: Mapping[str, Any]) -> dict[str, float]:
    raw = answer.get("probabilities")
    if not isinstance(raw, Mapping) or not raw:
        raise InvalidDecisionError(f"the question `{name}` has no `probabilities`")
    return {str(key): _probability(name, value, f"the probability of `{key}`") for key, value in raw.items()}


def _choice(name: str, question: ChoiceQuestion, answer: Mapping[str, Any]) -> DecisionAnswer:
    _typed(name, answer, "choice")
    chosen = answer.get("choice")
    if not isinstance(chosen, str) or chosen not in question.options:
        raise InvalidDecisionError(
            f"the question `{name}` was answered {chosen!r}, which is not one of the options offered "
            f"({', '.join(question.options)})"
        )
    probabilities = _probabilities(name, answer)
    if chosen not in probabilities:
        raise InvalidDecisionError(f"the question `{name}` gives no probability for its answer `{chosen}`")
    unknown = sorted(set(probabilities) - set(question.options))
    if unknown:
        raise InvalidDecisionError(
            f"the question `{name}` scores options that were not offered: {', '.join(unknown)}"
        )
    return DecisionAnswer(value=chosen, probability=probabilities[chosen])


def _score(name: str, question: ScoreQuestion, answer: Mapping[str, Any]) -> DecisionAnswer:
    _typed(name, answer, "score")
    levels = list(question.levels)
    probabilities = _probabilities(name, answer)
    offered = {str(index) for index in range(len(levels))}
    unknown = sorted(set(probabilities) - offered)
    if unknown:
        raise InvalidDecisionError(
            f"the question `{name}` scores levels that were not offered: {', '.join(unknown)}"
        )
    if "score" in answer:
        score = answer["score"]
        if (
            isinstance(score, bool)
            or not isinstance(score, int | float)
            or not math.isfinite(score)
            or not 0 <= score <= len(levels) - 1
        ):
            raise InvalidDecisionError(
                f"the question `{name}` was answered the score {score!r}, "
                f"outside the {len(levels)} levels offered"
            )
    # The most probable level; on a tie, the lowest (the order is the levels').
    best = max(sorted(probabilities, key=int), key=lambda index: probabilities[index])
    return DecisionAnswer(value=levels[int(best)], probability=probabilities[best])


__all__ = [
    "SYSTEMONE_PATH",
    "DecisionAnswer",
    "DecisionCall",
    "DecisionReply",
    "DecisionUnreachableError",
    "InvalidDecisionError",
    "read_systemone_answers",
    "systemone_request",
    "systemone_url",
    "wire_question",
]
