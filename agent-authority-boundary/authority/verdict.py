"""The authority lattice.

Every decision in this system resolves to one of four verdicts. They are TOTALLY ORDERED by
permissiveness, and that ordering is the product's central safety property:

    STOP (0)  <  ESCALATE (1)  <  REQUIRE_APPROVAL (2)  <  ALLOW (3)

Deterministic policy computes a ceiling. Every other participant -- a decision model, a
confidence score, a classifier, a reviewer's opinion -- may only ever move the verdict DOWN
this lattice. Combination is `min`. There is no operation in this package that moves a verdict
up, which is why "models can never create authority" is a structural property here rather than
a rule someone has to remember to check.

UNKNOWN is deliberately not a verdict. Missing trusted information resolves to ESCALATE: a
human can supply what is missing. It never resolves to ALLOW, and it is never silently read
as False.
"""

from __future__ import annotations

import enum
from typing import Iterable


@enum.unique
class Verdict(enum.IntEnum):
    """Ordered by permissiveness. Higher means more authority."""

    STOP = 0
    ESCALATE = 1
    REQUIRE_APPROVAL = 2
    ALLOW = 3

    def __str__(self) -> str:
        return self.name


#: The verdict a request gets when trusted policy information is missing. Never ALLOW.
UNKNOWN_VERDICT = Verdict.ESCALATE


def narrowest(*verdicts: Verdict) -> Verdict:
    """The least permissive of the given verdicts.

    This is the ONLY sanctioned way to combine verdicts. It cannot widen, because `min` over a
    total order cannot exceed any of its inputs.
    """
    if not verdicts:
        raise ValueError("narrowest() needs at least one verdict")
    return Verdict(min(int(v) for v in verdicts))


def narrow_all(verdicts: Iterable[Verdict], *, ceiling: Verdict) -> Verdict:
    """Apply a sequence of verdicts to a ceiling. The result never exceeds the ceiling."""
    result = ceiling
    for verdict in verdicts:
        result = narrowest(result, verdict)
    return result


def permits_execution(verdict: Verdict) -> bool:
    """Only ALLOW executes without further human involvement."""
    return verdict is Verdict.ALLOW
