"""Agent Authority Boundary.

Models can reduce authority. Models can never create authority.

Deterministic policy computes a ceiling; a decision model may only narrow it. See README.md
for the PROTECT -> OBSERVE -> CALIBRATE architecture.
"""

from __future__ import annotations

from .engine import (
    ActionRequest,
    Advice,
    Decision,
    Finding,
    decide,
    revalidate,
    verify_effect,
)
from .effects import effect_is_verified, observe_git_effect
from .evidence import EvidenceLog, Observation
from .observers.git import GitEffectObserver, GitObservation, assess_git_effect
from .policy import ActionRule, Policy, PolicyError, is_alias, load
from .verdict import UNKNOWN_VERDICT, Verdict, narrow_all, narrowest, permits_execution

__version__ = "0.1.0"

__all__ = [
    "ActionRequest",
    "GitEffectObserver",
    "GitObservation",
    "assess_git_effect",
    "effect_is_verified",
    "observe_git_effect",
    "ActionRule",
    "Advice",
    "Decision",
    "EvidenceLog",
    "Finding",
    "Observation",
    "Policy",
    "PolicyError",
    "UNKNOWN_VERDICT",
    "Verdict",
    "__version__",
    "decide",
    "is_alias",
    "load",
    "narrow_all",
    "narrowest",
    "permits_execution",
    "revalidate",
    "verify_effect",
]
