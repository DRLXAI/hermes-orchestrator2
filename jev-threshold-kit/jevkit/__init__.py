"""Jev Threshold Kit -- fit confidence thresholds you can bet money on.

Jev returns typed answers with probabilities. Those probabilities are monotone
but not calibrated, and the direction of the error flips with the primitive:
published measurements put Choice and Score overconfident (T~3.3-3.4) and Noul
underconfident (T~0.66) on the same inputs. So `if confidence >= 0.9` is unsafe
in one direction and wasteful in the other, and which one you got depends on
the question type rather than on anything visible in the response.

This kit measures that on your data and hands back a threshold with the money
attached.

    from jevkit import Economics, fit

    profile = fit(confidences, correct, Economics(cost_error=8.0, cost_review=0.60),
                  question="refund_eligible", model="typesafe/jev-1.13-20260917")
    profile.save("calibration/refund_eligible.json")

    if profile.act(answer.confidence):
        auto_refund()
    else:
        escalate()
"""

from .calibrate import (
    IsotonicMap,
    TemperatureMap,
    fit_isotonic,
    fit_temperature,
    split,
)
from .client import (
    Answer,
    Decision,
    JevClient,
    JevError,
    MissingKeyError,
    Provider,
    choice,
    noul,
    resolve_provider,
    score,
)
from .guard import DriftCheck, ModelDriftError, check, is_alias
from .metrics import Bin, Calibration, assess, noise_floor, reliability_bins
from .profile import Profile, fit
from .thresholds import Economics, Operating, ThresholdChoice, choose, operating_point

__version__ = "1.0.0"

__all__ = [
    "Answer", "Bin", "Calibration", "Decision", "DriftCheck", "Economics",
    "IsotonicMap", "JevClient", "JevError", "MissingKeyError", "ModelDriftError",
    "Operating", "Profile", "Provider", "TemperatureMap", "ThresholdChoice",
    "assess", "check", "choice", "choose", "fit", "fit_isotonic", "fit_temperature",
    "is_alias", "noise_floor", "noul", "operating_point", "reliability_bins",
    "resolve_provider", "score", "split", "__version__",
]
