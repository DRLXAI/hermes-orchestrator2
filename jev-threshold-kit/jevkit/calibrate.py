"""Turn Jev's raw confidence into a probability you can put money behind.

The published evidence says Jev's numbers are monotone but not honest: higher
means more likely, yet the value itself is off, and off in a direction that
flips with the question type. The fix is not to argue with the model. It is to
learn the map from what it says to what actually happens, on your data.

Two fitters, because they fail differently:

`IsotonicMap` is nonparametric. It assumes only that higher confidence means
more likely correct, and otherwise lets the data say whatever it wants. That
matters here specifically, because the distortion reported for Jev is
*compression toward the middle* -- low probabilities overstated and high ones
understated at the same time. A single temperature cannot express that shape;
it can only stretch or squash the whole curve one way. Isotonic can.

`TemperatureMap` fits one number. It is worse at odd shapes and better when you
have few labels, because one parameter cannot overfit 200 points the way a step
function can. It is also the thing to report when you want to compare against
published figures, which quote temperatures: T>1 means the model was
overconfident and needed cooling, T<1 means underconfident.

Fit on one split, judge on another. A calibration map scored on its own
training data always looks excellent and tells you nothing.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass


def _clamp(p: float, eps: float = 1e-6) -> float:
    """Keep a probability strictly inside (0, 1) so logit stays finite."""
    return min(max(p, eps), 1.0 - eps)


def _logit(p: float) -> float:
    q = _clamp(p)
    return math.log(q / (1.0 - q))


def _sigmoid(x: float) -> float:
    # Branch to avoid overflow in exp for large |x|.
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


@dataclass(frozen=True)
class IsotonicMap:
    """A monotone step function from raw confidence to fitted probability.

    Stored as the breakpoints of the fitted curve. Lookup is a binary search,
    and values between breakpoints are linearly interpolated so the map is
    continuous -- a bare step function produces a discontinuity exactly where
    thresholds tend to land.
    """

    xs: tuple[float, ...]
    ys: tuple[float, ...]
    n_train: int

    def __call__(self, confidence: float) -> float:
        """Map a raw confidence to its fitted probability of correctness."""
        if not self.xs:
            return confidence
        if confidence <= self.xs[0]:
            return self.ys[0]
        if confidence >= self.xs[-1]:
            return self.ys[-1]
        low, high = 0, len(self.xs) - 1
        while high - low > 1:
            mid = (low + high) // 2
            if self.xs[mid] <= confidence:
                low = mid
            else:
                high = mid
        span = self.xs[high] - self.xs[low]
        if span <= 0:
            return self.ys[low]
        weight = (confidence - self.xs[low]) / span
        return self.ys[low] + weight * (self.ys[high] - self.ys[low])

    @property
    def kind(self) -> str:
        return "isotonic"


@dataclass(frozen=True)
class TemperatureMap:
    """Divide the logit by one learned constant.

    `temperature` > 1 cools an overconfident model, < 1 sharpens an
    underconfident one. This is the number published Jev analyses quote
    (Choice/Score ~3.3-3.4, Noul ~0.66), so it is the one to compare against.

    `interval` is why this carries a confidence interval at all. The estimator
    is consistent but noisy on the sample sizes people actually have: fitting a
    known T=3.30 recovers 2.32 at n=800, 3.15 at n=5,000 and 3.28 at n=40,000.
    Quoting a bare "T=2.3" against a published "T=3.3" invites the wrong
    conclusion -- that your workload differs -- when the honest reading is that
    800 labels cannot separate the two.
    """

    temperature: float
    n_train: int
    interval: tuple[float, float] | None = None

    @property
    def agrees_with(self) -> str:
        """Which published direction this fit is consistent with, given its CI."""
        if self.interval is None:
            return "no interval fitted"
        low, high = self.interval
        if low > 1.0:
            return "overconfident, like published Choice/Score (T~3.3-3.4)"
        if high < 1.0:
            return "underconfident, like published Noul (T~0.66)"
        return "interval spans T=1.0: this sample cannot establish a direction"

    def __call__(self, confidence: float) -> float:
        """Map a raw confidence to its fitted probability of correctness."""
        return _sigmoid(_logit(confidence) / self.temperature)

    @property
    def kind(self) -> str:
        return "temperature"

    @property
    def reading(self) -> str:
        """What the fitted temperature says about the raw model."""
        if self.temperature > 1.15:
            return f"overconfident (T={self.temperature:.2f}, needed cooling)"
        if self.temperature < 0.87:
            return f"underconfident (T={self.temperature:.2f}, needed sharpening)"
        return f"near-calibrated (T={self.temperature:.2f})"


def fit_isotonic(confidences: list[float], correct: list[bool]) -> IsotonicMap:
    """Pool-adjacent-violators fit of a monotone confidence -> probability map.

    PAVA is exact and runs in one pass: sort by confidence, then repeatedly
    merge any adjacent block whose mean is lower than the block before it, until
    the sequence of block means is non-decreasing.

    Args:
        confidences: Raw Jev confidence (or noul) per item.
        correct: Whether that item's answer was actually right.

    Returns:
        The fitted monotone map.

    Raises:
        ValueError: If the inputs disagree in length or are empty.
    """
    if len(confidences) != len(correct):
        raise ValueError(
            f"confidences and correct must be the same length: "
            f"{len(confidences)} != {len(correct)}"
        )
    if not confidences:
        raise ValueError("cannot fit a calibration map on an empty sample")

    points = sorted(zip(confidences, correct), key=lambda pair: pair[0])

    # Each block carries (sum of outcomes, count, max x). Merging is on sums so
    # the pooled mean stays exact rather than averaging averages.
    blocks: list[list[float]] = []
    for x, hit in points:
        blocks.append([1.0 if hit else 0.0, 1.0, x])
        while len(blocks) > 1:
            last, prev = blocks[-1], blocks[-2]
            if prev[0] / prev[1] <= last[0] / last[1]:
                break
            blocks[-2] = [prev[0] + last[0], prev[1] + last[1], last[2]]
            blocks.pop()

    xs = tuple(block[2] for block in blocks)
    ys = tuple(block[0] / block[1] for block in blocks)
    return IsotonicMap(xs=xs, ys=ys, n_train=len(points))


def fit_temperature(
    confidences: list[float],
    correct: list[bool],
    *,
    tolerance: float = 1e-4,
    bootstrap: int = 200,
    seed: int = 0,
) -> TemperatureMap:
    """Find the single temperature minimising negative log-likelihood.

    NLL in log-temperature is unimodal, so a ternary search converges without
    needing a gradient. Searching over log T rather than T keeps the step size
    proportional -- 0.5 to 1.0 is the same move as 1.0 to 2.0.

    Args:
        confidences: Raw Jev confidence per item.
        correct: Whether that item's answer was actually right.
        tolerance: Convergence width in log-temperature.
        bootstrap: Resamples for the 90% interval. 0 skips it.
        seed: Fixed so the interval reproduces.

    Returns:
        The fitted one-parameter map, with a 90% interval when `bootstrap` > 0.

    Raises:
        ValueError: If the inputs disagree in length or are empty.
    """
    if len(confidences) != len(correct):
        raise ValueError(
            f"confidences and correct must be the same length: "
            f"{len(confidences)} != {len(correct)}"
        )
    if not confidences:
        raise ValueError("cannot fit a temperature on an empty sample")

    logits = [_logit(c) for c in confidences]
    outcomes = [1.0 if hit else 0.0 for hit in correct]

    def nll(log_t: float) -> float:
        t = math.exp(log_t)
        total = 0.0
        for z, y in zip(logits, outcomes):
            p = _clamp(_sigmoid(z / t))
            total -= y * math.log(p) + (1.0 - y) * math.log(1.0 - p)
        return total

    # T in [0.05, 20] covers every temperature the Jev literature reports with
    # room either side; the optimum is interior for any real sample.
    low, high = math.log(0.05), math.log(20.0)
    while high - low > tolerance:
        a = low + (high - low) / 3.0
        b = high - (high - low) / 3.0
        if nll(a) < nll(b):
            high = b
        else:
            low = a
    point = math.exp((low + high) / 2.0)

    interval: tuple[float, float] | None = None
    if bootstrap > 0 and len(confidences) >= 30:
        rng = random.Random(seed)
        n = len(confidences)
        draws: list[float] = []
        for _ in range(bootstrap):
            picks = [rng.randrange(n) for _ in range(n)]
            resampled = fit_temperature(
                [confidences[i] for i in picks],
                [correct[i] for i in picks],
                tolerance=tolerance,
                bootstrap=0,
            )
            draws.append(resampled.temperature)
        draws.sort()
        interval = (
            draws[int(0.05 * len(draws))],
            draws[min(int(0.95 * len(draws)), len(draws) - 1)],
        )

    return TemperatureMap(temperature=point, n_train=len(confidences), interval=interval)


def split(
    confidences: list[float],
    correct: list[bool],
    *,
    holdout: float = 0.3,
    seed: int = 0,
) -> tuple[list[float], list[bool], list[float], list[bool]]:
    """Shuffle once and cut into fit and verify halves.

    Args:
        confidences: Raw confidence per item.
        correct: Ground truth per item.
        holdout: Fraction reserved for verification.
        seed: Fixed so a report reproduces exactly.

    Returns:
        `(fit_conf, fit_correct, verify_conf, verify_correct)`.

    Raises:
        ValueError: If either side of the split would be empty.
    """
    if not 0.0 < holdout < 1.0:
        raise ValueError(f"holdout must be strictly between 0 and 1, got {holdout}")
    paired = list(zip(confidences, correct))
    random.Random(seed).shuffle(paired)
    cut = int(len(paired) * (1.0 - holdout))
    if cut == 0 or cut == len(paired):
        raise ValueError(
            f"a {holdout:.0%} holdout of {len(paired)} items leaves an empty side; "
            f"collect more labels or lower the holdout"
        )
    fit_part, verify_part = paired[:cut], paired[cut:]
    return (
        [c for c, _ in fit_part],
        [h for _, h in fit_part],
        [c for c, _ in verify_part],
        [h for _, h in verify_part],
    )
