"""Binding an independent observation into the evidence chain.

The chain a decision reconstructs to:

    trusted policy -> fingerprint -> authority ceiling -> advisory observation
    -> final narrowed verdict -> approval if required -> INDEPENDENTLY OBSERVED EFFECT
    -> observed outcome -> confirmed ground truth

This module joins the observer to the store in the one way that keeps the property intact: the
observation is recorded under the OBSERVER's name, so the store's trust lookup decides whether
it counts. Naming the agent as the observer produces an untrusted row and an unverifiable
result, which is the correct outcome and not something this helper will paper over.
"""

from __future__ import annotations

from typing import Sequence

from .engine import Decision, Finding
from .observers.git import GitEffectObserver, GitObservation, assess_git_effect
from .policy import Policy
from .store import EvidenceStore
from .verdict import UNKNOWN_VERDICT, Verdict, narrowest


def observe_git_effect(
    store: EvidenceStore,
    decision: Decision,
    observer: GitEffectObserver,
    *,
    baseline: str,
    policy: Policy,
    declared_resources: Sequence[str] | None = None,
    allow_dirty: bool = False,
) -> tuple[GitObservation, Finding]:
    """Observe, record and assess one decision's effect. Returns (observation, finding).

    The finding is already clamped against the decision's verdict: an observation can confirm
    or contradict compliance, but the result is never more permissive than what policy allowed
    before the agent ran.
    """
    observation = observer.observe(baseline)
    rule = policy.rule_for(decision.request.action)
    authorised = rule.scope if rule is not None else None

    finding = assess_git_effect(
        observation, authorised, declared_resources=declared_resources, allow_dirty=allow_dirty
    )
    row = store.record_effect(
        decision.request.correlation_id,
        resources=list(observation.touched),
        observer=observer.name,
        detail=observation.as_payload(),
    )
    if not row.trusted:
        return observation, Finding(
            "git_effect",
            UNKNOWN_VERDICT,
            f"observer {observer.name!r} is not a configured trusted source, so its report is "
            "not admissible as evidence of what happened",
        )
    # Clamped, so a clean observation can never lift the ceiling the decision already had.
    clamped = narrowest(decision.verdict, finding.verdict)
    if clamped is not finding.verdict:
        return observation, Finding(
            finding.guard, clamped,
            f"{finding.reason} (clamped to the decision's verdict {decision.verdict})",
        )
    return observation, finding


def effect_is_verified(finding: Finding) -> bool:
    """True only when an admissible observation actively confirmed compliance."""
    return finding.verdict is Verdict.ALLOW
