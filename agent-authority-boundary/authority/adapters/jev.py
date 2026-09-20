"""Jev adapter: TypeSafe's System One model as an advisory narrower. Never an authority source.

Modelled on Jev's actual response shape, which differs per primitive:

  * **noul**   — a boolean question. Returns the PROBABILITY the answer is yes, as a bare float.
                 There is no confidence field. Confidence has to be derived, and the convention
                 carried from the Jev Threshold Kit is `|p - 0.5| * 2`.
  * **choice** — returns the chosen option, per-option probabilities, AND a server-supplied
                 confidence in [0, 1].
  * **score**  — returns a score, probabilities across levels, AND a server-supplied confidence.

That difference matters and is easy to get wrong. For choice and score this adapter uses the
confidence TypeSafe returns; it does NOT substitute `max(probabilities)`, which is a different
quantity. For noul it derives one and labels it derived.

The derived noul value is a SYMMETRIC DISTANCE FROM A COIN FLIP, not P(the answer is correct).
It is not comparable across questions, and a threshold fitted on one does not transfer to
another. This adapter records it; it does not interpret it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..engine import Advice
from ..verdict import Verdict
from .base import advice_from

NAME = "jev"
KINDS = ("noul", "choice", "score")


@dataclass(frozen=True)
class JevResponse:
    """The fields this adapter needs from one answered Jev question."""

    model: str
    question: str
    kind: str
    #: noul -> float probability of yes; choice -> the chosen option; score -> the score.
    value: Any
    #: Server-supplied for choice and score. Absent (None) for noul by design.
    confidence: float | None = None
    probabilities: Mapping[str, float] | None = None


def confidence_of(response: JevResponse) -> tuple[float | None, str]:
    """Return (confidence, provenance). Provenance is 'reported', 'derived' or 'unavailable'."""
    if response.kind == "noul":
        try:
            p_yes = float(response.value)
        except (TypeError, ValueError):
            return None, "unavailable"
        if not 0.0 <= p_yes <= 1.0:
            return None, "unavailable"
        return abs(p_yes - 0.5) * 2.0, "derived"
    if response.confidence is None:
        # Do NOT fall back to max(probabilities): that is a different quantity, and passing it
        # off as Jev's confidence would be exactly the kind of quiet fiction this product exists
        # to prevent.
        return None, "unavailable"
    try:
        value = float(response.confidence)
    except (TypeError, ValueError):
        return None, "unavailable"
    if not 0.0 <= value <= 1.0:
        return None, "unavailable"
    return value, "reported"


def answer_of(response: JevResponse) -> str:
    """The answer as a comparable string. A noul probability becomes true/false at 0.5."""
    if response.kind == "noul":
        try:
            return "true" if float(response.value) >= 0.5 else "false"
        except (TypeError, ValueError):
            return ""
    return str(response.value)


class JevAdapter:
    """Maps a Jev response to advice. Can only ever narrow."""

    name = NAME

    def __init__(self, *, unsafe_answers: tuple[str, ...] = ("false", "no", "unsafe")) -> None:
        self.unsafe_answers = tuple(a.lower() for a in unsafe_answers)

    def advise(self, response: Any, *, min_confidence: float | None = None) -> Advice:
        """Advise on one response.

        ESCALATE when the response is unusable or the model is unsure, STOP when it objects,
        ALLOW when it has no objection — which grants nothing on its own.
        """
        if not isinstance(response, JevResponse) or response.kind not in KINDS:
            return advice_from(NAME, verdict=Verdict.ESCALATE, answer="unusable response")

        confidence, provenance = confidence_of(response)
        answer = answer_of(response).strip().lower()

        if answer in self.unsafe_answers:
            verdict = Verdict.STOP
        elif confidence is None:
            verdict = Verdict.ESCALATE
        elif min_confidence is not None and confidence < min_confidence:
            # Low confidence narrows. High confidence never widens: it just stops narrowing.
            verdict = Verdict.ESCALATE
        else:
            verdict = Verdict.ALLOW

        return advice_from(
            NAME,
            verdict=verdict,
            model=response.model,
            question=response.question,
            answer=answer_of(response),
            confidence=confidence,
            confidence_source=provenance,
        )
