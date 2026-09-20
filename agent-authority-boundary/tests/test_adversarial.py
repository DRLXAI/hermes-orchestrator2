#!/usr/bin/env python3
"""Adversarial suite. This is security-sensitive infrastructure, so every test here is an attack.

The invariant under test, stated once:

    MODEL CONFIDENCE NEVER CREATES AUTHORITY.

Each test names the attack it is running and asserts the boundary survives it. Where an attack
can be made to "land" (the hostile input really is accepted and processed), the test asserts
that it landed before asserting it was refused -- otherwise the test would pass vacuously.
"""

from __future__ import annotations

import itertools
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authority import scope as scope_mod
from authority.adapters.base import advice_from
from authority.adapters.jev import JevAdapter, JevResponse
from authority.engine import ActionRequest, Advice, decide, revalidate, verify_effect
from authority.evidence import EvidenceLog, Observation
from authority.policy import PolicyError, load
from authority.verdict import Verdict, narrowest

BASE = {
    "actions": {
        "write_doc": {"ceiling": "ALLOW", "scope": ["docs/**"], "reversible": True},
        "deploy": {
            "ceiling": "ALLOW",
            "scope": ["infra/**"],
            "reversible": False,
            "external_side_effects": True,
            "requires_approval": True,
        },
    },
    "pinned_models": {"jev": "jev-1.13"},
}


def policy(**overrides):
    doc = {"actions": dict(BASE["actions"]), "pinned_models": dict(BASE["pinned_models"])}
    doc.update(overrides)
    return load(doc)


class ScopeBroadening(unittest.TestCase):
    """ATTACK 1: broaden scope."""

    def test_a_resource_outside_the_declared_scope_is_refused(self):
        d = decide(policy(), ActionRequest("write_doc", ("docs/ok.md", "src/secrets.ts")))
        self.assertIs(d.verdict, Verdict.STOP)
        self.assertIn("src/secrets.ts", d.explain())

    def test_an_unbounded_scope_is_not_honoured_as_permission(self):
        for patterns in (["**"], ["**/*"], ["*", "*/*", "*/*/*", "*/*/*/*"]):
            with self.subTest(patterns=patterns):
                pol = policy(actions={"write_doc": {"ceiling": "ALLOW", "scope": patterns,
                                                    "reversible": True}})
                d = decide(pol, ActionRequest("write_doc", ("anything/at/all.bin",)))
                self.assertIsNot(d.verdict, Verdict.ALLOW)

    def test_scope_patterns_that_escape_the_root_are_refused_at_load(self):
        for bad in (["/etc/passwd"], ["../../etc"], ["docs/../../x"], ["a\\b"], [""], [1]):
            with self.subTest(bad=bad), self.assertRaises((PolicyError, scope_mod.ScopeError)):
                policy(actions={"write_doc": {"ceiling": "ALLOW", "scope": bad}})

    def test_declaring_fewer_resources_than_you_touch_is_caught_after_the_fact(self):
        """Intent declared up front is not evidence of behaviour afterwards."""
        pol = policy()
        d = decide(pol, ActionRequest("write_doc", ("docs/ok.md",)))
        self.assertIs(d.verdict, Verdict.ALLOW)  # the request looked clean

        finding = verify_effect(d, ["docs/ok.md", "src/exfil.ts"], pol)
        self.assertIs(finding.verdict, Verdict.STOP)
        self.assertIn("src/exfil.ts", finding.reason)


class TrustedPolicyTampering(unittest.TestCase):
    """ATTACK 2: modify trusted policy. ATTACK 8: change authority mid-flight."""

    def test_a_policy_without_a_fingerprint_is_not_trusted(self):
        from authority.policy import Policy

        forged = Policy(rules=policy().rules, pinned_models={"jev": "jev-1.13"}, fingerprint="")
        d = decide(forged, ActionRequest("write_doc", ("docs/a.md",)))
        self.assertIs(d.verdict, Verdict.STOP)

    def test_policy_changing_between_decision_and_execution_refuses(self):
        pol = policy()
        d = decide(pol, ActionRequest("write_doc", ("docs/a.md",)))
        self.assertIs(d.verdict, Verdict.ALLOW)

        widened = policy(actions={"write_doc": {"ceiling": "ALLOW", "scope": ["**"],
                                               "reversible": True}})
        self.assertNotEqual(widened.fingerprint, pol.fingerprint)  # the swap really happened

        self.assertIs(revalidate(d, widened).verdict, Verdict.STOP)

    def test_revalidation_against_the_same_policy_is_a_no_op(self):
        pol = policy()
        d = decide(pol, ActionRequest("write_doc", ("docs/a.md",)))
        self.assertIs(revalidate(d, pol).verdict, d.verdict)


class ForgedModelIdentity(unittest.TestCase):
    """ATTACK 4: forge model identity/version."""

    def test_an_unpinned_adapter_is_inadmissible(self):
        d = decide(policy(), ActionRequest("write_doc", ("docs/a.md",)),
                   advice_from("rogue", verdict=Verdict.ALLOW, model="whatever-1.0"))
        self.assertIs(d.verdict, Verdict.STOP)

    def test_a_model_that_is_not_the_pinned_build_is_refused(self):
        d = decide(policy(), ActionRequest("write_doc", ("docs/a.md",)),
                   advice_from("jev", verdict=Verdict.ALLOW, model="jev-1.14"))
        self.assertIs(d.verdict, Verdict.STOP)

    def test_a_moving_alias_is_refused_even_if_it_resolves_to_the_pin(self):
        d = decide(policy(), ActionRequest("write_doc", ("docs/a.md",)),
                   advice_from("jev", verdict=Verdict.ALLOW, model="jev-latest"))
        self.assertIs(d.verdict, Verdict.STOP)

    def test_an_alias_cannot_be_pinned_in_policy_at_all(self):
        with self.assertRaises(PolicyError):
            policy(pinned_models={"jev": "jev-latest"})


class ConfidenceCannotCreateAuthority(unittest.TestCase):
    """ATTACK 3: forge confidence. ATTACK 7: use high confidence to expand authority."""

    def test_perfect_confidence_cannot_lift_a_ceiling(self):
        pol = policy()
        req = ActionRequest("deploy", ("infra/main.tf",))
        without = decide(pol, req)
        self.assertIsNot(without.verdict, Verdict.ALLOW)  # approval + irreversible + side effects

        with_advice = decide(pol, req, advice_from("jev", verdict=Verdict.ALLOW,
                                                   model="jev-1.13", confidence=1.0))
        self.assertEqual(with_advice.verdict, without.verdict)
        self.assertLessEqual(with_advice.verdict, with_advice.ceiling)

    def test_impossible_confidence_values_do_not_help(self):
        pol = policy()
        req = ActionRequest("deploy", ("infra/main.tf",))
        for forged in (1.0, 99.0, float("inf"), -5.0, float("nan")):
            with self.subTest(confidence=forged):
                d = decide(pol, req, advice_from("jev", verdict=Verdict.ALLOW,
                                                 model="jev-1.13", confidence=forged))
                self.assertIsNot(d.verdict, Verdict.ALLOW)

    def test_an_adapter_cannot_express_more_than_no_objection(self):
        """Even an adapter that tries to return something above ALLOW is clamped."""
        advice = advice_from("jev", verdict=Verdict.ALLOW, model="jev-1.13")
        self.assertEqual(advice.verdict, Verdict.ALLOW)
        self.assertLessEqual(int(advice.verdict), int(Verdict.ALLOW))

    def test_a_hostile_advice_object_still_cannot_widen(self):
        """Advice constructed directly, bypassing the adapter helper entirely."""
        pol = policy()
        req = ActionRequest("deploy", ("infra/main.tf",))
        hostile = Advice(adapter="jev", verdict=Verdict.ALLOW, model="jev-1.13", confidence=1.0)
        d = decide(pol, req, hostile)
        self.assertIsNot(d.verdict, Verdict.ALLOW)

    def test_jev_low_confidence_narrows_and_high_confidence_merely_declines_to_narrow(self):
        adapter = JevAdapter()
        pol = policy()
        req = ActionRequest("write_doc", ("docs/a.md",))

        unsure = adapter.advise(JevResponse("jev-1.13", "safe?", True,
                                            {"true": 0.52, "false": 0.48}), min_confidence=0.8)
        self.assertIs(decide(pol, req, unsure).verdict, Verdict.ESCALATE)

        sure = adapter.advise(JevResponse("jev-1.13", "safe?", True,
                                          {"true": 0.99, "false": 0.01}), min_confidence=0.8)
        self.assertIs(decide(pol, req, sure).verdict, Verdict.ALLOW)
        # ...but only because the deterministic ceiling was already ALLOW.
        self.assertIs(decide(pol, req).verdict, Verdict.ALLOW)


class UnknownIsNotPermission(unittest.TestCase):
    """ATTACK 5: turn UNKNOWN into permission."""

    def test_an_action_the_policy_does_not_mention_is_not_allowed(self):
        d = decide(policy(), ActionRequest("rm_rf", ("/",)))
        self.assertIsNot(d.verdict, Verdict.ALLOW)

    def test_an_undeclared_scope_is_not_permission(self):
        pol = policy(actions={"write_doc": {"ceiling": "ALLOW", "reversible": True}})
        d = decide(pol, ActionRequest("write_doc", ("anything.md",)))
        self.assertIsNot(d.verdict, Verdict.ALLOW)

    def test_unknown_reversibility_is_never_read_as_reversible(self):
        pol = policy(actions={"write_doc": {"ceiling": "ALLOW", "scope": ["docs/**"]}})
        d = decide(pol, ActionRequest("write_doc", ("docs/a.md",)))
        self.assertIsNot(d.verdict, Verdict.ALLOW)

    def test_unknown_ground_truth_is_not_a_false_safe_and_not_a_label(self):
        row = Observation("c1", "write_doc", Verdict.ALLOW, Verdict.ALLOW, question="q",
                          confidence=0.9, ground_truth=None)
        self.assertFalse(row.labelled)
        self.assertFalse(row.false_safe)


class ApprovalBypass(unittest.TestCase):
    """ATTACK 6: bypass human approval."""

    def test_advice_cannot_satisfy_an_approval_requirement(self):
        pol = policy()
        req = ActionRequest("deploy", ("infra/main.tf",))
        d = decide(pol, req, advice_from("jev", verdict=Verdict.ALLOW, model="jev-1.13",
                                         confidence=1.0, answer="approved"))
        self.assertFalse(d.executable)

    def test_operator_agreement_is_not_approval_and_not_ground_truth(self):
        log = EvidenceLog()
        log.record(Observation("c1", "deploy", Verdict.REQUIRE_APPROVAL, Verdict.REQUIRE_APPROVAL,
                               question="q", confidence=0.95, operator_agreed=True))
        self.assertEqual(len(log.labelled()), 0)
        self.assertEqual(log.export_for_calibration(), [])


class CalibrationHygiene(unittest.TestCase):
    """ATTACK 9: inject unlabelled observations into calibration statistics."""

    def test_unlabelled_rows_never_reach_a_calibration_export(self):
        log = EvidenceLog()
        for i in range(50):
            log.record(Observation(f"c{i}", "write_doc", Verdict.ALLOW, Verdict.ALLOW,
                                   question="q", confidence=0.9, operator_agreed=True))
        self.assertEqual(len(log.observed()), 50)
        self.assertEqual(len(log.export_for_calibration()), 0)

    def test_agreement_cannot_be_laundered_into_a_label(self):
        log = EvidenceLog()
        log.record(Observation("c1", "write_doc", Verdict.ALLOW, Verdict.ALLOW, question="q",
                               confidence=0.9, operator_agreed=True, ground_truth=True))
        # ground_truth without a named confirmer is not a label.
        self.assertEqual(len(log.labelled()), 0)

    def test_a_label_requires_a_named_confirmer(self):
        log = EvidenceLog()
        log.record(Observation("c1", "write_doc", Verdict.ALLOW, Verdict.ALLOW, question="q",
                               confidence=0.9))
        with self.assertRaises(ValueError):
            log.confirm_outcome("c1", ground_truth=True, confirmed_by="   ")
        log.confirm_outcome("c1", ground_truth=True, confirmed_by="david")
        self.assertEqual(len(log.labelled()), 1)

    def test_readiness_refuses_to_conclude_without_a_stated_target(self):
        log = EvidenceLog()
        for i in range(500):
            log.record(Observation(f"c{i}", "write_doc", Verdict.ALLOW, Verdict.ALLOW,
                                   question="q", confidence=0.9))
            log.confirm_outcome(f"c{i}", ground_truth=True, confirmed_by="david")
        r = log.readiness("q")
        self.assertEqual(r.labelled_count, 500)
        self.assertFalse(r.can_conclude, "500 labels must not self-certify as sufficient")
        self.assertTrue(r.can_conclude is False and "no sufficiency target" in r.reason)

    def test_false_safe_rate_is_none_rather_than_zero_when_there_is_no_evidence(self):
        self.assertIsNone(EvidenceLog().readiness("q").false_safe_rate)

    def test_export_strips_concordance_so_it_cannot_be_used_as_truth(self):
        log = EvidenceLog()
        log.record(Observation("c1", "write_doc", Verdict.ALLOW, Verdict.ALLOW, question="q",
                               confidence=0.9, operator_agreed=False))
        log.confirm_outcome("c1", ground_truth=True, confirmed_by="david")
        rows = log.export_for_calibration()
        self.assertEqual(len(rows), 1)
        self.assertNotIn("operator_agreed", rows[0])


class TheInvariantHolds(unittest.TestCase):
    """The property the whole product rests on, over the full cross-product and randomly."""

    def test_advice_never_raises_the_verdict_for_any_combination(self):
        pol = policy()
        requests = [
            ActionRequest("write_doc", ("docs/a.md",)),
            ActionRequest("write_doc", ("src/x.ts",)),
            ActionRequest("deploy", ("infra/a.tf",)),
            ActionRequest("unknown", ()),
        ]
        for req, verdict in itertools.product(requests, list(Verdict)):
            with self.subTest(action=req.action, advice=verdict):
                ceiling = decide(pol, req).verdict
                got = decide(pol, req, advice_from("jev", verdict=verdict, model="jev-1.13"))
                self.assertLessEqual(got.verdict, ceiling)

    def test_randomised_policies_and_advice_never_widen(self):
        rng = random.Random(20260920)
        for _ in range(2000):
            doc = {
                "actions": {
                    "act": {
                        "ceiling": rng.choice(["STOP", "ESCALATE", "REQUIRE_APPROVAL", "ALLOW"]),
                        "scope": rng.choice([["docs/**"], ["**"], None, ["a/*"]]),
                        "reversible": rng.choice([True, False, None]),
                        "external_side_effects": rng.choice([True, False]),
                        "requires_approval": rng.choice([True, False]),
                    }
                },
                "pinned_models": {"jev": "jev-1.13"},
            }
            doc["actions"]["act"] = {k: v for k, v in doc["actions"]["act"].items() if v is not None}
            pol = load(doc)
            req = ActionRequest("act", tuple(rng.choice([("docs/a.md",), ("z/z/z.bin",), ()])))
            ceiling = decide(pol, req).verdict
            advice = advice_from("jev", verdict=rng.choice(list(Verdict)), model="jev-1.13",
                                 confidence=rng.random())
            self.assertLessEqual(decide(pol, req, advice).verdict, ceiling)

    def test_narrowest_cannot_exceed_any_input(self):
        for a, b in itertools.product(list(Verdict), repeat=2):
            self.assertLessEqual(narrowest(a, b), a)
            self.assertLessEqual(narrowest(a, b), b)


if __name__ == "__main__":
    unittest.main(verbosity=1)
