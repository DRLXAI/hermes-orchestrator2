"""The adapter contract.

An adapter turns some decision model's output into an `Advice`. That is ALL it can do. The
engine folds advice in with `narrowest`, so the most permissive thing any adapter can express
is ALLOW, which means "I have no objection" -- never "I grant this".

Adapters are deliberately not given the policy. A model that cannot see the authority envelope
cannot argue with it.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable

from ..engine import Advice
from ..verdict import Verdict


@runtime_checkable
class DecisionAdapter(Protocol):
    """Anything that can offer a bounded opinion about a proposed action."""

    name: str

    def advise(self, response: Any) -> Advice:
        """Translate a model response into advice. Must never raise on hostile input."""
        ...


def advice_from(
    adapter: str,
    *,
    verdict: Verdict,
    model: str = "",
    question: str = "",
    answer: str = "",
    confidence: float | None = None,
    confidence_source: str = "",
    labelled: bool = False,
) -> Advice:
    """Build advice, clamping it to the advisory ceiling.

    `Verdict.ALLOW` is the ceiling for any adapter: no adapter can express more than
    "no objection". This is belt and braces -- the engine would clamp it anyway.
    """
    return Advice(
        adapter=adapter,
        verdict=Verdict(min(int(verdict), int(Verdict.ALLOW))),
        model=model,
        question=question,
        answer=answer,
        confidence=confidence,
        confidence_source=confidence_source,
        labelled=labelled,
    )


def observed_only(payload: Mapping[str, Any]) -> bool:
    """Whether a recorded row carries genuine ground truth.

    An operator agreeing with a recommendation is NOT ground truth, so agreement fields are
    deliberately not consulted here.
    """
    return bool(payload.get("labelled")) and payload.get("ground_truth") is not None
