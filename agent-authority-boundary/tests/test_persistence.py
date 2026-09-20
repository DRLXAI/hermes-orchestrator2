#!/usr/bin/env python3
"""Evidence chain, trust model, review workflow and target-dependent readiness.

The adversarial cases here attack the newest and softest claim in the product: that writing
something into storage does not make it true.
"""

from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authority.adapters.base import advice_from
from authority.calibration import (
    ReadinessState, Target, export_dataset, readiness, wilson_interval,
)
from authority.engine import ActionRequest, decide
from authority.policy import load
from authority.review import approve, confirm_outcome, pending_approvals, pending_outcomes
from authority.store import EvidenceError, EvidenceStore
from authority.verdict import Verdict

DOC = {
    "actions": {
        "write_doc": {"ceiling": "ALLOW", "scope": ["docs/**"], "reversible": True},
        "deploy": {"ceiling": "ALLOW", "scope": ["infra/**"], "reversible": False,
                   "requires_approval": True},
    },
    "pinned_models": {"jev": "jev-1.13"},
    "trusted_sources": ["policy", "david", "ci-observer"],
}


def fresh():
    policy = load(DOC)
    return policy, EvidenceStore.for_policy(policy)


def decided(policy, store, action="write_doc", resources=("docs/a.md",)):
    d = decide(policy, ActionRequest(action, resources))
    store.record_decision(d)
    return d


class TrustIsNotConferredByStorage(unittest.TestCase):
    def test_model_advice_is_always_untrusted_whatever_it_is_called(self):
        policy, store = fresh()
        d = decided(policy, store)
        for adapter in ("jev", "policy", "david", "ci-observer"):
            store.record_advice(d.request.correlation_id,
                                advice_from(adapter, verdict=Verdict.ALLOW, model="jev-1.13"))
        advice = [r for r in store.chain(d.request.correlation_id) if r.kind == "advice"]
        self.assertEqual(len(advice), 4)
        self.assertTrue(all(not r.trusted for r in advice),
                        "an adapter named after a trusted source must not become trusted")

    def test_the_agents_own_account_of_what_it_touched_is_not_evidence(self):
        policy, store = fresh()
        d = decided(policy, store)
        store.record_effect(d.request.correlation_id,
                            resources=["docs/a.md"], observer="the-agent")
        resources, reason = store.authorised_effect(d.request.correlation_id)
        self.assertIsNone(resources, "an untrusted observer must yield UNKNOWN, not a result")
        self.assertIn("untrusted", reason)

    def test_a_trusted_observer_overrides_nothing_but_is_the_one_consulted(self):
        policy, store = fresh()
        d = decided(policy, store)
        store.record_effect(d.request.correlation_id,
                            resources=["docs/a.md", "src/exfil.ts"], observer="the-agent")
        store.record_effect(d.request.correlation_id, resources=["docs/a.md"],
                            observer="ci-observer")
        resources, _ = store.authorised_effect(d.request.correlation_id)
        self.assertEqual(resources, ["docs/a.md"])

    def test_no_effect_observation_at_all_is_unknown_not_clean(self):
        policy, store = fresh()
        d = decided(policy, store)
        resources, reason = store.authorised_effect(d.request.correlation_id)
        self.assertIsNone(resources)
        self.assertIn("no effect", reason)

    def test_an_untrusted_confirmer_cannot_create_a_label(self):
        policy, store = fresh()
        d = decided(policy, store)
        with self.assertRaises(EvidenceError):
            store.record_outcome(d.request.correlation_id, ground_truth=True,
                                 confirmed_by="the-agent")
        self.assertEqual(len(store.labelled()), 0)

    def test_an_untrusted_approver_cannot_satisfy_require_approval(self):
        policy, store = fresh()
        d = decided(policy, store, "deploy", ("infra/a.tf",))
        self.assertIs(d.verdict, Verdict.REQUIRE_APPROVAL)
        with self.assertRaises(EvidenceError):
            store.record_approval(d.request.correlation_id, approver="the-agent", granted=True)
        self.assertFalse(store.approved(d.request.correlation_id))

    def test_a_rejected_self_confirmation_is_kept_as_untrusted_evidence(self):
        """The attempt leaves a trace. It just never becomes a label."""
        policy, store = fresh()
        d = decided(policy, store)
        with self.assertRaises(EvidenceError):
            store.record_outcome(d.request.correlation_id, ground_truth=True,
                                 confirmed_by="the-agent")
        rows = [r for r in store.chain(d.request.correlation_id) if r.kind == "outcome"]
        self.assertEqual(len(rows), 1, "the attempt must be recorded, not discarded")
        self.assertFalse(rows[0].trusted)
        self.assertEqual(len(store.labelled()), 0)
        self.assertTrue(store.verify_chain()[0], "a refused write must not break the chain")

    def test_adding_a_trusted_source_changes_the_policy_fingerprint(self):
        """Trust config is policy, so widening it invalidates decisions made under the old one."""
        first = load(DOC)
        widened = load({**DOC, "trusted_sources": [*DOC["trusted_sources"], "the-agent"]})
        self.assertNotEqual(first.fingerprint, widened.fingerprint)


class ChainIsTamperEvident(unittest.TestCase):
    def test_a_clean_chain_verifies(self):
        policy, store = fresh()
        for _ in range(5):
            decided(policy, store)
        ok, reason = store.verify_chain()
        self.assertTrue(ok, reason)

    def test_editing_a_row_breaks_verification(self):
        policy, store = fresh()
        d = decided(policy, store)
        store._conn.execute(
            "UPDATE evidence SET payload_json = ? WHERE kind = 'decision'",
            ('{"action":"write_doc","final":"ALLOW","ceiling":"ALLOW"}',),
        )
        store._conn.commit()
        ok, reason = store.verify_chain()
        self.assertFalse(ok, "an edited payload must not verify")
        self.assertIn("altered", reason)

    def test_deleting_a_row_breaks_verification(self):
        policy, store = fresh()
        for _ in range(3):
            decided(policy, store)
        store._conn.execute("DELETE FROM evidence WHERE id = 2")
        store._conn.commit()
        ok, _ = store.verify_chain()
        self.assertFalse(ok, "a deleted row must not verify")

    def test_the_schema_refuses_an_invented_trust_level(self):
        policy, store = fresh()
        with self.assertRaises(sqlite3.IntegrityError):
            store._conn.execute(
                "INSERT INTO evidence (correlation_id, kind, source, trust, payload_json, "
                "recorded_at, prev_hash, row_hash) VALUES ('c','outcome','x','VERY_TRUSTED',"
                "'{}','t','p','h')"
            )


class ReviewKeepsAgreementApartFromTruth(unittest.TestCase):
    def test_approving_does_not_produce_a_label(self):
        policy, store = fresh()
        d = decided(policy, store, "deploy", ("infra/a.tf",))
        store.record_advice(d.request.correlation_id,
                            advice_from("jev", verdict=Verdict.ALLOW, model="jev-1.13",
                                        question="safe?", confidence=0.9))
        approve(store, d.request.correlation_id, approver="david")
        self.assertEqual(len(pending_approvals(store)), 0)
        self.assertEqual(len(pending_outcomes(store)), 1, "approval must not clear the label queue")
        self.assertEqual(len(store.labelled()), 0)

    def test_confirming_an_outcome_does_not_approve_anything(self):
        policy, store = fresh()
        d = decided(policy, store, "deploy", ("infra/a.tf",))
        store.record_advice(d.request.correlation_id,
                            advice_from("jev", verdict=Verdict.ALLOW, model="jev-1.13",
                                        question="safe?", confidence=0.9))
        confirm_outcome(store, d.request.correlation_id, ground_truth=True, confirmed_by="david")
        self.assertEqual(len(store.labelled()), 1)
        self.assertFalse(store.approved(d.request.correlation_id))
        self.assertEqual(len(pending_approvals(store)), 1)


class TargetDependentReadiness(unittest.TestCase):
    def _load(self, store, n, correct, question="q"):
        for i in range(n):
            cid = f"{question}-{correct}-{i}"
            store.record_advice(cid, advice_from("jev", verdict=Verdict.ALLOW, model="jev-1.13",
                                                 question=question, confidence=0.9))
            store.record_outcome(cid, ground_truth=correct, confirmed_by="david",
                                 question=question, confidence=0.9)

    def test_no_target_is_neither_calibrated_nor_uncalibrated(self):
        _, store = fresh()
        self._load(store, 500, True)
        result = readiness(store, "q")
        self.assertIs(result.state, ReadinessState.TARGET_REQUIRED)
        self.assertFalse(result.can_conclude)
        self.assertIn("neither calibrated nor uncalibrated", result.reason)

    def test_sufficiency_scales_with_the_target_not_a_fixed_count(self):
        """The same evidence is enough for a loose target and not for a strict one."""
        _, store = fresh()
        self._load(store, 40, True)
        loose = readiness(store, "q", target=Target(max_error_rate=0.20))
        strict = readiness(store, "q", target=Target(max_error_rate=0.01))
        self.assertIs(loose.state, ReadinessState.MEETS_TARGET)
        self.assertIs(strict.state, ReadinessState.INSUFFICIENT)

    def test_a_measured_failure_is_reported_as_failure_not_as_missing_evidence(self):
        _, store = fresh()
        self._load(store, 100, True)
        self._load(store, 100, False)
        result = readiness(store, "q", target=Target(max_error_rate=0.05))
        self.assertIs(result.state, ReadinessState.FAILS_TARGET)
        self.assertTrue(result.can_conclude)
        self.assertIn("measured failure", result.reason)

    def test_zero_labels_is_insufficient_and_error_rate_is_none_not_zero(self):
        _, store = fresh()
        result = readiness(store, "q", target=Target(max_error_rate=0.05))
        self.assertIs(result.state, ReadinessState.INSUFFICIENT)
        self.assertIsNone(result.error_rate)

    def test_wilson_interval_contains_the_point_estimate(self):
        for successes, n in ((0, 10), (5, 10), (10, 10), (1, 1000)):
            lower, upper = wilson_interval(successes, n, 0.95)
            self.assertLessEqual(lower, successes / n)
            self.assertGreaterEqual(upper, successes / n)

    def test_unlabelled_observations_never_enter_the_export(self):
        _, store = fresh()
        for i in range(30):
            store.record_advice(f"u{i}", advice_from("jev", verdict=Verdict.ALLOW,
                                                     model="jev-1.13", question="q",
                                                     confidence=0.9))
        self.assertEqual(len(export_dataset(store, "q")), 0)
        self.assertEqual(readiness(store, "q", target=Target(0.05)).labelled_count, 0)

    def test_export_carries_only_trusted_labels(self):
        _, store = fresh()
        self._load(store, 5, True)
        rows = export_dataset(store, "q")
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(r["confirmed_by"] == "david" for r in rows))
        self.assertTrue(all("operator_agreed" not in r for r in rows))


class ChainReconstruction(unittest.TestCase):
    def test_a_decision_reconstructs_end_to_end(self):
        policy, store = fresh()
        d = decided(policy, store, "deploy", ("infra/a.tf",))
        cid = d.request.correlation_id
        store.record_advice(cid, advice_from("jev", verdict=Verdict.ALLOW, model="jev-1.13",
                                             question="safe?", confidence=0.91))
        approve(store, cid, approver="david")
        store.record_effect(cid, resources=["infra/a.tf"], observer="ci-observer")
        confirm_outcome(store, cid, ground_truth=True, confirmed_by="david")

        kinds = [r.kind for r in store.chain(cid)]
        self.assertEqual(kinds, ["decision", "advice", "approval", "effect", "outcome"])
        decision_row = store.chain(cid)[0]
        self.assertEqual(decision_row.payload["policy_fingerprint"], policy.fingerprint)
        self.assertTrue(store.verify_chain()[0])


if __name__ == "__main__":
    unittest.main(verbosity=1)
