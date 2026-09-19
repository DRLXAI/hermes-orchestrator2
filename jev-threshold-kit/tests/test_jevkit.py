#!/usr/bin/env python3
"""Tests for the Jev Threshold Kit.

The bar for a calibration tool is higher than "it runs": every number it prints
is one somebody will set a production threshold from. So these check the
statistics against cases with known answers -- a recoverable temperature, a
monotone fit, a Wilson bound with a published value -- rather than only
checking that functions return floats.
"""

from __future__ import annotations

import io
import json
import random
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jevkit import (  # noqa: E402
    Economics, JevError, MissingKeyError, Profile, assess, check, choice, choose,
    fit, fit_isotonic, fit_temperature, is_alias, noise_floor, noul,
    operating_point, resolve_provider, score, split,
)
from jevkit.calibrate import _logit, _sigmoid  # noqa: E402
from jevkit.cli import load_labels, main  # noqa: E402
from jevkit.client import _parse_answer  # noqa: E402
from jevkit.thresholds import wilson_lower  # noqa: E402


def overconfident_sample(n: int, temperature: float, seed: int = 0):
    """A sample whose reported confidence is distorted by a known temperature."""
    rng = random.Random(seed)
    confidences, correct = [], []
    for _ in range(n):
        true_p = 0.55 + 0.44 * rng.random() ** 0.5
        confidences.append(_sigmoid(_logit(true_p) * temperature))
        correct.append(rng.random() < true_p)
    return confidences, correct


class MetricsCarryDirection(unittest.TestCase):
    """A scalar ECE cannot say which way the error runs. These must."""

    def test_overconfidence_is_reported_as_overconfidence(self):
        conf, cor = overconfident_sample(2000, temperature=3.0, seed=1)
        result = assess(conf, cor)
        self.assertEqual(result.direction, "overconfident")
        self.assertGreater(result.signed_error, 0)

    def test_underconfidence_is_reported_as_underconfidence(self):
        conf, cor = overconfident_sample(2000, temperature=0.5, seed=2)
        result = assess(conf, cor)
        self.assertEqual(result.direction, "underconfident")
        self.assertLess(result.signed_error, 0)

    def test_a_calibrated_sample_is_called_calibrated(self):
        conf, cor = overconfident_sample(2000, temperature=1.0, seed=3)
        self.assertEqual(assess(conf, cor).direction, "calibrated")

    def test_a_thin_spread_sample_is_unmeasurable_not_calibrated(self):
        """The n=60 trap: absence of evidence must not read as evidence."""
        rng = random.Random(4)
        conf = [rng.uniform(0.3, 1.0) for _ in range(60)]
        result = assess(conf, [rng.random() < c for c in conf])
        self.assertEqual(result.direction, "unmeasurable")
        self.assertFalse(result.measurable)
        self.assertIn("UNMEASURABLE", result.verdict())
        self.assertIn("labelled items", result.verdict())

    def test_measurability_follows_the_distribution_not_only_the_count(self):
        """A concentrated sample resolves at n=60 where a spread one cannot.

        Both are n=60. The floor differs by ~100x, because binning error
        depends on how many bins the predictions actually occupy. Reporting
        this as a flat "n is too small" rule would be wrong in both directions.
        """
        rng = random.Random(41)
        concentrated = [0.999 + 0.0009 * rng.random() for _ in range(60)]
        spread = [rng.uniform(0.3, 1.0) for _ in range(60)]
        self.assertLess(noise_floor(concentrated, trials=80), 0.05)
        self.assertGreater(noise_floor(spread, trials=80), 0.05)

    def test_noise_floor_falls_as_the_sample_grows(self):
        rng = random.Random(5)
        small = [rng.uniform(0.5, 1.0) for _ in range(150)]
        large = [rng.uniform(0.5, 1.0) for _ in range(3000)]
        self.assertGreater(noise_floor(small, trials=60), noise_floor(large, trials=60))

    def test_binning_rejects_mismatched_input(self):
        with self.assertRaises(ValueError):
            assess([0.5, 0.6], [True])
        with self.assertRaises(ValueError):
            assess([], [])


class CalibrationMapsRecoverTheTruth(unittest.TestCase):
    def test_temperature_recovers_a_known_value(self):
        conf, cor = overconfident_sample(30000, temperature=3.3, seed=6)
        fitted = fit_temperature(conf, cor, bootstrap=0)
        self.assertAlmostEqual(fitted.temperature, 3.3, delta=0.25)

    def test_temperature_interval_usually_covers_the_truth(self):
        """A 90% interval misses ~10% of the time by construction.

        So this asserts the coverage property across independent draws rather
        than on one lucky seed -- a single-seed assertion here would be a test
        that fails on a schedule nobody can predict.
        """
        covered = 0
        trials = 10
        for seed in range(trials):
            conf, cor = overconfident_sample(800, temperature=3.3, seed=100 + seed)
            fitted = fit_temperature(conf, cor, bootstrap=80, seed=seed)
            self.assertIsNotNone(fitted.interval)
            low, high = fitted.interval
            covered += int(low <= 3.3 <= high)
        self.assertGreaterEqual(
            covered, 7, f"90% interval covered only {covered}/{trials} draws"
        )

    def test_isotonic_output_is_monotone(self):
        conf, cor = overconfident_sample(1500, temperature=2.5, seed=8)
        mapping = fit_isotonic(conf, cor)
        probed = [mapping(x / 100) for x in range(101)]
        for earlier, later in zip(probed, probed[1:]):
            self.assertLessEqual(earlier, later + 1e-12)

    def test_calibration_improves_on_held_out_data(self):
        """The claim the whole kit rests on, measured where it counts."""
        conf, cor = overconfident_sample(3000, temperature=3.0, seed=9)
        fit_c, fit_y, check_c, check_y = split(conf, cor, seed=9)
        mapping = fit_isotonic(fit_c, fit_y)
        before = assess(check_c, check_y, seed=9).ece
        after = assess([mapping(c) for c in check_c], check_y, seed=9).ece
        self.assertLess(after, before / 2)

    def test_fitters_reject_empty_and_mismatched_input(self):
        for fitter in (fit_isotonic, fit_temperature):
            with self.assertRaises(ValueError):
                fitter([], [])
            with self.assertRaises(ValueError):
                fitter([0.5], [True, False])

    def test_split_refuses_to_produce_an_empty_side(self):
        # One item cannot be cut in two: the fitting side rounds to zero.
        with self.assertRaises(ValueError):
            split([0.5], [True], holdout=0.3)
        with self.assertRaises(ValueError):
            split([0.5], [True], holdout=1.5)

    def test_split_is_deterministic_and_loses_nothing(self):
        conf, cor = overconfident_sample(200, temperature=2.0, seed=16)
        first = split(conf, cor, seed=3)
        second = split(conf, cor, seed=3)
        self.assertEqual(first, second)
        self.assertEqual(len(first[0]) + len(first[2]), 200)


class ThresholdsCostMoney(unittest.TestCase):
    def test_wilson_lower_matches_a_published_value(self):
        # 97/100 successes, one-sided 95%: standard reference value ~0.9276.
        self.assertAlmostEqual(wilson_lower(97, 100), 0.9276, places=3)

    def test_wilson_is_conservative_on_thin_coverage(self):
        self.assertLess(wilson_lower(40, 40), 0.95)
        self.assertGreater(wilson_lower(400, 400), 0.99)

    def test_empty_coverage_is_not_perfect_precision(self):
        """A threshold nothing clears must not win by looking flawless."""
        point = operating_point([0.1, 0.2], [True, True], 0.99, Economics(1.0, 1.0))
        self.assertFalse(point.automates)
        self.assertEqual(point.precision, 0.0)

    def test_expensive_errors_block_automation(self):
        conf, cor = overconfident_sample(1200, temperature=1.0, seed=10)
        decision = choose(conf, cor, Economics(cost_error=5000.0, cost_review=0.10))
        self.assertFalse(decision.worth_automating)
        self.assertIn("DO NOT AUTOMATE", decision.verdict())

    def test_cheap_errors_permit_wide_automation(self):
        conf, cor = overconfident_sample(1200, temperature=1.0, seed=11)
        decision = choose(conf, cor, Economics(cost_error=0.05, cost_review=2.0))
        self.assertTrue(decision.worth_automating)
        self.assertGreater(decision.best.coverage, 0.8)

    def test_stakes_move_the_threshold_upward(self):
        conf, cor = overconfident_sample(2000, temperature=1.0, seed=12)
        cheap = choose(conf, cor, Economics(cost_error=0.5, cost_review=0.4))
        dear = choose(conf, cor, Economics(cost_error=25.0, cost_review=0.4))
        self.assertGreaterEqual(dear.best.threshold, cheap.best.threshold)

    def test_economics_rejects_nonsense(self):
        with self.assertRaises(ValueError):
            Economics(cost_error=-1.0, cost_review=1.0)
        with self.assertRaises(ValueError):
            Economics(cost_error=0.0, cost_review=0.0)


class ProfilesRoundTrip(unittest.TestCase):
    def setUp(self):
        conf, cor = overconfident_sample(1200, temperature=3.0, seed=13)
        self.profile = fit(
            conf, cor, Economics(cost_error=4.0, cost_review=0.5),
            question="department", kind="choice",
            model="typesafe/jev-1.13-20260917", seed=13,
        )

    def test_profile_survives_save_and_load(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.profile.save(Path(directory) / "department.json")
            reloaded = Profile.load(path)
        self.assertEqual(reloaded.threshold, self.profile.threshold)
        self.assertEqual(reloaded.model, self.profile.model)
        self.assertAlmostEqual(reloaded.calibrate(0.83), self.profile.calibrate(0.83), places=12)

    def test_profile_rejects_a_future_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "future.json"
            path.write_text(json.dumps({"profile_version": 999, "question": "x"}))
            with self.assertRaises(ValueError):
                Profile.load(path)

    def test_fit_refuses_too_few_labels(self):
        with self.assertRaises(ValueError):
            fit([0.9] * 10, [True] * 10, Economics(1.0, 1.0), question="tiny")

    def test_act_respects_the_do_not_automate_verdict(self):
        conf, cor = overconfident_sample(800, temperature=1.0, seed=14)
        blocked = fit(
            conf, cor, Economics(cost_error=9000.0, cost_review=0.05),
            question="refund", seed=14,
        )
        self.assertFalse(blocked.automates)
        self.assertFalse(blocked.act(0.999))


class DriftIsLoud(unittest.TestCase):
    def setUp(self):
        conf, cor = overconfident_sample(900, temperature=2.0, seed=15)
        self.profile = fit(
            conf, cor, Economics(2.0, 0.5), question="dept",
            model="typesafe/jev-1.13-20260917", seed=15,
        )

    def test_same_model_does_not_trip(self):
        self.assertFalse(check(self.profile, "typesafe/jev-1.13-20260917").drifted)

    def test_a_moved_alias_trips(self):
        result = check(self.profile, "typesafe/jev-1.14-20261101")
        self.assertTrue(result.drifted)
        self.assertIn("STALE", result.message())

    def test_strict_mode_raises(self):
        from jevkit.guard import ModelDriftError
        with self.assertRaises(ModelDriftError):
            check(self.profile, "typesafe/jev-2.0", strict=True)

    def test_aliases_are_identified(self):
        self.assertTrue(is_alias("jev-latest"))
        self.assertTrue(is_alias("jev-preview"))
        self.assertFalse(is_alias("typesafe/jev-1.13-20260917"))


class ClientSpeaksTheProtocol(unittest.TestCase):
    def test_question_builders_produce_the_type_discriminator(self):
        self.assertEqual(noul("Is it urgent.")["type"], "noul")
        self.assertEqual(choice("Team.", {"a": "x", "b": "y"})["type"], "choice")
        self.assertEqual(score("Degree.", ["low", "high"])["type"], "score")

    def test_question_builders_reject_degenerate_answer_spaces(self):
        with self.assertRaises(ValueError):
            choice("Team.", {"only": "one"})
        with self.assertRaises(ValueError):
            score("Degree.", ["single"])
        with self.assertRaises(ValueError):
            choice("Team.", {str(i): "x" for i in range(300)})

    def test_each_primitive_parses(self):
        self.assertEqual(_parse_answer("d", {
            "type": "choice", "choice": "technical",
            "probabilities": {"technical": 0.85, "billing": 0.15}, "confidence": 0.82,
        }).value, "technical")
        self.assertAlmostEqual(_parse_answer("s", {
            "type": "score", "score": 1.35, "confidence": 0.78,
        }).value, 1.35)

    def test_noul_confidence_is_distance_from_the_coin_flip(self):
        self.assertAlmostEqual(_parse_answer("u", {"type": "noul", "noul": 0.5}).confidence, 0.0)
        self.assertAlmostEqual(_parse_answer("u", {"type": "noul", "noul": 0.97}).confidence, 0.94)
        self.assertAlmostEqual(_parse_answer("u", {"type": "noul", "noul": 0.03}).confidence, 0.94)

    def test_an_unknown_primitive_raises_rather_than_vanishing(self):
        with self.assertRaises(JevError):
            _parse_answer("q", {"type": "rank", "rank": 2})

    def test_provider_resolution_prefers_direct_access(self):
        self.assertEqual(resolve_provider(env={"OPENROUTER_API_KEY": "k"}).name, "openrouter")
        both = {"OPENROUTER_API_KEY": "k", "TYPESAFE_API_KEY": "t"}
        self.assertEqual(resolve_provider(env=both).name, "typesafe")
        self.assertEqual(resolve_provider(prefer="openrouter", env=both).name, "openrouter")

    def test_missing_credentials_say_which_to_set(self):
        with self.assertRaises(MissingKeyError) as caught:
            resolve_provider(env={})
        self.assertIn("OPENROUTER_API_KEY", str(caught.exception))

    def test_a_key_never_appears_in_a_label(self):
        provider = resolve_provider(env={"OPENROUTER_API_KEY": "sk-secret-value"})
        self.assertNotIn("secret", provider.label)


class CommandLineWorks(unittest.TestCase):
    def test_labels_load_and_group_by_question(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "labels.jsonl"
            path.write_text(
                '{"question":"a","kind":"choice","confidence":0.9,"correct":true}\n'
                "# a comment\n\n"
                '{"question":"a","kind":"choice","confidence":0.4,"correct":false}\n'
                '{"question":"b","kind":"noul","confidence":0.8,"correct":true}\n'
            )
            columns = load_labels(path)
        self.assertEqual(sorted(columns), ["a", "b"])
        self.assertEqual(len(columns["a"][0]), 2)
        self.assertEqual(columns["b"][2], "noul")

    def test_malformed_labels_name_the_line(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.jsonl"
            path.write_text('{"question":"a","confidence":0.9,"correct":true}\n{oops\n')
            with self.assertRaises(ValueError) as caught:
                load_labels(path)
            self.assertIn(":2", str(caught.exception))

    def test_out_of_range_confidence_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.jsonl"
            path.write_text('{"question":"a","confidence":1.4,"correct":true}\n')
            with self.assertRaises(ValueError):
                load_labels(path)

    def test_fit_then_report_then_check_end_to_end(self):
        dataset = Path(__file__).resolve().parents[1] / "examples/data/triage_labeled.jsonl"
        self.assertTrue(dataset.exists(), "example dataset is missing")
        with tempfile.TemporaryDirectory() as directory:
            buffer = io.StringIO()
            with redirect_stdout(buffer), redirect_stderr(buffer):
                fit_code = main([
                    "fit", str(dataset), "--error", "6", "--review", "0.55",
                    "--out", directory, "--model", "typesafe/jev-1.13-20260917",
                ])
                report_code = main(["report", directory, "--volume", "1000"])
                same_code = main(["check", directory, "--model", "typesafe/jev-1.13-20260917"])
                moved_code = main(["check", directory, "--model", "typesafe/jev-1.14"])
            written = sorted(p.name for p in Path(directory).glob("*.json"))
            output = buffer.getvalue()

        self.assertEqual(fit_code, 0)
        self.assertEqual(report_code, 0)
        self.assertEqual(same_code, 0)
        self.assertEqual(moved_code, 2, "a moved model must be a finding, not a pass")
        self.assertEqual(
            written, ["department.json", "frustration.json", "refund_eligible.json"]
        )
        self.assertIn("JEV THRESHOLD REPORT", output)
        self.assertIn("STALE", output)

    def test_a_missing_file_is_incomplete_not_a_crash(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer), redirect_stderr(buffer):
            code = main(["fit", "/nonexistent.jsonl", "--error", "1", "--review", "1"])
        self.assertEqual(code, 3)


class ExampleDataMatchesPublishedDistortions(unittest.TestCase):
    """The shipped dataset must actually show the thing the kit is about."""

    def setUp(self):
        dataset = Path(__file__).resolve().parents[1] / "examples/data/triage_labeled.jsonl"
        self.columns = load_labels(dataset)

    def test_choice_and_score_are_overconfident(self):
        for question in ("department", "frustration"):
            confidences, correct, _ = self.columns[question]
            self.assertEqual(assess(confidences, correct).direction, "overconfident", question)

    def test_noul_is_underconfident(self):
        confidences, correct, _ = self.columns["refund_eligible"]
        self.assertEqual(assess(confidences, correct).direction, "underconfident")


if __name__ == "__main__":
    unittest.main(verbosity=2)
