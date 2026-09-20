"""Target-dependent calibration readiness, and the export a specialist tool consumes.

There is no universal "enough samples" number here, because there isn't one. How much evidence
you need depends entirely on the error rate you are willing to tolerate: proving you are under
20% takes a fraction of the data that proving you are under 1% does.

So the operator states a target, and this module answers ONE question against it:

    does the genuinely labelled evidence support a conclusion about that target?

It answers with a Wilson score interval on the observed error rate at the operator's confidence
level, and reports one of four states:

    TARGET_REQUIRED  no target has been set -- neither calibrated nor uncalibrated, just unanswerable
    INSUFFICIENT     the interval straddles the target; more labels are needed to say either way
    MEETS_TARGET     the whole interval sits below the target
    FAILS_TARGET     the whole interval sits above the target

FAILS_TARGET is a real answer, not an error. A system that is measurably worse than its target
is a thing the operator needs told, and it is not the same as having no evidence.

Only rows from trusted confirmers count. Concordance never appears here in any form.
"""

from __future__ import annotations

import enum
import json
import math
from dataclasses import dataclass
from pathlib import Path
from statistics import NormalDist
from typing import Any, Sequence

from .store import EvidenceStore


class ReadinessState(enum.Enum):
    TARGET_REQUIRED = "target_required"
    INSUFFICIENT = "insufficient"
    MEETS_TARGET = "meets_target"
    FAILS_TARGET = "fails_target"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class Target:
    """The operator's acceptable risk. Nothing in this package invents one."""

    #: The highest error rate the operator will accept, as a fraction.
    max_error_rate: float
    #: How sure they want to be before acting on the conclusion.
    confidence: float = 0.95

    def __post_init__(self) -> None:
        if not 0.0 < self.max_error_rate < 1.0:
            raise ValueError("max_error_rate must be between 0 and 1, exclusive")
        if not 0.5 <= self.confidence < 1.0:
            raise ValueError("confidence must be at least 0.5 and below 1")


def wilson_interval(successes: int, n: int, confidence: float) -> tuple[float, float]:
    """Two-sided Wilson score interval for a binomial proportion.

    Chosen over the normal approximation because it stays sane at small n and at proportions
    near 0 or 1, which is exactly where a young evidence set lives.
    """
    if n <= 0:
        return (0.0, 1.0)
    z = NormalDist().inv_cdf(1.0 - (1.0 - confidence) / 2.0)
    p = successes / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    # Clamp to [0,1] AND to the point estimate: floating-point noise otherwise leaves a bound
    # fractionally the wrong side of p (e.g. 2.8e-17 when there are no errors at all), which
    # would be reported to an operator as a non-zero error rate.
    lower = min(max(0.0, centre - margin), p)
    upper = max(min(1.0, centre + margin), p)
    return (lower, upper)


@dataclass(frozen=True)
class Readiness:
    """The answer, with the numbers that produced it."""

    question: str
    state: ReadinessState
    observed_count: int
    labelled_count: int
    error_count: int
    target: Target | None = None
    error_lower: float | None = None
    error_upper: float | None = None

    @property
    def can_conclude(self) -> bool:
        return self.state in (ReadinessState.MEETS_TARGET, ReadinessState.FAILS_TARGET)

    @property
    def error_rate(self) -> float | None:
        """None when there is nothing to divide by. Never 0.0 by default."""
        if not self.labelled_count:
            return None
        return self.error_count / self.labelled_count

    @property
    def reason(self) -> str:
        if self.state is ReadinessState.TARGET_REQUIRED:
            return (
                f"{self.labelled_count} labelled of {self.observed_count} observed. No target has "
                "been set, so this is neither calibrated nor uncalibrated -- the question cannot "
                "be answered until the operator states an acceptable error rate."
            )
        assert self.target is not None
        band = f"[{self.error_lower:.3f}, {self.error_upper:.3f}]"
        pct = f"{self.target.confidence:.0%}"
        if self.state is ReadinessState.INSUFFICIENT:
            return (
                f"insufficient evidence: {self.labelled_count} labelled row(s) put the error rate "
                f"in {band} at {pct} confidence, which straddles the target of "
                f"{self.target.max_error_rate:.3f}. More labels are needed to conclude either way."
            )
        if self.state is ReadinessState.MEETS_TARGET:
            return (
                f"{self.labelled_count} labelled row(s) put the error rate in {band} at {pct} "
                f"confidence, entirely below the target of {self.target.max_error_rate:.3f}."
            )
        return (
            f"{self.labelled_count} labelled row(s) put the error rate in {band} at {pct} "
            f"confidence, entirely ABOVE the target of {self.target.max_error_rate:.3f}. This is "
            "a measured failure to meet the target, not a lack of evidence."
        )


def readiness(
    store: EvidenceStore, question: str, *, target: Target | None = None
) -> Readiness:
    """Evaluate the labelled evidence for one question against the operator's target."""
    labelled = store.labelled(question)
    observed = [r for r in store.all_rows() if r.kind == "advice"
                and r.payload.get("question") == question]
    errors = sum(1 for r in labelled if r.payload.get("ground_truth") is False)
    n = len(labelled)

    if target is None:
        return Readiness(
            question=question, state=ReadinessState.TARGET_REQUIRED,
            observed_count=len(observed), labelled_count=n, error_count=errors,
        )

    lower, upper = wilson_interval(errors, n, target.confidence)
    if n == 0:
        state = ReadinessState.INSUFFICIENT
    elif upper < target.max_error_rate:
        state = ReadinessState.MEETS_TARGET
    elif lower > target.max_error_rate:
        state = ReadinessState.FAILS_TARGET
    else:
        state = ReadinessState.INSUFFICIENT

    return Readiness(
        question=question, state=state, observed_count=len(observed), labelled_count=n,
        error_count=errors, target=target, error_lower=lower, error_upper=upper,
    )


def export_dataset(store: EvidenceStore, question: str | None = None) -> list[dict[str, Any]]:
    """Labelled rows in a shape a calibration tool such as jevcal can consume.

    Only trusted labels. Concordance is never included -- it is not present in the row type at
    all, so it cannot leak by accident.
    """
    rows: list[dict[str, Any]] = []
    for label in store.labelled(question):
        chain = store.chain(label.correlation_id)
        advice = next((r for r in chain if r.kind == "advice"), None)
        confidence = label.payload.get("confidence")
        if confidence is None and advice is not None:
            confidence = advice.payload.get("confidence")
        if confidence is None:
            continue  # nothing to calibrate against
        rows.append(
            {
                "correlation_id": label.correlation_id,
                "question": label.payload.get("question", ""),
                "confidence": float(confidence),
                "correct": bool(label.payload.get("ground_truth")),
                "model": (advice.payload.get("model") if advice else "") or "",
                "confidence_source": (
                    advice.payload.get("confidence_source") if advice else ""
                ) or "",
                "answer": (advice.payload.get("answer") if advice else "") or "",
                "confirmed_by": label.source,
                "recorded_at": label.recorded_at,
            }
        )
    return rows


def write_jsonl(rows: Sequence[dict[str, Any]], path: str | Path) -> Path:
    """Write an export as JSONL. Returns the path written."""
    target = Path(path)
    target.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    return target


# ------------------------------------------------------------------ multiple comparisons
#
# Checking one question at 95% confidence means a 5% chance of a spurious MEETS_TARGET. Check
# twenty and you should expect roughly one by luck alone. Reporting "all twenty met the target"
# from twenty independent 95% tests is a claim the evidence does not support.
#
# The choice made here, deliberately: this package does NOT silently apply a correction, and it
# does NOT pretend the problem does not exist. It refuses to emit an aggregate claim unless the
# operator asks for one with an explicit correction. Bonferroni is offered because it is simple,
# assumption-free and conservative -- it holds the family-wise error rate at the stated level
# whatever the correlation between questions, which is the right trade when the cost of a false
# "we are calibrated" is an agent acting on a threshold that is not there.
#
# The limitation is machine-visible: `aggregate_claim_available` is False, with a reason, unless
# a correction was requested.


@dataclass(frozen=True)
class PortfolioReadiness:
    """Readiness across several questions, with the multiple-comparison problem made explicit."""

    results: tuple[Readiness, ...]
    correction: str
    family_confidence: float | None = None
    per_test_confidence: float | None = None

    @property
    def aggregate_claim_available(self) -> bool:
        """Whether an 'all questions meet the target' claim is statistically supported."""
        return self.correction != "none"

    @property
    def all_meet_target(self) -> bool | None:
        """True/False only when an aggregate claim is available; otherwise None (unanswerable)."""
        if not self.aggregate_claim_available:
            return None
        return all(r.state is ReadinessState.MEETS_TARGET for r in self.results)

    @property
    def reason(self) -> str:
        n = len(self.results)
        if not self.aggregate_claim_available:
            return (
                f"{n} question(s) evaluated independently. No aggregate claim is available: "
                f"{n} separate tests at the stated confidence would produce roughly "
                f"{n * 0.05:.1f} spurious passes by chance at 95%. Re-run with "
                "correction='bonferroni' to make a family-wise claim."
            )
        met = sum(1 for r in self.results if r.state is ReadinessState.MEETS_TARGET)
        return (
            f"{met}/{n} question(s) meet the target with a family-wise confidence of "
            f"{self.family_confidence:.0%} (Bonferroni: each test run at "
            f"{self.per_test_confidence:.4f})."
        )


def portfolio_readiness(
    store: EvidenceStore,
    questions: Sequence[str],
    *,
    target: Target,
    correction: str = "none",
) -> PortfolioReadiness:
    """Readiness across several questions.

    `correction="none"` (the default) evaluates each question independently and REFUSES to make
    an aggregate claim. `correction="bonferroni"` tightens each test so the family-wise error
    rate matches `target.confidence`, and then an aggregate claim is available.
    """
    if correction not in ("none", "bonferroni"):
        raise ValueError(f"unknown correction {correction!r}; use 'none' or 'bonferroni'")
    unique = list(dict.fromkeys(questions))
    if correction == "none":
        results = tuple(readiness(store, q, target=target) for q in unique)
        return PortfolioReadiness(results=results, correction="none")

    m = max(1, len(unique))
    per_test = 1.0 - (1.0 - target.confidence) / m
    tightened = Target(max_error_rate=target.max_error_rate, confidence=per_test)
    results = tuple(readiness(store, q, target=tightened) for q in unique)
    return PortfolioReadiness(
        results=results, correction="bonferroni",
        family_confidence=target.confidence, per_test_confidence=per_test,
    )


# ------------------------------------------------------------------ minimal drift monitoring
#
# Deliberately small: two questions an operator needs answered periodically, and no scheduler,
# storage or dashboard. Run it from cron, CI or a health check.


@dataclass(frozen=True)
class DriftReport:
    """Has the ground moved under a threshold that was fitted earlier?"""

    question: str
    pinned_model: str
    observed_models: tuple[str, ...]
    model_drifted: bool
    earlier: Readiness | None
    later: Readiness | None

    @property
    def error_rate_moved(self) -> bool:
        """True when the two halves' confidence intervals do not overlap at all."""
        if self.earlier is None or self.later is None:
            return False
        if None in (self.earlier.error_lower, self.later.error_lower):
            return False
        return (
            self.earlier.error_upper < self.later.error_lower
            or self.later.error_upper < self.earlier.error_lower
        )

    @property
    def ok(self) -> bool:
        return not self.model_drifted and not self.error_rate_moved

    @property
    def reason(self) -> str:
        if self.model_drifted:
            return (
                f"model drift: policy pins {self.pinned_model!r} but "
                f"{', '.join(self.observed_models)} produced advice. A threshold fitted against "
                "one build is not evidence about another."
            )
        if self.error_rate_moved:
            return (
                "the error rate moved: the earlier and later halves of the labelled evidence "
                "have non-overlapping confidence intervals."
            )
        if self.earlier is None or self.later is None:
            return "not enough labelled evidence to split into two halves; no drift signal yet"
        return "no model drift and no detected movement in the error rate"


def check_drift(
    store: EvidenceStore, policy: Any, question: str, *, target: Target, adapter: str = "jev"
) -> DriftReport:
    """Compare the models that actually answered against the pin, and early evidence against late.

    Minimal by design: two signals, no scheduling, no history table.
    """
    pinned = dict(getattr(policy, "pinned_models", {})).get(adapter, "")
    seen = tuple(
        dict.fromkeys(
            str(r.payload.get("model") or "")
            for r in store.all_rows()
            if r.kind == "advice" and r.payload.get("question") == question
            and r.payload.get("model")
        )
    )
    drifted = bool(pinned) and any(model != pinned for model in seen)

    labelled = sorted(store.labelled(question), key=lambda r: r.recorded_at)
    earlier = later = None
    if len(labelled) >= 4:
        half = len(labelled) // 2
        earlier = _readiness_of(labelled[:half], question, target)
        later = _readiness_of(labelled[half:], question, target)

    return DriftReport(
        question=question, pinned_model=pinned, observed_models=seen, model_drifted=drifted,
        earlier=earlier, later=later,
    )


def _readiness_of(rows: Sequence[Any], question: str, target: Target) -> Readiness:
    """Readiness over an explicit row subset, for the two halves of a drift comparison."""
    errors = sum(1 for r in rows if r.payload.get("ground_truth") is False)
    lower, upper = wilson_interval(errors, len(rows), target.confidence)
    state = (
        ReadinessState.MEETS_TARGET if upper < target.max_error_rate
        else ReadinessState.FAILS_TARGET if lower > target.max_error_rate
        else ReadinessState.INSUFFICIENT
    )
    return Readiness(
        question=question, state=state, observed_count=len(rows), labelled_count=len(rows),
        error_count=errors, target=target, error_lower=lower, error_upper=upper,
    )
