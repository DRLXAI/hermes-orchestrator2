# Jev Threshold Kit

**Jev's confidence is not a probability. Fit your own before you gate money on it.**

Zero dependencies. Python 3.9+. Works today through OpenRouter — no early-access key.

---

## The 30-second version

Jev returns typed answers with probabilities, so the obvious thing to write is:

```python
if answer.confidence >= 0.9:
    auto_refund()
```

Independent measurements of `jev-1.13` say that line is unsafe, and unsafe **in a
direction that flips with the question type**:

| Primitive | Measured | What `>= 0.9` does to you |
|---|---|---|
| `Choice` | overconfident, T ≈ 3.3 | auto-acts on errors at a rate the number hides |
| `Score` | overconfident, T ≈ 3.4 | same, usually worse |
| `Noul` | **under**confident, T ≈ 0.66 | escalates cases that were fine, burning the savings you switched for |

One global cutoff across a workload is therefore wrong in two directions at once.
And it fails **silently** — Jev never generates text, so there is no weird output
to catch your eye. The first symptom is a number on a monthly report.

This kit measures the distortion on *your* labelled data, fits a correction, and
picks the cutoff that costs you the least money.

## What it does to the shipped example

```
  question            fitted      cover    prec     $/1k   |  raw 0.9 cover    prec     $/1k
  ----------------------------------------------------------------------------------------
  department          >= 0.93     57.0%  99.58%   250.75   |         100.0%  95.01%   299.33
  frustration         >= 0.92     42.0%  94.92%   447.07   |         100.0%  90.26%   584.36
  refund_eligible     >= 0.90     51.8%  94.95%   422.01   |          15.9%  97.01%   491.01
```

Read the right-hand block. On the two overconfident questions a raw `0.9` gate
lets through **100% of traffic** — it gates nothing at all — at 95% and 90%
precision. On the underconfident one it covers **15.9%**, sending six out of
seven cases to a human who did not need to see them.

## Install

```sh
git clone <this repo> && cd jev-threshold-kit
python3 tests/test_jevkit.py      # 44 tests, no network, no dependencies
```

There is nothing to install. It is standard library only, on purpose: this runs
inside your repo and in your CI, and a calibration tool with a dependency tree is
a calibration tool nobody adopts.

## Use it in four steps

**1 — Collect labels.** One JSONL line per decision. 300+ per question; below
that the noise floor swallows the effect.

```json
{"question": "department", "kind": "choice", "confidence": 0.82, "correct": true}
```

You need `correct` — whether Jev's answer was actually right. That is the whole
cost of using this kit, and there is no way around it: nobody can tell you your
thresholds without your labels.

**2 — Fit.** Give it what an error and a review each cost you, in your currency.

```sh
python3 -m jevkit.cli fit labels.jsonl \
    --error 6.00 --review 0.55 \
    --model typesafe/jev-1.13-20260917 \
    --out calibration/ --volume 400000
```

The *ratio* is what selects the threshold, so rough numbers are fine. An order
of magnitude matters; a factor of two mostly does not.

**3 — Ship the profile.** Commit `calibration/*.json` and gate on it:

```python
from jevkit import Profile

department = Profile.load("calibration/department.json")

decision = client.ask(ticket, {"department": choice(...)})
if department.act(decision["department"].confidence):
    route_to(decision["department"].value)
else:
    escalate()
```

**4 — Watch for drift.** `jev-latest` is an alias, and TypeSafe documents that
aliases move. A threshold is a statement about one build's distortion, so when
the alias moves your cutoff silently becomes a guess — no deploy, no error.

```sh
python3 -m jevkit.cli check calibration/ --model "$(your_live_model_id)"
```

Exit `2` means stale. Put it in CI.

## What is in the box

| | |
|---|---|
| `jevkit/metrics.py` | ECE **with its sign** and a simulated noise floor, so a number means something |
| `jevkit/calibrate.py` | Isotonic + temperature fitting, held-out verified, bootstrap CI on T |
| `jevkit/thresholds.py` | Cost-optimal cutoff with Wilson lower bounds — including "do not automate" |
| `jevkit/profile.py` | The saveable artefact: question + model + map + threshold, pinned together |
| `jevkit/guard.py` | Drift tripwire for when the alias moves |
| `jevkit/client.py` | Dependency-free Jev client, OpenRouter **or** direct |
| `jevkit/report.py` | The memo you forward to whoever owns the budget |
| `PLAYBOOK.md` | How to turn a classification prompt into typed questions without stepping on the known traps |

## Three things it will tell you that you will not like

**"DO NOT AUTOMATE."** If an error costs 60× a review and the question runs at
92%, no cutoff beats sending everything to a human. The kit says so instead of
finding you a threshold that looks fine and loses money.

**"UNMEASURABLE."** Under a few hundred labels a perfect model and a broken one
score the same ECE. The most-cited public Jev benchmark is n=60, where a flawless
model scores ≈0.045 — the range it reports. The kit refuses to pretend.

**Your error cost is probably wrong.** Most people guess the review cost well and
the error cost by a factor of ten, because the error cost includes the refund, the
churn and the apology, not the agent minute.

## Evidence

The distortion figures above are other people's measurements, not ours:

- Per-type signs (Choice/Score T≈3.3–3.4 overconfident, Noul T≈0.66 underconfident)
  on 900 rule-generated tickets — [scienthoon/jev-ood-calibration](https://github.com/scienthoon/jev-ood-calibration)
- 800-item difficulty gradient, ECE 2.1–2.5× noise floor at every tier, and the
  n=60 argument — [SamuelSacco/jev-exploration](https://github.com/SamuelSacco/jev-exploration)
- Protocol, primitives and the polarity trap —
  [awesome-jev-by-typesafe](https://github.com/Anil-matcha/awesome-jev-by-typesafe),
  [typesafe-jev-examples](https://github.com/rajivkuriakose/typesafe-jev-examples)
- TypeSafe's own position: calibration "is measured across groups of predictions;
  it does not guarantee that an individual answer is correct," and thresholds
  should be tested on your own data. This kit is how you do that.

These are third-party findings about a model that shipped in September 2026 and is
still early access. Re-measure on your own traffic — which is, after all, the point.

## Licence

MIT. Not affiliated with TypeSafe AI.
