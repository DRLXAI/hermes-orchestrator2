# Agent Authority Boundary

**Models can reduce authority. Models can never create authority.**

A model-independent authority boundary for AI agents. Deterministic policy decides what an
agent may do; a decision model may only ever make that narrower. Useful on day one, with zero
labelled calibration data.

## PROTECT → OBSERVE → CALIBRATE

**PROTECT** — deterministic, model-free. Policy computes a ceiling for every action: what is
permitted, what resources are in scope, whether a human must approve, whether it is reversible,
whether external side effects are allowed. No model output, confidence score or classifier
result participates.

**OBSERVE** — shadow mode. Record what a decision model *would* have advised, alongside the
deterministic outcome, without letting it act. Keep `operator_agreed` (concordance) rigorously
apart from `ground_truth` (correctness). Missing truth stays UNKNOWN, never False.

**CALIBRATE** — only once genuine labelled evidence exists. This package reports what evidence
it has and refuses to conclude; it exports a clean dataset for a specialist tool such as
[jevcal](https://github.com/abhixhek/jevcal) rather than reimplementing calibration.

## Why the boundary is separate from model judgment

A confidence score answers "how sure is the model". Authority answers "what is this agent
permitted to do". They are different questions, and merging them is how a model ends up
authorising itself. Here the two never meet: the ceiling is computed with no model involved,
and advice is folded in with `narrowest` — `min` over a total order:

```
STOP (0)  <  ESCALATE (1)  <  REQUIRE_APPROVAL (2)  <  ALLOW (3)
```

There is no operation in this package that moves a verdict up the lattice. "Confidence never
creates authority" is therefore structural, not a rule someone must remember to check. An
adapter returning `ALLOW` is not granting permission — it is declining to object.

## Quickstart

```python
from authority.policy import load
from authority.engine import ActionRequest, decide, verify_effect
from authority.adapters.jev import JevAdapter, JevResponse

policy = load({
    "actions": {
        "write_doc": {"ceiling": "ALLOW", "scope": ["docs/**"], "reversible": True},
        "deploy":    {"ceiling": "ALLOW", "scope": ["infra/**"], "reversible": False,
                      "external_side_effects": True, "requires_approval": True},
    },
    "pinned_models": {"jev": "jev-1.13"},   # an alias is refused at load
})

decision = decide(policy, ActionRequest("write_doc", ("docs/pricing.md",)))
assert decision.executable

# Optional, advisory only. This can lower the verdict; it can never raise it.
advice = JevAdapter().advise(
    JevResponse("jev-1.13", "is this edit safe?", True, {"true": 0.97, "false": 0.03}),
    min_confidence=0.8,
)
decision = decide(policy, ActionRequest("write_doc", ("docs/pricing.md",)), advice)

# After acting: what was ACTUALLY touched, not what was declared beforehand.
finding = verify_effect(decision, observed_resources=["docs/pricing.md"], policy=policy)
```

## Status

Honest about what is built. The PROTECT layer, the adapter contract, the Jev adapter and the
evidence/readiness layer are implemented and tested, including a 31-test adversarial suite. No
performance or calibration claim is made anywhere in this package, because no measurement on
real traffic has been done. See `docs/GAPS.md`.

## Tests

```sh
python3 tests/test_adversarial.py    # attacks; must stay green
python3 tests/test_onboarding.py     # runs the quickstart above verbatim
```

Zero dependencies, standard library only — deliberate for security-sensitive infrastructure.
