#!/usr/bin/env python3
"""A worked integration: an agent that edits docs, wrapped in an authority boundary.

Run it:  python3 examples/guarded_agent.py

It walks the whole chain for two actions -- one the policy permits, one it does not -- and
shows the three places an integration usually goes wrong:

  1. the agent asks for something outside its scope and is refused deterministically;
  2. a highly confident model cannot lift a ceiling;
  3. the agent's own account of what it touched is not accepted as evidence.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authority.adapters.jev import JevAdapter, JevResponse
from authority.calibration import Target, readiness
from authority.engine import ActionRequest, decide, verify_effect
from authority.policy import load
from authority.review import confirm_outcome
from authority.store import EvidenceStore

POLICY_DOC = {
    "actions": {
        "edit_doc": {
            "ceiling": "ALLOW", "scope": ["docs/**"], "tools": ["read", "write"],
            "reversible": True,
        },
        "publish": {
            "ceiling": "ALLOW", "scope": ["docs/**"], "tools": ["read", "write", "http"],
            "reversible": False, "external_side_effects": True, "requires_approval": True,
        },
    },
    "pinned_models": {"jev": "jev-1.13"},
    "trusted_sources": ["policy", "david", "fs-observer"],
}


def observe_filesystem(paths: list[str]) -> list[str]:
    """Stand-in for a real observer: something OTHER than the agent reporting what changed.

    In production this is a git diff, an audit log, an eBPF probe -- anything the agent cannot
    author. If you wire the agent's own claim in here, the boundary becomes decoration.
    """
    return paths


def main() -> int:
    policy = load(POLICY_DOC)
    store = EvidenceStore.for_policy(policy, ":memory:")
    adapter = JevAdapter()
    print(f"policy {policy.fingerprint[:12]}  trusted: {sorted(policy.trusted_sources)}\n")

    # 1 -- a permitted edit, with an advisory model that has no objection.
    request = ActionRequest("edit_doc", ("docs/pricing.md",), ("read", "write"))
    advice = adapter.advise(
        JevResponse("jev-1.13", "is this edit safe?", "noul", 0.97),
        min_confidence=0.8,
    )
    decision = decide(policy, request, advice)
    store.record_decision(decision)
    store.record_advice(decision.request.correlation_id, advice)
    print("1. permitted edit")
    print(f"   ceiling={decision.ceiling} final={decision.verdict} executable={decision.executable}")

    # ... the agent runs, and something OTHER than the agent reports what changed.
    touched = observe_filesystem(["docs/pricing.md"])
    store.record_effect(decision.request.correlation_id, resources=touched, observer="fs-observer")
    observed, why = store.authorised_effect(decision.request.correlation_id)
    print(f"   effect: {observed} ({why})")
    print(f"   {verify_effect(decision, observed or [], policy).reason}\n")

    # 2 -- the same agent reaches outside its scope. No model is consulted; it does not matter.
    stray = decide(policy, ActionRequest("edit_doc", ("docs/ok.md", "src/billing.ts")))
    print("2. out of scope")
    print(f"   final={stray.verdict}  <- deterministic, no model involved")
    print(f"   {[f.reason for f in stray.findings if f.guard == 'resource_scope'][0]}\n")

    # 3 -- an irreversible publish with a maximally confident model.
    certain = adapter.advise(
        JevResponse("jev-1.13", "is this safe to publish?", "noul", 1.0),
        min_confidence=0.8,
    )
    publish = decide(policy, ActionRequest("publish", ("docs/pricing.md",), ("http",)), certain)
    print("3. irreversible publish, model confidence 1.0")
    print(f"   ceiling={publish.ceiling} final={publish.verdict} executable={publish.executable}")
    print("   confidence did not create authority; a human still has to approve\n")

    # 4 -- the agent tries to confirm its own outcome. Refused.
    print("4. labels")
    try:
        store.record_outcome(decision.request.correlation_id, ground_truth=True,
                             confirmed_by="the-agent")
    except Exception as exc:  # EvidenceError
        print(f"   agent self-confirmation refused: {str(exc)[:72]}...")
    confirm_outcome(store, decision.request.correlation_id, ground_truth=True,
                    confirmed_by="david")
    print(f"   labels now: {len(store.labelled())}")

    result = readiness(store, "is this edit safe?", target=Target(max_error_rate=0.05))
    print(f"   readiness: {result.state}")
    print(f"   {result.reason}\n")

    ok, why = store.verify_chain()
    print(f"evidence chain: {'intact' if ok else 'BROKEN'} ({why})")
    print("chain for the first decision:",
          [r.kind for r in store.chain(decision.request.correlation_id)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
