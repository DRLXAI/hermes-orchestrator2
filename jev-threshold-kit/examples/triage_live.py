#!/usr/bin/env python3
"""Call Jev for real, then gate on a fitted profile instead of a raw number.

    export OPENROUTER_API_KEY=sk-...        # works today, no early access
    python3 examples/triage_live.py

With no key it runs in --dry mode against a canned response, so you can read the
control flow without spending anything.

The shape to copy is the bottom half: every branch gates on
`profile.act(confidence)`, never on `answer.confidence` directly, and the
resolved model id is checked against the one the profile was fitted under before
any of it is trusted.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jevkit import JevClient, MissingKeyError, Profile, check, choice, noul, score
from jevkit.client import Decision, _parse_answer

CALIBRATION = Path(__file__).resolve().parent / "calibration"

TICKET = {
    "message": (
        "This is the third time I've been charged twice this month. Your billing "
        "page still shows one subscription. I need the duplicate refunded today "
        "or I'm disputing it with my bank and cancelling."
    ),
    "account_tier": "business",
    "months_active": 14,
}

QUESTIONS = {
    "department": choice(
        instructions="Which team should handle this ticket.",
        criteria={
            "billing": "Charges, refunds, invoices, subscription changes.",
            "technical": "Bugs, failed integrations, anything not working.",
            "sales": "Pricing, plan comparisons, plans they do not have.",
            "other": "None of the above applies.",
        },
    ),
    # Positive polarity on purpose: jev-1.13 degrades when `true` means `no`.
    "refund_eligible": noul(
        instructions="The customer is eligible for a refund under a duplicate-charge policy.",
    ),
    "frustration": score(
        instructions="How frustrated the customer is with the company right now.",
        criteria=[
            "Neutral; asking a question.",
            "Annoyed; something is not working and they said so.",
            "Angry; repeated failures, threats to leave or escalate.",
        ],
    ),
}

CANNED = Decision(
    model="typesafe/jev-1.13-20260917",
    answers={
        "department": _parse_answer("department", {
            "type": "choice", "choice": "billing",
            "probabilities": {"billing": 0.94, "technical": 0.04, "sales": 0.01, "other": 0.01},
            "confidence": 0.9996,
        }),
        "refund_eligible": _parse_answer("refund_eligible", {"type": "noul", "noul": 0.88}),
        "frustration": _parse_answer("frustration", {
            "type": "score", "score": 1.9, "confidence": 0.9987,
        }),
    },
    input_tokens=214,
)


def main() -> int:
    if not CALIBRATION.is_dir():
        print("No profiles. Run this first:\n"
              "  python3 -m jevkit.cli fit examples/data/triage_labeled.jsonl \\\n"
              "      --error 6.00 --review 0.55 --out examples/calibration \\\n"
              "      --model typesafe/jev-1.13-20260917", file=sys.stderr)
        return 3
    profiles = {p.stem: Profile.load(p) for p in CALIBRATION.glob("*.json")}

    try:
        client = JevClient()
        print(f"asking {client.provider.label}")
        decision = client.ask(TICKET, QUESTIONS)
    except MissingKeyError:
        print("no key set -- using a canned response (set OPENROUTER_API_KEY for a real call)")
        decision = CANNED

    print(f"served by {decision.model}  "
          f"{decision.input_tokens} tok  ${decision.cost_usd:.6f}  "
          f"{decision.latency_ms:.0f}ms\n")

    # Before trusting a single threshold: is this the build they were fitted on?
    stale = [c for c in (check(p, decision.model) for p in profiles.values()) if c.drifted]
    for drift in stale:
        print(f"!! {drift.message()}\n")
    if stale:
        print("Thresholds are stale. Escalating everything until they are re-fitted.")
        return 2

    for name, answer in decision.answers.items():
        profile = profiles.get(name)
        if profile is None:
            print(f"{name:<16} {answer.value!r:<12} no profile -> escalate")
            continue
        calibrated = profile.calibrate(answer.confidence)
        verdict = "AUTO" if profile.act(answer.confidence) else "escalate"
        print(
            f"{name:<16} {str(answer.value):<12} "
            f"raw {answer.confidence:.4f} -> calibrated {calibrated:.3f} "
            f"(bar {profile.threshold:.2f})   {verdict}"
        )

    print()
    # Policy lives here, in code, as separate conditions -- not averaged into a
    # score inside the model, where a threshold change means a re-run.
    angry = decision["frustration"].value >= 1.5
    owed = profiles["refund_eligible"].act(decision["refund_eligible"].confidence)
    if angry and owed:
        print("-> refund queue, priority: angry customer with a valid duplicate-charge claim")
    elif angry:
        print("-> human review: angry, refund eligibility not established")
    else:
        print("-> normal queue")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
