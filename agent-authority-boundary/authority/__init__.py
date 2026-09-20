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
from .evidence import EvidenceLog, Observation, Readiness
from .policy import ActionRule, Policy, PolicyError, is_alias, load
from .verdict import UNKNOWN_VERDICT, Verdict, narrow_all, narrowest, permits_execution

__version__ = "0.1.0"

__all__ = [
    "ActionRequest",
    "ActionRule",
    "Advice",
    "Decision",
    "EvidenceLog",
    "Finding",
    "Observation",
    "Policy",
    "PolicyError",
    "Readiness",
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
