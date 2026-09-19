"""A fitted, versioned, saveable calibration profile for one question.

This is the artefact the kit exists to produce. It pins four things together
that are only meaningful as a set:

    the question      which judgment this is
    the model version which build of Jev the numbers were measured against
    the calibration   the map from raw confidence to real probability
    the threshold     the cutoff your economics chose

Splitting them is how calibration work quietly rots. A threshold copied from a
blog post is meaningless without the map. A map fitted six weeks ago is
meaningless if `jev-latest` has moved underneath it, and TypeSafe documents
that aliases do move. Keeping them in one file with the model id recorded is
what lets `jevkit.guard` notice when the ground has shifted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from .calibrate import IsotonicMap, TemperatureMap, fit_isotonic, fit_temperature, split
from .metrics import Calibration, assess
from .thresholds import Economics, ThresholdChoice, choose, operating_point

#: Bumped when the on-disk shape changes in a way older readers cannot handle.
PROFILE_VERSION = 1


@dataclass(frozen=True)
class Profile:
    """Everything needed to turn a live Jev answer into an act-or-escalate call."""

    question: str
    kind: str
    model: str
    threshold: float
    map_kind: str
    map_payload: dict[str, Any]
    n_fit: int
    n_verify: int
    raw_ece: float
    raw_direction: str
    calibrated_ece: float
    calibrated_direction: str
    coverage: float
    precision: float
    precision_lower: float
    cost_per_1k: float
    naive_cost_per_1k: float
    naive_coverage: float
    naive_precision: float
    automates: bool
    economics: dict[str, float]

    def calibrate(self, confidence: float) -> float:
        """Map a raw Jev confidence to its fitted probability of correctness."""
        if self.map_kind == "temperature":
            return TemperatureMap(
                temperature=self.map_payload["temperature"], n_train=self.n_fit
            )(confidence)
        return IsotonicMap(
            xs=tuple(self.map_payload["xs"]),
            ys=tuple(self.map_payload["ys"]),
            n_train=self.n_fit,
        )(confidence)

    def act(self, confidence: float) -> bool:
        """Whether this answer clears the fitted bar for automatic action."""
        return self.automates and self.calibrate(confidence) >= self.threshold

    @property
    def saving_per_1k(self) -> float:
        """Against shipping a reflex 0.90 cutoff on raw confidence."""
        return self.naive_cost_per_1k - self.cost_per_1k

    def save(self, path: str | Path) -> Path:
        """Write the profile as JSON. Returns the path written."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"profile_version": PROFILE_VERSION, **asdict(self)}, indent=2) + "\n"
        )
        return target

    @classmethod
    def load(cls, path: str | Path) -> Profile:
        """Read a profile written by `save`.

        Raises:
            ValueError: On a profile written by a newer, incompatible version.
        """
        payload = json.loads(Path(path).read_text())
        found = payload.pop("profile_version", 0)
        if found > PROFILE_VERSION:
            raise ValueError(
                f"profile at {path} is version {found}; this kit reads up to "
                f"{PROFILE_VERSION}. Upgrade jevkit rather than editing the file."
            )
        return cls(**payload)


def fit(
    confidences: list[float],
    correct: list[bool],
    economics: Economics,
    *,
    question: str,
    kind: str = "choice",
    model: str = "unknown",
    holdout: float = 0.3,
    seed: int = 0,
    method: str = "auto",
) -> Profile:
    """Fit a calibration map and choose a threshold, honestly.

    The map is fitted on one split and every number reported comes from the
    other. A calibration scored on its own training data always looks superb,
    which is exactly why a profile that did so would be worthless.

    Args:
        confidences: Raw Jev confidence per labelled item.
        correct: Whether Jev's answer for that item was right.
        economics: What an error and a review each cost you.
        question: The question id this profile governs.
        kind: `choice`, `score` or `noul`. Recorded, not used in the fit.
        model: The resolved model id these labels were collected under.
        holdout: Fraction reserved for verification.
        seed: Fixed so a profile reproduces exactly.
        method: `isotonic`, `temperature`, or `auto`. Auto prefers isotonic
            but falls back to temperature under 300 fitting items, where a step
            function overfits and one parameter does not.

    Returns:
        The fitted profile.

    Raises:
        ValueError: If there are too few labels to fit and verify separately.
    """
    if len(confidences) < 50:
        raise ValueError(
            f"{len(confidences)} labelled items is not enough to fit and verify a "
            f"threshold. Collect at least 50, and prefer 300+ -- below roughly "
            f"300 the noise floor swallows the effect you are trying to measure."
        )
    fit_conf, fit_correct, verify_conf, verify_correct = split(
        confidences, correct, holdout=holdout, seed=seed
    )

    chosen = method
    if method == "auto":
        chosen = "isotonic" if len(fit_conf) >= 300 else "temperature"

    if chosen == "isotonic":
        mapping: IsotonicMap | TemperatureMap = fit_isotonic(fit_conf, fit_correct)
        payload = {"xs": list(mapping.xs), "ys": list(mapping.ys)}
    elif chosen == "temperature":
        mapping = fit_temperature(fit_conf, fit_correct, seed=seed)
        payload = {"temperature": mapping.temperature}
        if mapping.interval:
            payload["interval_low"], payload["interval_high"] = mapping.interval
    else:
        raise ValueError(f"unknown method {method!r}; use isotonic, temperature or auto")

    raw = assess(verify_conf, verify_correct, seed=seed)
    calibrated_conf = [mapping(c) for c in verify_conf]
    calibrated = assess(calibrated_conf, verify_correct, seed=seed)
    decision = choose(calibrated_conf, verify_correct, economics, question=question)
    # The comparison has to be against what somebody actually ships by reflex:
    # `if answer.confidence >= 0.9`, on the RAW value Jev returned. Measuring it
    # on the calibrated value instead would compare the fitted threshold against
    # itself and report a saving of roughly zero on every question.
    naive = operating_point(verify_conf, verify_correct, 0.90, economics)

    return Profile(
        question=question,
        kind=kind,
        model=model,
        threshold=decision.best.threshold,
        map_kind=mapping.kind,
        map_payload=payload,
        n_fit=len(fit_conf),
        n_verify=len(verify_conf),
        raw_ece=raw.ece,
        raw_direction=raw.direction,
        calibrated_ece=calibrated.ece,
        calibrated_direction=calibrated.direction,
        coverage=decision.best.coverage,
        precision=decision.best.precision,
        precision_lower=decision.best.precision_lower,
        cost_per_1k=decision.best.cost_per_1k,
        naive_coverage=naive.coverage,
        naive_precision=naive.precision,
        naive_cost_per_1k=naive.cost_per_1k,
        automates=decision.worth_automating,
        economics={
            "cost_error": economics.cost_error,
            "cost_review": economics.cost_review,
            "cost_per_call": economics.cost_per_call,
        },
    )
