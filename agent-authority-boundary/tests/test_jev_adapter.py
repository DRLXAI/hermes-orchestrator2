#!/usr/bin/env python3
"""The Jev adapter against Jev's actual response shape.

Jev's three primitives do not return the same fields: `noul` gives a bare probability of yes
with NO confidence, while `choice` and `score` carry a server-supplied confidence. Getting that
wrong is how a product ends up calibrating a number the model never produced.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authority.adapters.jev import JevAdapter, JevResponse, answer_of, confidence_of
from authority.engine import ActionRequest, decide
from authority.policy import load
from authority.verdict import Verdict

POLICY = load({
    "actions": {"w": {"ceiling": "ALLOW", "scope": ["docs/**"], "reversible": True}},
    "pinned_models": {"jev": "jev-1.13"},
    "trusted_sources": ["policy"],
})


class ResponseShapes(unittest.TestCase):
    def test_noul_confidence_is_derived_and_labelled_as_derived(self):
        conf, provenance = confidence_of(JevResponse("jev-1.13", "is_urgent", "noul", 0.96))
        self.assertAlmostEqual(conf, 0.92)
        self.assertEqual(provenance, "derived")

    def test_noul_is_symmetric_about_a_coin_flip(self):
        """0.96 yes and 0.04 yes are equally far from a coin flip; the answers differ."""
        high, _ = confidence_of(JevResponse("jev-1.13", "q", "noul", 0.96))
        low, _ = confidence_of(JevResponse("jev-1.13", "q", "noul", 0.04))
        self.assertAlmostEqual(high, low)
        self.assertEqual(answer_of(JevResponse("jev-1.13", "q", "noul", 0.96)), "true")
        self.assertEqual(answer_of(JevResponse("jev-1.13", "q", "noul", 0.04)), "false")

    def test_choice_uses_the_reported_confidence_not_the_top_probability(self):
        response = JevResponse("jev-1.13", "department", "choice", "billing",
                               confidence=0.55, probabilities={"billing": 0.97, "other": 0.03})
        conf, provenance = confidence_of(response)
        self.assertEqual(conf, 0.55, "must use the reported confidence, not max(probabilities)")
        self.assertEqual(provenance, "reported")

    def test_score_uses_the_reported_confidence(self):
        conf, provenance = confidence_of(
            JevResponse("jev-1.13", "frustration", "score", 1.3, confidence=0.55)
        )
        self.assertEqual((conf, provenance), (0.55, "reported"))

    def test_a_choice_with_no_reported_confidence_is_unavailable_not_invented(self):
        conf, provenance = confidence_of(
            JevResponse("jev-1.13", "department", "choice", "billing",
                        probabilities={"billing": 0.97})
        )
        self.assertIsNone(conf, "inventing a confidence from probabilities is a quiet fiction")
        self.assertEqual(provenance, "unavailable")

    def test_missing_confidence_escalates_rather_than_allowing(self):
        adapter = JevAdapter()
        advice = adapter.advise(
            JevResponse("jev-1.13", "department", "choice", "billing"), min_confidence=0.8
        )
        self.assertIs(advice.verdict, Verdict.ESCALATE)

    def test_the_question_key_is_not_polluted_with_metadata(self):
        """Calibration groups by question; a provenance suffix would split the groups."""
        advice = JevAdapter().advise(JevResponse("jev-1.13", "is_urgent", "noul", 0.96))
        self.assertEqual(advice.question, "is_urgent")
        self.assertEqual(advice.confidence_source, "derived")


class HostileResponses(unittest.TestCase):
    def test_out_of_range_and_garbage_values_never_allow(self):
        adapter = JevAdapter()
        hostile = [
            JevResponse("jev-1.13", "q", "noul", 5.0),
            JevResponse("jev-1.13", "q", "noul", -1.0),
            JevResponse("jev-1.13", "q", "noul", "yes-definitely"),
            JevResponse("jev-1.13", "q", "choice", "x", confidence=99.0),
            JevResponse("jev-1.13", "q", "choice", "x", confidence=float("nan")),
            JevResponse("jev-1.13", "q", "telepathy", 1.0),
        ]
        for response in hostile:
            with self.subTest(response=response):
                advice = adapter.advise(response, min_confidence=0.8)
                self.assertIsNot(advice.verdict, Verdict.ALLOW)

    def test_no_response_shape_can_lift_a_ceiling(self):
        adapter = JevAdapter()
        request = ActionRequest("w", ("src/outside.ts",))   # out of scope: ceiling is STOP
        for value in (0.99, 1.0, 0.5):
            advice = adapter.advise(JevResponse("jev-1.13", "q", "noul", value))
            self.assertIs(decide(POLICY, request, advice).verdict, Verdict.STOP)

    def test_a_nonsense_object_is_unusable_not_permissive(self):
        self.assertIs(
            JevAdapter().advise({"model": "jev-1.13", "value": 1.0}).verdict, Verdict.ESCALATE
        )


if __name__ == "__main__":
    unittest.main(verbosity=1)
