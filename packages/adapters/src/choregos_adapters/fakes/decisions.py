# SPDX-License-Identifier: Apache-2.0
"""In-memory decision model behind a fake gateway (ADR 0121): scripted answers, recorded calls.

It answers in the System One wire format and reads its own answer back with the same reader as the
HTTP client (`read_systemone_answers`), so a scripted answer that names an option not offered, or a
probability outside [0, 1], is refused exactly as a real one would be. Given the fake gateway, it
also behaves like LiteLLM in front of a decision model: a key the gateway did not mint, or one not
minted for the model asked, is refused, and the call is billed to the key.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from typing import Any

from choregos_contracts.workflow import ChoiceQuestion, YesNoQuestion

from ..decisions import (
    DecisionCall,
    DecisionReply,
    DecisionUnreachableError,
    Question,
    read_systemone_answers,
    systemone_request,
)
from .gateway import BudgetExceededError, FakeGateway

#: What a question is answered when nothing is scripted: the first option or level, or yes.
DEFAULT_PROBABILITY = 0.9
#: The tokens a scripted call is billed, as a small CPU decision model reports them.
DEFAULT_USAGE = (180, 4)


class FakeDecisionClient:
    """Scripted per call: `answer(route=("billing", 0.93))`, `respond({...})`, `fail_next()`."""

    def __init__(self, gateway: FakeGateway | None = None) -> None:
        self.gateway = gateway
        #: Every call, as the platform made it, and the wire body it would have sent.
        self.calls: list[DecisionCall] = []
        self.requests: list[dict[str, Any]] = []
        self._script: deque[dict[str, Any]] = deque()
        self.usage = DEFAULT_USAGE

    def answer(self, **answers: tuple[Any, float]) -> FakeDecisionClient:
        """The next call's answers, `question=(value, probability)`; an unnamed question gets the default."""
        self._script.append({"answers": dict(answers)})
        return self

    def respond(self, body: dict[str, Any]) -> FakeDecisionClient:
        """The next call's raw System One response, whatever it says."""
        self._script.append({"body": body})
        return self

    def fail_next(
        self, message: str = "connection refused", status_code: int | None = None
    ) -> FakeDecisionClient:
        """The next call gets no answer, as when the gateway is down or refuses it."""
        self._script.append({"error": DecisionUnreachableError(message, status_code=status_code)})
        return self

    async def decide(self, call: DecisionCall) -> DecisionReply:
        self.calls.append(call)
        self.requests.append(systemone_request(call.model, call.state, call.questions))
        step = self._script.popleft() if self._script else {"answers": {}}
        if "error" in step:
            raise step["error"]
        self._bill(call)
        body = step.get("body") or wire_answers(call.questions, step["answers"], self.usage)
        return read_systemone_answers(call.questions, body)

    def _bill(self, call: DecisionCall) -> None:
        """What LiteLLM does with the call's key: authenticate it, check its models, bill it."""
        if self.gateway is None:
            return
        key_id = next((kid for kid, key in self.gateway.keys.items() if key.key == call.key), None)
        if key_id is None:
            raise DecisionUnreachableError("401 Authentication Error, the key is not valid", status_code=401)
        if call.model not in self.gateway.keys[key_id].models:
            raise DecisionUnreachableError(
                f"401 key not allowed to access model `{call.model}`", status_code=401
            )
        try:
            self.gateway.record_usage(key_id, self.usage[0], self.usage[1], model=call.model)
        except BudgetExceededError as error:
            raise DecisionUnreachableError(f"400 {error}", status_code=400) from error


def wire_answers(
    questions: Mapping[str, Question],
    scripted: Mapping[str, tuple[Any, float]],
    usage: tuple[int, int] = DEFAULT_USAGE,
) -> dict[str, Any]:
    """A System One response giving each question its scripted answer, or the default."""
    answers: dict[str, Any] = {}
    for name, question in questions.items():
        value, probability = scripted.get(name, (None, DEFAULT_PROBABILITY))
        answers[name] = _wire_answer(question, value, probability)
    return {
        "model": "fake-decide",
        "answers": answers,
        "usage": {"input_tokens": usage[0], "output_tokens": usage[1]},
    }


def _wire_answer(question: Question, value: Any, probability: float) -> dict[str, Any]:
    if isinstance(question, YesNoQuestion):
        yes = True if value is None else bool(value)
        return {"type": "noul", "noul": probability if yes else round(1.0 - probability, 6)}
    labels = list(question.options) if isinstance(question, ChoiceQuestion) else list(question.levels)
    chosen = labels[0] if value is None else value
    others = [label for label in labels if label != chosen]
    rest = round((1.0 - probability) / len(others), 6) if others else 0.0
    if isinstance(question, ChoiceQuestion):
        # A value not offered is answered as such: the reader refuses it, as it would a real one.
        probabilities = {chosen: probability, **dict.fromkeys(others, rest)}
        return {"type": "choice", "choice": chosen, "confidence": probability, "probabilities": probabilities}
    index = labels.index(chosen) if chosen in labels else len(labels)
    probabilities = {str(i): (probability if i == index else rest) for i in range(len(labels))}
    if index == len(labels):
        probabilities[str(index)] = probability
    return {
        "type": "score",
        "score": index,
        "confidence": probability,
        "legend": {str(i): level for i, level in enumerate(labels)},
        "probabilities": probabilities,
    }


__all__ = ["DEFAULT_PROBABILITY", "FakeDecisionClient", "wire_answers"]
