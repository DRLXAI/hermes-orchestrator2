#!/usr/bin/env python3
"""Executes the README quickstart verbatim, so the documentation cannot drift from the code."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authority.adapters.jev import JevAdapter, JevResponse
from authority.engine import ActionRequest, decide, verify_effect
from authority.evidence import EvidenceLog, Observation
from authority.policy import load
from authority.verdict import Verdict


class Quickstart(unittest.TestCase):
    def setUp(self):
        self.policy = load({
            "actions": {
                "write_doc": {"ceiling": "ALLOW", "scope": ["docs/**"], "reversible": True},
                "deploy": {"ceiling": "ALLOW", "scope": ["infra/**"], "reversible": False,
                           "external_side_effects": True, "requires_approval": True},
            },
            "pinned_models": {"jev": "jev-1.13"},
        })

    def test_the_documented_flow_works_end_to_end(self):
        decision = decide(self.policy, ActionRequest("write_doc", ("docs/pricing.md",)))
        self.assertTrue(decision.executable)

        advice = JevAdapter().advise(
            JevResponse("jev-1.13", "is this edit safe?", True, {"true": 0.97, "false": 0.03}),
            min_confidence=0.8,
        )
        decision = decide(self.policy, ActionRequest("write_doc", ("docs/pricing.md",)), advice)
        self.assertTrue(decision.executable)

        finding = verify_effect(decision, ["docs/pricing.md"], self.policy)
        self.assertIs(finding.verdict, Verdict.ALLOW)

    def test_the_deploy_example_requires_a_human(self):
        decision = decide(self.policy, ActionRequest("deploy", ("infra/main.tf",)))
        self.assertFalse(decision.executable)
        self.assertIs(decision.verdict, Verdict.REQUIRE_APPROVAL)

    def test_shadow_mode_to_calibration_export(self):
        log = EvidenceLog()
        decision = decide(self.policy, ActionRequest("write_doc", ("docs/a.md",)))
        log.record(Observation(decision.request.correlation_id, decision.request.action,
                               decision.ceiling, decision.verdict, adapter="jev",
                               model="jev-1.13", question="is this edit safe?", confidence=0.94))
        self.assertEqual(len(log.export_for_calibration()), 0)   # nothing labelled yet

        log.confirm_outcome(decision.request.correlation_id, ground_truth=True,
                            confirmed_by="david")
        self.assertEqual(len(log.export_for_calibration()), 1)
        self.assertFalse(log.readiness("is this edit safe?").can_conclude)


if __name__ == "__main__":
    unittest.main(verbosity=1)
