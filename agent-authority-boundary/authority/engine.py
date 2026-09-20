"""The decision engine: deterministic policy computes a ceiling, advice may only narrow it.

    policy -> available authority (ceiling) -> model recommendation -> selected action

The ceiling is computed with no model involvement at all. Advice is then folded in with
`narrowest`, which is `min` over a total order and therefore cannot raise the result. Every
Decision records both the ceiling and the final verdict so the two can be compared in evidence
and in tests.

Two checks exist because authority is not a single moment:

  * `revalidate` -- policy may not change between deciding and executing.
  * `verify_effect` -- what was ACTUALLY touched is compared against what was authorised.
    Intent declared up front is not evidence of behaviour afterwards.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from typing import Sequence

from . import scope as scope_mod
from .policy import ActionRule, Policy, is_alias
from .verdict import UNKNOWN_VERDICT, Verdict, narrowest


@dataclass(frozen=True)
class ActionRequest:
    """What an agent proposes to do. Entirely untrusted: it is the subject, not an input."""

    action: str
    resources: tuple[str, ...] = ()
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass(frozen=True)
class Advice:
    """A decision model's opinion. Advisory in the strongest sense: it can only narrow.

    An adapter returning ALLOW is NOT granting permission -- it is declining to object. The
    verdict still passes through `narrowest` against the deterministic ceiling.
    """

    adapter: str
    verdict: Verdict
    model: str = ""
    question: str = ""
    answer: str = ""
    confidence: float | None = None
    #: True only when this row carries genuine ground truth, never an operator's agreement.
    labelled: bool = False


@dataclass(frozen=True)
class Finding:
    """One deterministic guard's contribution to the ceiling."""

    guard: str
    verdict: Verdict
    reason: str


@dataclass(frozen=True)
class Decision:
    """A decision, reconstructable end to end."""

    request: ActionRequest
    verdict: Verdict
    ceiling: Verdict
    findings: tuple[Finding, ...]
    policy_fingerprint: str
    advice: Advice | None = None

    @property
    def executable(self) -> bool:
        return self.verdict is Verdict.ALLOW

    @property
    def narrowed_by_model(self) -> bool:
        return self.advice is not None and self.verdict < self.ceiling

    def explain(self) -> str:
        lines = [f"action={self.request.action} ceiling={self.ceiling} final={self.verdict}"]
        lines += [f"  [{f.guard}] {f.verdict}: {f.reason}" for f in self.findings]
        if self.advice is not None:
            lines.append(
                f"  [advice:{self.advice.adapter}] {self.advice.verdict}: "
                f"model={self.advice.model or 'unknown'} confidence={self.advice.confidence}"
            )
        return "\n".join(lines)


# --------------------------------------------------------------------------- guards


def _guard_known_action(policy: Policy, request: ActionRequest) -> tuple[Finding, ActionRule | None]:
    rule = policy.rule_for(request.action)
    if rule is None:
        return (
            Finding(
                "known_action",
                UNKNOWN_VERDICT,
                f"policy does not mention action {request.action!r}; unknown is not permission",
            ),
            None,
        )
    return Finding("known_action", rule.ceiling, f"policy allows at most {rule.ceiling}"), rule


def _guard_resource_scope(rule: ActionRule, request: ActionRequest) -> Finding:
    if rule.scope is None:
        return Finding(
            "resource_scope",
            UNKNOWN_VERDICT,
            "action declares no resource scope; a human must decide what it may touch",
        )
    try:
        patterns = scope_mod.normalize(rule.scope)
    except scope_mod.ScopeError as exc:
        return Finding("resource_scope", Verdict.STOP, f"declared scope is unusable: {exc}")
    if scope_mod.is_unbounded(patterns):
        return Finding(
            "resource_scope",
            UNKNOWN_VERDICT,
            f"declared scope ({', '.join(patterns[:3])}) matches any resource at any depth, "
            "so it authorises nothing in particular",
        )
    stray = scope_mod.outside(request.resources, patterns)
    if stray:
        return Finding(
            "resource_scope",
            Verdict.STOP,
            f"{len(stray)} resource(s) outside the declared scope: {', '.join(stray[:5])}",
        )
    return Finding("resource_scope", Verdict.ALLOW, f"{len(request.resources)} resource(s) in scope")


def _guard_reversibility(rule: ActionRule) -> Finding:
    if rule.reversible is None:
        return Finding(
            "reversibility",
            UNKNOWN_VERDICT,
            "reversibility is unknown; unknown is never read as reversible",
        )
    if not rule.reversible:
        return Finding(
            "reversibility", Verdict.REQUIRE_APPROVAL, "action is irreversible; a human must approve"
        )
    return Finding("reversibility", Verdict.ALLOW, "action is reversible")


def _guard_side_effects(rule: ActionRule) -> Finding:
    if rule.external_side_effects:
        return Finding(
            "external_side_effects",
            Verdict.REQUIRE_APPROVAL,
            "action causes effects outside the system; a human must approve",
        )
    return Finding("external_side_effects", Verdict.ALLOW, "no external side effects")


def _guard_approval(rule: ActionRule) -> Finding:
    if rule.requires_approval:
        return Finding("human_approval", Verdict.REQUIRE_APPROVAL, "policy requires human approval")
    return Finding("human_approval", Verdict.ALLOW, "no standing approval requirement")


def _guard_model_pinning(policy: Policy, advice: Advice | None) -> Finding:
    if advice is None:
        return Finding("model_pinning", Verdict.ALLOW, "no decision model consulted")
    expected = policy.pinned_models.get(advice.adapter)
    if expected is None:
        return Finding(
            "model_pinning",
            Verdict.STOP,
            f"adapter {advice.adapter!r} is not pinned in policy; an unpinned model's opinion "
            "is not admissible",
        )
    if is_alias(advice.model):
        return Finding(
            "model_pinning",
            Verdict.STOP,
            f"{advice.model!r} is a moving alias, not a pinned build",
        )
    if advice.model != expected:
        return Finding(
            "model_pinning",
            Verdict.STOP,
            f"serving model {advice.model!r} is not the pinned {expected!r}",
        )
    return Finding("model_pinning", Verdict.ALLOW, f"model matches pin {expected!r}")


def _guard_config_integrity(policy: Policy) -> Finding:
    if not policy.fingerprint:
        return Finding(
            "config_integrity", Verdict.STOP, "policy carries no fingerprint; it cannot be trusted"
        )
    return Finding("config_integrity", Verdict.ALLOW, f"policy {policy.fingerprint[:12]}")


# --------------------------------------------------------------------------- decide


def decide(policy: Policy, request: ActionRequest, advice: Advice | None = None) -> Decision:
    """Compute the deterministic ceiling, then let advice narrow it. Never the other way."""
    integrity = _guard_config_integrity(policy)
    known, rule = _guard_known_action(policy, request)
    findings: list[Finding] = [integrity, known]

    if rule is not None:
        findings += [
            _guard_resource_scope(rule, request),
            _guard_reversibility(rule),
            _guard_side_effects(rule),
            _guard_approval(rule),
        ]
    findings.append(_guard_model_pinning(policy, advice))

    ceiling = Verdict.ALLOW
    for finding in findings:
        ceiling = narrowest(ceiling, finding.verdict)

    final = narrowest(ceiling, advice.verdict) if advice is not None else ceiling
    # Structural, not decorative: `narrowest` is min over a total order, so this cannot trip.
    # It is asserted anyway because this line is the product.
    assert final <= ceiling, "advice widened authority"

    return Decision(
        request=request,
        verdict=final,
        ceiling=ceiling,
        findings=tuple(findings),
        policy_fingerprint=policy.fingerprint,
        advice=advice,
    )


def revalidate(decision: Decision, policy: Policy) -> Decision:
    """Refuse a decision whose policy changed underneath it."""
    if policy.fingerprint != decision.policy_fingerprint:
        return replace(
            decision,
            verdict=Verdict.STOP,
            findings=decision.findings
            + (
                Finding(
                    "policy_stability",
                    Verdict.STOP,
                    "policy changed between the decision and execution",
                ),
            ),
        )
    return decision


def verify_effect(
    decision: Decision, observed_resources: Sequence[str], policy: Policy
) -> Finding:
    """Compare what was ACTUALLY touched against what was authorised.

    Declared intent before the fact is not evidence of behaviour after it.
    """
    rule = policy.rule_for(decision.request.action)
    if rule is None or rule.scope is None:
        return Finding(
            "effect_scope",
            UNKNOWN_VERDICT,
            "no authorised scope to compare the observed effect against",
        )
    patterns = scope_mod.normalize(rule.scope)
    stray = scope_mod.outside(observed_resources, patterns)
    if stray:
        return Finding(
            "effect_scope",
            Verdict.STOP,
            f"{len(stray)} resource(s) touched outside the authorised scope: "
            f"{', '.join(stray[:5])}",
        )
    return Finding(
        "effect_scope", Verdict.ALLOW, f"{len(observed_resources)} observed resource(s) in scope"
    )
