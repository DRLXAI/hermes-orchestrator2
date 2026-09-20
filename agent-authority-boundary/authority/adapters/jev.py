"""Jev adapter: TypeSafe's System One model as an advisory narrower.

Jev returns typed values with confidence rather than text, which makes it a good fit for a
bounded opinion. It is never the authority source here.

One property matters for anyone reading confidence off this adapter: for a boolean (`noul`)
question the confidence is SYNTHESISED from the probability split as `|p_yes - 0.5| * 2`. It is
a symmetric measure of how far from a coin-flip the answer sits -- NOT P(answer is correct).
Two different questions' confidences are not comparable, and a threshold fitted on one does not
transfer to another. This adapter records it rather than interpreting it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..engine import Advice
from ..verdict import Verdict
from .base import advice_from

NAME = "jev"


@dataclass(frozen=True)
class JevResponse:
    """The fields this adapter needs from a Jev call."""

    model: str
    question: str
    answer: Any
    probabilities: Mapping[str, float]
    kind: str = "noul"


def confidence_of(response: JevResponse) -> float | None:
    """The kit's confidence convention, carried over deliberately and documented above."""
    probs = response.probabilities or {}
    if not probs:
        return None
    if response.kind == "noul":
        p_yes = probs.get("true", probs.get("yes"))
        if p_yes is None:
            return None
        return abs(float(p_yes) - 0.5) * 2.0
    return max(float(v) for v in probs.values())


class JevAdapter:
    """Maps a Jev response to advice. Can only ever narrow."""

    name = NAME

    def __init__(self, *, unsafe_answers: tuple[str, ...] = ("false", "no", "unsafe")) -> None:
        #: Answers that mean "do not do this". Compared case-insensitively.
        self.unsafe_answers = tuple(a.lower() for a in unsafe_answers)

    def advise(self, response: Any, *, min_confidence: float | None = None) -> Advice:
        """Advise on one response.

        Returns ESCALATE when the model is unusable or unsure, STOP when it objects, and ALLOW
        when it has no objection -- which grants nothing on its own.
        """
        if not isinstance(response, JevResponse):
            return advice_from(NAME, verdict=Verdict.ESCALATE, answer="unusable response")
        confidence = confidence_of(response)
        answer = str(response.answer).strip().lower()

        verdict = Verdict.ALLOW
        if answer in self.unsafe_answers:
            verdict = Verdict.STOP
        elif confidence is None:
            verdict = Verdict.ESCALATE
        elif min_confidence is not None and confidence < min_confidence:
            # Low confidence narrows. High confidence never widens: it just stops narrowing.
            verdict = Verdict.ESCALATE

        return advice_from(
            NAME,
            verdict=verdict,
            model=response.model,
            question=response.question,
            answer=str(response.answer),
            confidence=confidence,
        )
