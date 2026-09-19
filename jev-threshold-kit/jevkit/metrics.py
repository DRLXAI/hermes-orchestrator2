"""Calibration metrics, with the one thing a scalar ECE cannot tell you: the sign.

Every public Jev benchmark reports Expected Calibration Error. ECE is an
absolute value, so it says how far the probabilities are from honest without
saying which way. That distinction is the whole ballgame for a threshold:

    overconfident  ->  `p >= 0.9` accepts things that are not 90% likely.
                       You auto-act on errors. You lose money per error.
    underconfident ->  `p >= 0.9` rejects things that were fine.
                       You pay for human review you did not need. You lose the
                       savings that justified the migration.

Both read as "ECE = 0.08". They require opposite fixes. So every function here
that reports a magnitude also reports a direction.

The second trap is sample size. ECE is biased upward: a perfectly calibrated
model scores well above zero on a small sample, because binned empirical
frequencies scatter around the true probability. At n=60 a flawless model
scores ECE ~0.045, which is the range the most-cited public Jev benchmark
reports -- meaning that benchmark cannot distinguish Jev from perfect, or from
badly broken. `noise_floor` computes that baseline by simulation so a measured
ECE can be read as a multiple of it rather than as a bare number.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass


#: Equal-width buckets for every reliability measurement.
#:
#: Ten, not the fifteen the published Jev analyses use. More bins means fewer
#: items per bin, which raises the noise floor without revealing more structure:
#: measured across n = 200..2000, ten bins floors lower than fifteen at every
#: size (0.063 vs 0.075 at n=200; 0.019 vs 0.023 at n=2000). Fifteen is still
#: worth passing explicitly when the point is to line a number up against a
#: published figure, since an ECE is only comparable against the same binning.
DEFAULT_BINS = 10


@dataclass(frozen=True)
class Bin:
    """One reliability-diagram bucket."""

    lower: float
    upper: float
    count: int
    mean_confidence: float
    accuracy: float

    @property
    def gap(self) -> float:
        """Signed: positive means overconfident, negative means underconfident."""
        return self.mean_confidence - self.accuracy


@dataclass(frozen=True)
class Calibration:
    """A calibration measurement that carries its own trustworthiness."""

    ece: float
    signed_error: float
    noise_floor: float
    brier: float
    accuracy: float
    mean_confidence: float
    n: int
    bins: tuple[Bin, ...]

    @property
    def ratio(self) -> float:
        """ECE as a multiple of the noise floor. Below ~1.5 measures nothing."""
        return self.ece / self.noise_floor if self.noise_floor else float("inf")

    #: Above this, the floor is so high that real miscalibration would hide
    #: underneath it. Chosen so a 10-point confidence error stays visible.
    RESOLUTION_LIMIT = 0.05

    @property
    def measurable(self) -> bool:
        """Whether the sample can resolve miscalibration worth acting on.

        Distinct from `direction`: a tiny sample is not evidence of calibration,
        it is absence of evidence. Conflating the two is how the n=60 benchmark
        got read as a pass.
        """
        return self.noise_floor <= self.RESOLUTION_LIMIT

    @property
    def direction(self) -> str:
        """Which way the probabilities lie. This decides whether you can threshold."""
        if not self.measurable:
            return "unmeasurable"
        if self.ratio < 1.5:
            return "calibrated"
        if self.signed_error > 0.01:
            return "overconfident"
        if self.signed_error < -0.01:
            return "underconfident"
        # A large ECE with no net bias: errors cancel across bins, so the mean is
        # honest while individual regions are not. Thresholding is still unsafe.
        return "distorted"

    @property
    def trustworthy(self) -> bool:
        """Whether a raw `confidence >= t` gate can be used without a fitted map."""
        return self.measurable and self.ratio < 1.5

    def verdict(self) -> str:
        """One line a human can act on."""
        if not self.measurable:
            needed = self._n_for_resolution()
            return (
                f"UNMEASURABLE at n={self.n}: a perfect model scores "
                f"ECE~{self.noise_floor:.3f} here and you measured {self.ece:.3f}, "
                f"so the two cannot be told apart. You need roughly {needed} labelled "
                f"items before any threshold is defensible."
            )
        if self.direction == "distorted":
            return (
                f"DISTORTED ({self.ratio:.1f}x noise floor) with no net bias: the mean "
                f"is honest but individual confidence bands are not, so errors cancel "
                f"rather than cover. Fit a calibration map."
            )
        if self.direction == "overconfident":
            return (
                f"OVERCONFIDENT by {self.signed_error:+.3f} "
                f"({self.ratio:.1f}x noise floor). A raw `confidence >= t` gate will "
                f"auto-act on more errors than t implies. Fit a calibration map."
            )
        if self.direction == "underconfident":
            return (
                f"UNDERCONFIDENT by {self.signed_error:+.3f} "
                f"({self.ratio:.1f}x noise floor). A raw `confidence >= t` gate is "
                f"safe but wasteful: you will over-escalate and lose the savings."
            )
        return (
            f"CALIBRATED within noise ({self.ratio:.1f}x floor, n={self.n}). "
            f"A raw `confidence >= t` gate is defensible for this question."
        )

    def _n_for_resolution(self) -> int:
        """Sample size that would bring the noise floor under the limit.

        The floor falls as roughly 1/sqrt(n), so scale the current n by the
        square of how far over the limit we are, and round to something a human
        would actually go and collect.
        """
        factor = (self.noise_floor / self.RESOLUTION_LIMIT) ** 2
        target = int(self.n * factor)
        step = 100 if target < 2000 else 500
        return max(200, ((target + step - 1) // step) * step)


def reliability_bins(
    confidences: list[float], correct: list[bool], n_bins: int = DEFAULT_BINS
) -> tuple[Bin, ...]:
    """Bucket predictions into equal-width confidence bins.

    Equal-width (not equal-mass) because the published Jev analyses use 15
    equal-width bins on max-probability, and a comparison only means something
    against the same binning.

    Args:
        confidences: Predicted probability of being correct, one per item.
        correct: Whether each prediction was actually right.
        n_bins: Number of equal-width buckets across [0, 1].

    Returns:
        Non-empty bins, in ascending order.

    Raises:
        ValueError: If the inputs disagree in length or are empty.
    """
    if len(confidences) != len(correct):
        raise ValueError(
            f"confidences and correct must be the same length: "
            f"{len(confidences)} != {len(correct)}"
        )
    if not confidences:
        raise ValueError("cannot bin an empty sample")

    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(n_bins)]
    for confidence, hit in zip(confidences, correct):
        # Clamp so that exactly 1.0 lands in the top bin rather than out of range.
        index = min(int(confidence * n_bins), n_bins - 1)
        buckets[max(index, 0)].append((confidence, hit))

    out: list[Bin] = []
    for index, bucket in enumerate(buckets):
        if not bucket:
            continue
        out.append(
            Bin(
                lower=index / n_bins,
                upper=(index + 1) / n_bins,
                count=len(bucket),
                mean_confidence=sum(c for c, _ in bucket) / len(bucket),
                accuracy=sum(1 for _, h in bucket if h) / len(bucket),
            )
        )
    return tuple(out)


def noise_floor(confidences: list[float], trials: int = 200, n_bins: int = DEFAULT_BINS,
                seed: int = 0) -> float:
    """Mean ECE of a *perfectly calibrated* model with these same confidences.

    This is the number that makes an ECE readable. We hold the predicted
    distribution fixed and resample outcomes from it -- so every simulated model
    is calibrated by construction -- then measure the ECE anyway. What comes
    back is the error that binning and finite samples produce on their own.

    Args:
        confidences: The predicted probabilities to hold fixed.
        trials: Simulation count. 200 is stable to ~0.001 at n>=200.
        n_bins: Must match the binning used for the measured ECE.
        seed: Fixed so a report is reproducible.

    Returns:
        The expected ECE of a flawless model at this sample size.
    """
    if not confidences:
        return 0.0
    rng = random.Random(seed)
    total = 0.0
    for _ in range(trials):
        simulated = [rng.random() < c for c in confidences]
        bins = reliability_bins(confidences, simulated, n_bins=n_bins)
        n = len(confidences)
        total += sum(b.count / n * abs(b.gap) for b in bins)
    return total / trials


def assess(
    confidences: list[float], correct: list[bool], n_bins: int = DEFAULT_BINS, seed: int = 0
) -> Calibration:
    """Measure calibration, its direction, and whether the measurement is real.

    Args:
        confidences: Predicted probability of correctness, one per item.
        correct: Ground-truth correctness, one per item.
        n_bins: Equal-width bins for the reliability diagram.
        seed: Seed for the noise-floor simulation.

    Returns:
        A `Calibration` carrying magnitude, sign, and its own noise floor.
    """
    bins = reliability_bins(confidences, correct, n_bins=n_bins)
    n = len(confidences)
    ece = sum(b.count / n * abs(b.gap) for b in bins)
    # Signed error is computed over the raw sample rather than the bins so that
    # it is exactly mean(confidence) - accuracy, with no binning artefact.
    mean_confidence = sum(confidences) / n
    accuracy = sum(1 for c in correct if c) / n
    brier = sum((c - (1.0 if h else 0.0)) ** 2 for c, h in zip(confidences, correct)) / n
    return Calibration(
        ece=ece,
        signed_error=mean_confidence - accuracy,
        noise_floor=noise_floor(confidences, n_bins=n_bins, seed=seed),
        brier=brier,
        accuracy=accuracy,
        mean_confidence=mean_confidence,
        n=n,
        bins=bins,
    )
