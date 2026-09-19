# The Playbook

Everything here is about decisions you make *before* the calibration run. A
question that is badly shaped cannot be rescued by a threshold.

---

## 1. Pick the primitive by what the answer means

Not by what is convenient to parse. These are three different measurements.

| The answer is… | Primitive | Returns |
|---|---|---|
| one of a fixed set | `Choice` | winner + distribution + confidence |
| a position on an ordered rubric | `Score` | a float that may land *between* levels |
| whether a condition holds | `Noul` | P(yes), and **no confidence field** |

The common mistake is encoding a degree as a `Choice` (`"low"`, `"medium"`,
`"high"`). You lose the ordering: the model cannot express "between medium and
high", and your code cannot tell that `low`→`high` is a bigger error than
`low`→`medium`. If the values have an order, it is a `Score`.

The reverse mistake is a `Score` over unordered categories. "Which department"
has no midpoint; 1.5 between `billing` and `technical` is meaningless.

## 2. Always give it a way to say "none of these"

The model cannot return a value you did not offer. Without an escape hatch,
every out-of-scope input is forced into one of your real categories and arrives
looking like an ordinary confident answer.

```python
criteria = {
    "billing":   "Charges, refunds, invoices, subscription changes.",
    "technical": "Bugs, failed integrations, anything not working.",
    "sales":     "Pricing, plan comparisons, plans they do not have.",
    "other":     "None of the above applies.",     # <- not optional
}
```

`other` firing more than you expect is a finding, not a nuisance: it is the
model telling you your taxonomy does not cover your traffic.

## 3. Do not fight the polarity

TypeSafe documents that `jev-1.13` degrades when the instructions and the
criteria pull in opposite directions — a `Noul` phrased so that *true* means
*no* scores worse than the same question phrased positively.

```python
noul("The customer is NOT eligible for a refund.")   # worse
noul("The customer is eligible for a refund.")       # better
```

Write the condition you want to be true, and invert in your own code. It is free
there and it is not free in the model.

## 4. Decompose. Seven narrow questions cost one round trip

Questions in a single request are answered in one parallel pass, so asking seven
costs roughly what asking one costs in wall-clock time. That changes the design:
instead of one broad "how should we handle this ticket", ask seven narrow things
and combine them in code.

Narrow questions calibrate better, fail more legibly, and let you set a
different threshold per question — which is the entire point of the next rule.

## 5. Scale the threshold to the stakes, not to your comfort

One global `0.85` is the most expensive habit in this space.

```python
ROUTABLE_CONFIDENCE = 0.60   # wrong = the ticket moves queues once
BILLING_CONFIDENCE  = 0.92   # wrong = this branch can move money
```

`jevkit` does this arithmetic for you: give it `cost_error` and `cost_review`
per question and it returns the cutoff that minimises expected cost. The ratio
is what matters, so estimates are fine.

## 6. Keep policy in code, never in the model

```python
escalate = churn > 0.70 or (impact >= 1.5 and urgent > 0.70)
```

Separate conditions, because an "any serious signal" rule does not survive being
averaged into a weighted score. Change a threshold and nothing needs re-running,
nothing needs re-prompting, and the change is visible in a diff.

Jev never generates text, calls your functions, or decides what happens next.
Thresholds, routing and escalation live in your code. That is a feature: it is
the part you can test.

## 7. Treat the output as a monotone score, not a probability

This is the finding that motivates the whole kit. Higher does mean more likely.
The number itself is not the likelihood, and the error has a sign that depends
on the primitive. So:

- **do** use it to rank and to threshold, once you have fitted a map
- **do not** multiply two Jev probabilities together and expect a joint
- **do not** put a raw confidence in an SLA, a report, or a customer-facing number
- **do not** copy a threshold from a blog post, including this one

## 8. Pin the version, log what answered

```python
decision = client.ask(state, questions)
log.info("jev", model=decision.model)     # the RESOLVED id, not your alias
```

`jev-latest` moves. Every threshold you fit is a statement about one build's
distortion. Pin a versioned id in production, record it in the profile, and put
`jevkit check` in CI so a moved alias is a failed build rather than a quiet
change in behaviour six weeks later.

## 9. Shadow before you switch

Run Jev beside your existing classifier, log both, act on neither. A week of
disagreements is a labelled dataset and a risk assessment at the same time — and
it is the cheapest labels you will ever collect, because your current system's
output is the label.

## 10. Re-fit when anything upstream changes

A calibration map is fitted to a distribution. New traffic mix, new product, new
customer segment, new prompt template, new model build — any of these can move
it. Re-fitting costs a few hundred labels and an afternoon. Not re-fitting costs
whatever the threshold was protecting.
