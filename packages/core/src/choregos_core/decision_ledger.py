# SPDX-License-Identifier: Apache-2.0
"""What the decision ledger records, and when a recorded decision was right (ADR 0121 §6, §7).

Pure rules, no I/O: the API writes and reads the ledger (`choregos_api.services.decision_ledger`),
the orchestrator feeds it, and both apply what is written here.

**What an agent's confidence is the probability of.** A decision step writes `<name>` and
`<name>_confidence` for each question, so its records are read off its questions. An agent writes
what its transition declares (ADR 0106), and a `confidence` it declares is recorded on the same
footing:

- `<name>_confidence` is the probability of the output `<name>`;
- a bare `confidence` is the probability of the output declared just before it in the transition's
  `outputs:` (`outputs: [route, confidence]` → `route`); when the transition does not declare
  `confidence`, of the output the agent wrote just before it;
- the probability is a number in [0, 1] (or a text that reads as one: agents write JSON loosely);
- the answer is a boolean, a finite number, or a text of at most `MAX_ANSWER` characters — a
  document is not an answer — and the question's name is a decision name (`^[a-z][a-z0-9_]{0,40}$`).

Anything else is not recorded: the ledger never guesses what a number was the probability of.

**When a decision is right.** A record is *observed* once something tells whether it was right: a
person contradicted it (`overridden`), the work item ended (`terminal`), or it came back after it
ended (`reopened`). An observed record is *right* when nobody contradicted it, the work item ended in
success, and it did not come back.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: The suffix of the output that carries a question's probability (`choregos_contracts.workflow`).
CONFIDENCE_SUFFIX = "_confidence"
#: An agent's bare confidence (ADR 0106): the probability of the output declared just before it.
CONFIDENCE = "confidence"
#: The longest text recorded as an answer: beyond, it is a document, not an answer to a question.
MAX_ANSWER = 200
#: A question's name, as the contract's `DecisionName`.
QUESTION_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
#: The work item's terminal outcome that makes a decision right.
SUCCESS = "success"

Answer = bool | int | float | str


@dataclass(frozen=True, slots=True)
class Answered:
    """One answered question: its name, its answer and the probability given for it."""

    question: str
    answer: Answer
    probability: float


def probability_of(value: Any) -> float | None:
    """A probability in [0, 1], from a number or a text that reads as one; `None` otherwise."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None
    if not isinstance(value, int | float) or not math.isfinite(value) or not 0.0 <= value <= 1.0:
        return None
    return float(value)


def answer_of(value: Any) -> Answer | None:
    """A recordable answer: a boolean, a finite number, a short text; `None` otherwise."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        text = value.strip()
        return text if text and len(text) <= MAX_ANSWER else None
    return None


def declares_a_confidence(outputs: Mapping[str, Any] | None) -> bool:
    """Whether a stage's outputs carry a confidence the ledger may record."""
    return any(name == CONFIDENCE or name.endswith(CONFIDENCE_SUFFIX) for name in outputs or {})


def declared_confidences(outputs: Mapping[str, Any], declared: Sequence[str] = ()) -> list[Answered]:
    """The answers an agent gave a confidence for, each with that confidence (see the module)."""
    answered: dict[str, Answered] = {}
    for name, value in outputs.items():
        if not name.endswith(CONFIDENCE_SUFFIX) or name == CONFIDENCE_SUFFIX:
            continue
        question = name.removesuffix(CONFIDENCE_SUFFIX)
        recorded = _answered(question, outputs.get(question), value)
        if recorded is not None:
            answered[question] = recorded
    if CONFIDENCE in outputs:
        order = list(declared) if CONFIDENCE in declared else list(outputs)
        position = order.index(CONFIDENCE)
        if position > 0:
            question = order[position - 1]
            if question not in answered and not question.endswith(CONFIDENCE_SUFFIX):
                recorded = _answered(question, outputs.get(question), outputs[CONFIDENCE])
                if recorded is not None:
                    answered[question] = recorded
    return list(answered.values())


def _answered(question: str, answer: Any, probability: Any) -> Answered | None:
    if not QUESTION_NAME.fullmatch(question):
        return None
    read_answer, read_probability = answer_of(answer), probability_of(probability)
    if read_answer is None or read_probability is None:
        return None
    return Answered(question, read_answer, read_probability)


def is_observed(overridden: bool | None, terminal: str | None, reopened: bool) -> bool:
    """Something tells whether the decision was right: a person, the work item's end, its return."""
    return overridden is True or terminal is not None or reopened


def is_right(overridden: bool | None, terminal: str | None, reopened: bool) -> bool | None:
    """Right: nobody contradicted it, the work item ended in success and did not come back.
    `None` while nothing is observed."""
    if not is_observed(overridden, terminal, reopened):
        return None
    return overridden is not True and terminal == SUCCESS and not reopened


__all__ = [
    "CONFIDENCE",
    "CONFIDENCE_SUFFIX",
    "MAX_ANSWER",
    "Answered",
    "answer_of",
    "declared_confidences",
    "declares_a_confidence",
    "is_observed",
    "is_right",
    "probability_of",
]
