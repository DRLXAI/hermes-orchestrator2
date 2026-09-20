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
    JevResponse("jev-1.13", "is this edit safe?", "noul", 0.97),
    min_confidence=0.8,
)
decision = decide(policy, ActionRequest("write_doc", ("docs/pricing.md",)), advice)

# After acting: what was ACTUALLY touched, not what was declared beforehand.
finding = verify_effect(decision, observed_resources=["docs/pricing.md"], policy=policy)
```

## CLI

```sh
authority init policy.json                 # starter policy
authority check policy.json                # validate; print fingerprint
authority decide policy.json --action write_doc --resource docs/a.md --db ev.db
authority review policy.json --db ev.db    # what is waiting for a human
authority approve policy.json <id> --db ev.db --by david
authority confirm policy.json <id> --db ev.db --by david      # ground truth, not agreement
authority readiness policy.json --db ev.db --question "is this edit safe?" --max-error 0.05
authority export policy.json --db ev.db --out labelled.jsonl  # for jevcal or the kit
authority verify policy.json --db ev.db    # evidence chain intact?
```

## Status

Honest about what is built. PROTECT, the adapter contract, the Jev adapter, persistent evidence,
the review workflow, target-dependent readiness and the CLI are implemented and tested. **No
performance or calibration claim is made anywhere in this package**, because nothing has been
measured on real traffic. See `docs/GAPS.md` for what is missing and
`docs/THREAT-MODEL.md` for what this does not defend.

## Tests

```sh
./run_tests.sh          # all four suites
```

66 tests: adversarial attacks, the Jev adapter against Jev's real response shape, persistence and
trust, and the quickstart above executed verbatim. Five critical invariants are mutation-tested —
inverting any one of them fails the suite.

Zero dependencies, standard library only — deliberate for security-sensitive infrastructure.
