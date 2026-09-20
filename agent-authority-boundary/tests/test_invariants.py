#!/usr/bin/env python3
"""Dedicated coverage for the two invariants the gap assessment flagged as thin.

    OBSERVATION != LABEL
    DECLARED INTENT != OBSERVED EFFECT

Each was previously defended by one or two assertions, which meant a mutation could invert the
boundary and barely register. These attack each from several independent directions, so an
inversion fails loudly. Also covers the multiple-comparison and drift surfaces.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authority.adapters.base import advice_from
from authority.calibration import (
    ReadinessState, Target, check_drift, export_dataset, portfolio_readiness, readiness,
)
from authority.engine import ActionRequest, decide
from authority.observers.git import GitObservation, assess_git_effect
from authority.policy import load
from authority.store import EvidenceError, EvidenceStore
from authority.verdict import Verdict

DOC = {
    "actions": {"w": {"ceiling": "ALLOW", "scope": ["docs/**"], "reversible": True}},
    "pinned_models": {"jev": "jev-1.13"},
    "trusted_sources": ["policy", "david", "git"],
}


def fresh():
    policy = load(DOC)
    return policy, EvidenceStore.for_policy(policy)


class ObservationIsNotALabel(unittest.TestCase):
    """OBSERVATION != LABEL, attacked from six directions."""

    def test_advice_alone_produces_no_labels(self):
        _, store = fresh()
        for i in range(40):
            store.record_advice(f"c{i}", advice_from("jev", verdict=Verdict.ALLOW,
                                                     model="jev-1.13", question="q",
                                                     confidence=0.9))
        self.assertEqual(len(store.labelled()), 0)
        self.assertEqual(len(store.labelled("q")), 0)

    def test_advice_alone_leaves_readiness_at_zero_labels(self):
        _, store = fresh()
        for i in range(40):
            store.record_advice(f"c{i}", advice_from("jev", verdict=Verdict.ALLOW,
                                                     model="jev-1.13", question="q",
                                                     confidence=0.99))
        result = readiness(store, "q", target=Target(0.05))
        self.assertEqual(result.labelled_count, 0)
        self.assertGreater(result.observed_count, 0, "they ARE observations")
        self.assertIs(result.state, ReadinessState.INSUFFICIENT)

    def test_advice_alone_exports_nothing(self):
        _, store = fresh()
        for i in range(40):
            store.record_advice(f"c{i}", advice_from("jev", verdict=Verdict.ALLOW,
                                                     model="jev-1.13", question="q",
                                                     confidence=0.9))
        self.assertEqual(export_dataset(store, "q"), [])

    def test_a_high_confidence_observation_is_still_not_a_label(self):
        _, store = fresh()
        store.record_advice("c1", advice_from("jev", verdict=Verdict.ALLOW, model="jev-1.13",
                                              question="q", confidence=1.0))
        self.assertEqual(len(store.labelled()), 0)

    def test_an_untrusted_outcome_row_exists_but_is_not_a_label(self):
        _, store = fresh()
        store.record_advice("c1", advice_from("jev", verdict=Verdict.ALLOW, model="jev-1.13",
                                              question="q", confidence=0.9))
        with self.assertRaises(EvidenceError):
            store.record_outcome("c1", ground_truth=True, confirmed_by="the-agent", question="q")
        outcomes = [r for r in store.all_rows() if r.kind == "outcome"]
        self.assertEqual(len(outcomes), 1, "the attempt is recorded")
        self.assertEqual(len(store.labelled()), 0, "...but it is not a label")
        self.assertEqual(export_dataset(store, "q"), [])

    def test_only_a_trusted_confirmation_turns_an_observation_into_a_label(self):
        _, store = fresh()
        store.record_advice("c1", advice_from("jev", verdict=Verdict.ALLOW, model="jev-1.13",
                                              question="q", confidence=0.9))
        self.assertEqual(len(store.labelled()), 0)
        store.record_outcome("c1", ground_truth=True, confirmed_by="david", question="q",
                             confidence=0.9)
        self.assertEqual(len(store.labelled()), 1)
        self.assertEqual(len(export_dataset(store, "q")), 1)

    def test_drift_sees_no_evidence_from_observations_alone(self):
        policy, store = fresh()
        for i in range(40):
            store.record_advice(f"c{i}", advice_from("jev", verdict=Verdict.ALLOW,
                                                     model="jev-1.13", question="q",
                                                     confidence=0.9))
        report = check_drift(store, policy, "q", target=Target(0.05))
        self.assertIsNone(report.earlier, "unlabelled rows must not feed a drift comparison")


class DeclaredIntentIsNotObservedEffect(unittest.TestCase):
    """DECLARED INTENT != OBSERVED EFFECT, attacked from six directions."""

    def test_an_agent_reported_effect_is_not_admissible(self):
        _, store = fresh()
        store.record_effect("c1", resources=["docs/a.md"], observer="the-agent")
        resources, reason = store.authorised_effect("c1")
        self.assertIsNone(resources)
        self.assertIn("untrusted", reason)

    def test_many_agent_reports_do_not_add_up_to_one_trusted_one(self):
        _, store = fresh()
        for i in range(20):
            store.record_effect("c1", resources=["docs/a.md"], observer=f"agent-{i}")
        self.assertIsNone(store.authorised_effect("c1")[0])

    def test_an_agent_cannot_impersonate_the_observer_name_without_being_trusted(self):
        policy = load({**DOC, "trusted_sources": ["policy", "david"]})   # 'git' NOT trusted here
        store = EvidenceStore.for_policy(policy)
        store.record_effect("c1", resources=["docs/a.md"], observer="git")
        self.assertIsNone(store.authorised_effect("c1")[0])

    def test_no_observation_at_all_is_unknown_not_compliant(self):
        _, store = fresh()
        resources, reason = store.authorised_effect("never-observed")
        self.assertIsNone(resources)
        self.assertIn("no effect", reason)

    def test_a_trusted_observation_is_what_gets_consulted(self):
        _, store = fresh()
        store.record_effect("c1", resources=["docs/a.md", "src/x.ts"], observer="the-agent")
        store.record_effect("c1", resources=["docs/a.md"], observer="git")
        self.assertEqual(store.authorised_effect("c1")[0], ["docs/a.md"])

    def test_an_unavailable_git_observation_never_reports_compliance(self):
        for reason in ("git missing", "baseline unreachable", "identity mismatch"):
            finding = assess_git_effect(GitObservation(available=False, reason=reason),
                                        ["docs/**"])
            self.assertIsNot(finding.verdict, Verdict.ALLOW)
            self.assertIn("unverifiable", finding.reason)

    def test_a_clean_observation_cannot_raise_a_blocked_decision(self):
        from authority.verdict import narrowest
        policy, _ = fresh()
        blocked = decide(policy, ActionRequest("w", ("src/x.ts",)))
        clean = assess_git_effect(
            GitObservation(available=True, reason="ok", added=("docs/a.md",)), ["docs/**"]
        )
        self.assertIs(clean.verdict, Verdict.ALLOW)
        self.assertIs(narrowest(blocked.verdict, clean.verdict), Verdict.STOP)


class MultipleComparisons(unittest.TestCase):
    def _load(self, store, question, n, correct=True):
        for i in range(n):
            cid = f"{question}-{i}-{correct}"
            store.record_advice(cid, advice_from("jev", verdict=Verdict.ALLOW, model="jev-1.13",
                                                 question=question, confidence=0.9))
            store.record_outcome(cid, ground_truth=correct, confirmed_by="david",
                                 question=question, confidence=0.9)

    def test_no_aggregate_claim_without_an_explicit_correction(self):
        _, store = fresh()
        for q in ("q1", "q2", "q3"):
            self._load(store, q, 300)
        result = portfolio_readiness(store, ["q1", "q2", "q3"], target=Target(0.05))
        self.assertFalse(result.aggregate_claim_available)
        self.assertIsNone(result.all_meet_target, "unanswerable, not False")
        self.assertIn("spurious", result.reason)

    def test_bonferroni_makes_the_claim_available_and_tightens_each_test(self):
        _, store = fresh()
        for q in ("q1", "q2", "q3"):
            self._load(store, q, 300)
        result = portfolio_readiness(store, ["q1", "q2", "q3"], target=Target(0.05),
                                     correction="bonferroni")
        self.assertTrue(result.aggregate_claim_available)
        self.assertTrue(result.all_meet_target)
        self.assertGreater(result.per_test_confidence, 0.95, "each test must get stricter")

    def test_the_correction_can_withdraw_a_claim_that_survived_uncorrected(self):
        """The whole point: correcting must be able to change the answer."""
        # 100 perfect labels per question: upper bound 0.037 at 95% (meets a 0.05 target), but
        # 0.084 once Bonferroni tightens each of 20 tests to 99.75% (no longer meets it).
        _, store = fresh()
        for q in [f"q{i}" for i in range(20)]:
            self._load(store, q, 100)
        uncorrected = portfolio_readiness(store, [f"q{i}" for i in range(20)], target=Target(0.05))
        corrected = portfolio_readiness(store, [f"q{i}" for i in range(20)], target=Target(0.05),
                                        correction="bonferroni")
        met_uncorrected = sum(1 for r in uncorrected.results
                              if r.state is ReadinessState.MEETS_TARGET)
        met_corrected = sum(1 for r in corrected.results
                            if r.state is ReadinessState.MEETS_TARGET)
        self.assertEqual(met_uncorrected, 20, "all 20 should pass uncorrected at this n")
        self.assertEqual(met_corrected, 0, "and none should survive the family-wise correction")
        self.assertGreater(met_uncorrected, met_corrected,
                           "a correction that never changes anything is decoration")

    def test_an_unknown_correction_is_refused(self):
        _, store = fresh()
        with self.assertRaises(ValueError):
            portfolio_readiness(store, ["q"], target=Target(0.05), correction="holm-ish")


class Drift(unittest.TestCase):
    def test_a_model_that_is_not_the_pin_is_reported_as_drift(self):
        policy, store = fresh()
        for model in ("jev-1.13", "jev-1.14"):
            store.record_advice(f"c-{model}", advice_from("jev", verdict=Verdict.ALLOW,
                                                          model=model, question="q",
                                                          confidence=0.9))
        report = check_drift(store, policy, "q", target=Target(0.05))
        self.assertTrue(report.model_drifted)
        self.assertFalse(report.ok)
        self.assertIn("jev-1.14", report.reason)

    def test_a_moved_error_rate_is_detected(self):
        policy, store = fresh()
        for i in range(60):
            cid = f"early{i}"
            store.record_advice(cid, advice_from("jev", verdict=Verdict.ALLOW, model="jev-1.13",
                                                 question="q", confidence=0.9))
            store.record_outcome(cid, ground_truth=True, confirmed_by="david", question="q",
                                 confidence=0.9)
        for i in range(60):
            cid = f"late{i}"
            store.record_advice(cid, advice_from("jev", verdict=Verdict.ALLOW, model="jev-1.13",
                                                 question="q", confidence=0.9))
            store.record_outcome(cid, ground_truth=False, confirmed_by="david", question="q",
                                 confidence=0.9)
        report = check_drift(store, policy, "q", target=Target(0.05))
        self.assertTrue(report.error_rate_moved)
        self.assertFalse(report.ok)

    def test_a_steady_system_reports_no_drift(self):
        policy, store = fresh()
        for i in range(60):
            cid = f"c{i}"
            store.record_advice(cid, advice_from("jev", verdict=Verdict.ALLOW, model="jev-1.13",
                                                 question="q", confidence=0.9))
            store.record_outcome(cid, ground_truth=True, confirmed_by="david", question="q",
                                 confidence=0.9)
        report = check_drift(store, policy, "q", target=Target(0.05))
        self.assertTrue(report.ok)


if __name__ == "__main__":
    unittest.main(verbosity=1)
