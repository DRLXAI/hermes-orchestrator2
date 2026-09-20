"""Choose the confidence cutoff that costs you the least money.

A threshold is an economic decision wearing a statistical costume. Everything
above it you act on automatically and eat the cost of being wrong; everything
below it you send to a human or a slower model and pay the review cost. The
right cutoff is wherever those two costs balance, and that point moves with the
stakes of the individual question -- which is why one global `0.85` across every
question in a workload is the most common and most expensive mistake here.

Two numbers per question, in your currency:

    cost_error   what one wrong auto-action costs. A misrouted ticket is
                 minutes. An auto-approved refund is the refund.
    cost_review  what one escalation costs. Usually agent minutes, or an LLM
                 call that is 400x the price of the Jev call.

Their *ratio* is what selects the threshold, so rough numbers are fine. An
order of magnitude wrong matters; a factor of two mostly does not.

Everything here reports a Wilson lower bound alongside each point estimate.
Precision measured at a high threshold is measured on few items by
construction, so the point estimate is the least reliable exactly where the
stakes are highest. A 0.97 on 40 covered items has a lower bound near 0.87, and
it is the 0.87 you should be budgeting against.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def wilson_lower(successes: int, trials: int, z: float = 1.645) -> float:
    """Lower end of a Wilson score interval. Default z is a 95% one-sided bound.

    Wilson rather than the textbook normal interval because the normal one is
    badly wrong exactly where thresholds live: near p=1 with small n it can
    produce a lower bound above 1, or a negative one.

    Args:
        successes: Correct predictions among the covered items.
        trials: Covered items.
        z: Normal quantile. 1.645 is one-sided 95%.

    Returns:
        A conservative estimate of true precision, or 0.0 when nothing covered.
    """
    if trials <= 0:
        return 0.0
    p = successes / trials
    denominator = 1.0 + z * z / trials
    centre = p + z * z / (2.0 * trials)
    spread = z * math.sqrt(p * (1.0 - p) / trials + z * z / (4.0 * trials * trials))
    return max(0.0, (centre - spread) / denominator)


@dataclass(frozen=True)
class Economics:
    """What being wrong and being cautious each cost you, per decision."""

    cost_error: float
    cost_review: float
    cost_per_call: float = 0.0

    def __post_init__(self) -> None:
        if self.cost_error < 0 or self.cost_review < 0 or self.cost_per_call < 0:
            raise ValueError("costs cannot be negative")
        if self.cost_error == 0 and self.cost_review == 0:
            raise ValueError(
                "both costs are zero, so every threshold is equally good; "
                "supply at least the ratio of error cost to review cost"
            )


@dataclass(frozen=True)
class Operating:
    """What one threshold would do to your traffic and your bill."""

    threshold: float
    coverage: float
    precision: float
    precision_lower: float
    covered: int
    errors: int
    cost_per_1k: float
    cost_per_1k_upper: float

    @property
    def automates(self) -> bool:
        """Whether this cutoff actually sends any traffic down the automatic path."""
        return self.covered > 0

    def describe(self) -> str:
        return (
            f"t={self.threshold:.2f}  auto {self.coverage:6.1%}  "
            f"precision {self.precision:6.2%} (>= {self.precision_lower:.2%})  "
            f"${self.cost_per_1k:8.2f}/1k"
        )


@dataclass(frozen=True)
class ThresholdChoice:
    """The chosen cutoff, and what choosing it naively would have cost."""

    best: Operating
    naive: Operating
    curve: tuple[Operating, ...]
    economics: Economics
    question: str
    #: Cost per 1,000 of escalating every single decision. The floor any
    #: automatic path has to beat to be worth switching on at all.
    escalate_all_per_1k: float = 0.0

    @property
    def worth_automating(self) -> bool:
        """Whether any cutoff beats simply escalating everything."""
        return self.best.automates

    @property
    def saving_per_1k(self) -> float:
        """Money the fitted threshold saves over a 0.90 cutoff on the CALIBRATED probability.

        Note the baseline: `choose()` is handed calibrated probabilities, so this compares the
        fitted cutoff against a reflex cutoff in the same calibrated space, and is near zero by
        construction. The headline number a user cares about is `Profile.saving_per_1k`, which
        compares against 0.90 on the RAW confidence -- what people actually ship. Both are
        correct; only the second is a claim about money.
        """
        return self.naive.cost_per_1k - self.best.cost_per_1k

    #: Explicit alias. Same value, unambiguous name.
    @property
    def saving_vs_calibrated_090_per_1k(self) -> float:
        return self.saving_per_1k

    def verdict(self) -> str:
        if not self.worth_automating:
            return (
                f"{self.question}: DO NOT AUTOMATE.\n"
                f"  No cutoff beats escalating everything at "
                f"${self.escalate_all_per_1k:.2f} per 1,000. With an error costing "
                f"${self.economics.cost_error:,.2f} against a review at "
                f"${self.economics.cost_review:,.2f} "
                f"({self.economics.cost_error / max(self.economics.cost_review, 1e-9):.0f}:1), "
                f"this question is not accurate enough to pay for its own mistakes.\n"
                f"  Options: split it into narrower questions, raise the review "
                f"budget, or keep it on the human path and automate a cheaper one."
            )
        lines = [
            f"{self.question}: act automatically at confidence >= {self.best.threshold:.2f}",
            f"  covers {self.best.coverage:.1%} of traffic at "
            f"{self.best.precision:.2%} precision "
            f"(worst case {self.best.precision_lower:.2%} on {self.best.covered} items)",
            f"  ${self.best.cost_per_1k:.2f} per 1,000 decisions, "
            f"budget ${self.best.cost_per_1k_upper:.2f} for the worst case",
        ]
        if abs(self.saving_per_1k) >= 0.005:
            if self.saving_per_1k > 0:
                lines.append(
                    f"  the reflex threshold (0.90 raw) would cost "
                    f"${self.naive.cost_per_1k:.2f} per 1,000 "
                    f"-- ${self.saving_per_1k:.2f} more"
                )
            else:
                lines.append(
                    f"  the reflex threshold (0.90 raw) happens to be cheaper here "
                    f"by ${-self.saving_per_1k:.2f} per 1,000; the fitted cutoff is "
                    f"still the one that holds when traffic shifts"
                )
        return "\n".join(lines)


def operating_point(
    probabilities: list[float],
    correct: list[bool],
    threshold: float,
    economics: Economics,
) -> Operating:
    """Evaluate one cutoff against a labelled sample.

    Args:
        probabilities: Calibrated probability of correctness, one per item.
        correct: Ground truth, one per item.
        threshold: Act automatically at or above this value.
        economics: Your cost of an error and of a review.

    Returns:
        Coverage, precision with a lower bound, and cost per 1,000 decisions.

    Raises:
        ValueError: If the inputs disagree in length or are empty.
    """
    if len(probabilities) != len(correct):
        raise ValueError(
            f"probabilities and correct must be the same length: "
            f"{len(probabilities)} != {len(correct)}"
        )
    if not probabilities:
        raise ValueError("cannot evaluate a threshold on an empty sample")

    n = len(probabilities)
    covered = [hit for p, hit in zip(probabilities, correct) if p >= threshold]
    hits = sum(1 for hit in covered if hit)
    n_covered = len(covered)
    coverage = n_covered / n
    # An empty covered set has no precision. Reporting 1.0 would be a lie that
    # wins every comparison: a threshold nothing clears looks flawless. Report
    # 0.0 and let `automates` carry the distinction, so selection has to decide
    # about escalate-everything explicitly rather than drift into it.
    precision = hits / n_covered if n_covered else 0.0
    lower = wilson_lower(hits, n_covered) if n_covered else 0.0

    def cost_at(prec: float) -> float:
        # Escalated items are assumed handled correctly, which is the point of
        # escalating: the review cost is what buys that. Per 1,000 decisions.
        auto_errors = coverage * (1.0 - prec) if n_covered else 0.0
        return 1000.0 * (
            auto_errors * economics.cost_error
            + (1.0 - coverage) * economics.cost_review
            + economics.cost_per_call
        )

    return Operating(
        threshold=threshold,
        coverage=coverage,
        precision=precision,
        precision_lower=lower,
        covered=n_covered,
        errors=n_covered - hits,
        cost_per_1k=cost_at(precision),
        cost_per_1k_upper=cost_at(lower),
    )


def choose(
    probabilities: list[float],
    correct: list[bool],
    economics: Economics,
    *,
    question: str = "question",
    grid: int = 99,
    conservative: bool = True,
) -> ThresholdChoice:
    """Sweep every cutoff and pick the cheapest.

    Args:
        probabilities: Calibrated probability of correctness, one per item.
        correct: Ground truth, one per item.
        economics: Cost of an error versus a review.
        question: Label used in the report.
        grid: Cutoffs tried across (0, 1).
        conservative: Optimise the Wilson-bounded cost rather than the point
            estimate. This is the default because the point estimate is most
            optimistic exactly where coverage is thinnest, so optimising it
            reliably selects a threshold too high to trust.

    Returns:
        The chosen cutoff, the naive 0.90 comparison, and the whole curve.
    """
    curve = tuple(
        operating_point(probabilities, correct, (i + 1) / (grid + 1), economics)
        for i in range(grid)
    )
    key = (lambda o: o.cost_per_1k_upper) if conservative else (lambda o: o.cost_per_1k)
    # Escalating everything is the honest floor: no automatic path, no errors,
    # review cost on every item. A cutoff only earns its place by beating it.
    escalate_all = 1000.0 * (economics.cost_review + economics.cost_per_call)

    automating = [point for point in curve if point.automates]
    # Ties break toward the lower threshold, which covers more traffic for the
    # same modelled cost.
    if automating and min(key(p) for p in automating) < escalate_all:
        best = min(automating, key=lambda o: (key(o), o.threshold))
    else:
        # Above the top of the grid: nothing is auto-acted on.
        best = operating_point(probabilities, correct, 1.01, economics)

    return ThresholdChoice(
        best=best,
        naive=operating_point(probabilities, correct, 0.90, economics),
        curve=curve,
        economics=economics,
        question=question,
        escalate_all_per_1k=escalate_all,
    )
