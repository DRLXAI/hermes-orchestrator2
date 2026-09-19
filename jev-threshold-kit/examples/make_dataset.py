#!/usr/bin/env python3
"""Generate a labelled triage set that reproduces Jev's published distortions.

This stands in for the file you will produce from your own traffic. It is
synthetic, and it is honest about being synthetic -- but the *shape* of the
distortion is not invented. It reproduces what independent measurements of
jev-1.13 report on the same inputs:

    Choice, Score   overconfident,  temperature ~3.3-3.4
    Noul            underconfident, temperature ~0.66

Which is the single most important thing about calibrating Jev: the sign of
the error flips with the primitive, so one global threshold across a workload
is wrong in two directions at once. Overall accuracy stays high (~94%), which
is exactly why the problem is invisible without measuring it.

Replace this with real labels as soon as you have 300 per question. Run:

    python examples/make_dataset.py > examples/data/triage_labeled.jsonl
"""

from __future__ import annotations

import json
import math
import random
import sys


def sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)


def logit(p: float) -> float:
    q = min(max(p, 1e-6), 1.0 - 1e-6)
    return math.log(q / (1.0 - q))


def distort(true_probability: float, temperature: float) -> float:
    """What the model *reports* given what is *actually* true.

    Inverse of temperature scaling: if fixing the model means dividing its
    logit by T, then the model produced that logit by multiplying the honest
    one by T. T > 1 reports too confidently; T < 1 reports too timidly.
    """
    return sigmoid(logit(true_probability) * temperature)


#: question id -> (primitive, temperature, accuracy band)
#:
#: The bands differ because the questions differ in difficulty, not to make a
#: point. `department` is a clean four-way split; `refund_eligible` is a policy
#: judgment with genuine grey area; `frustration` is a subjective rubric.
QUESTIONS = {
    "department":      ("choice", 3.30, (0.82, 0.995)),
    "refund_eligible": ("noul",   0.66, (0.74, 0.985)),
    "frustration":     ("score",  3.40, (0.70, 0.97)),
}

N_TICKETS = 1400


def main() -> int:
    rng = random.Random(20260919)
    for ticket in range(N_TICKETS):
        for question, (kind, temperature, (low, high)) in QUESTIONS.items():
            # Beta-ish skew toward the top of the band: most decisions are easy,
            # a tail of them genuinely is not.
            u = rng.random() ** 0.45
            true_probability = low + (high - low) * u
            reported = distort(true_probability, temperature)
            print(json.dumps({
                "ticket": f"T-{ticket:05d}",
                "question": question,
                "kind": kind,
                "confidence": round(reported, 6),
                "correct": rng.random() < true_probability,
            }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
