# Whop store listing — copy to paste

**Store:** one product, two pricing plans. Sales page: `docs/sales-page.html`.

---

## Product name

**Jev Threshold Kit**

## Tagline (under 80 chars)

> Jev's confidence is not a probability. Fit your own before you gate money on it.

## Short description (cards / Discover, ~160 chars)

> Independent tests put Jev's Choice/Score overconfident and Noul underconfident on the
> same inputs. This kit measures the distortion on your data and returns the cutoff that
> costs you least.

## Long description

TypeSafe's Jev returns typed answers with probabilities, so the obvious line to write is
`if answer.confidence >= 0.9: auto_act()`.

Independent measurements of `jev-1.13` say that line is unsafe — and unsafe in a direction
that flips with the question type. Choice and Score come back **overconfident** (temperature
≈ 3.3–3.4). Noul comes back **underconfident** (≈ 0.66). On the same inputs. So one global
cutoff is wrong in two directions at once: it auto-acts on errors in one place and burns your
cost saving on needless escalations in the other.

Both failures are silent. Jev never generates text, so there is no strange-looking output to
catch. The first symptom is a number on a monthly report.

**The Jev Threshold Kit measures that distortion on your own labelled data, fits the
correction, and returns the confidence cutoff that costs you the least money** — with the
coverage, the precision, and a conservative lower bound you can budget against.

On the 1,400-ticket example that ships with it, a raw `0.9` gate passes **100% of traffic** on
the two overconfident questions — gating nothing — and only **15.9%** on the underconfident
one, sending six of every seven cases to a human who did not need to see them.

**What's inside**

- Signed ECE with a simulated noise floor — direction, not just magnitude
- Isotonic and temperature calibration, verified on held-out data, bootstrap CI on T
- Cost-optimal threshold selection with Wilson lower bounds
- Saveable profiles that pin question + model build + map + threshold together
- A drift tripwire for when `jev-latest` moves underneath your cutoffs
- A dependency-free Jev client (OpenRouter **or** TypeSafe direct)
- A report you can forward to whoever owns the budget
- `PLAYBOOK.md` — ten rules for shaping questions, including the polarity trap
- 44 tests, zero dependencies, Python 3.9+

**It is built to tell you no.** When an error costs 60× a review and the question runs at 92%,
no threshold beats escalating everything — and it says so, instead of finding you a number
that looks fine and loses money. Below a few hundred labels it reports UNMEASURABLE rather
than pretending.

**Works today.** Jev's direct API is behind an early-access waitlist; OpenRouter serves the
same model to anyone. The kit speaks both.

## Pricing plans

| Plan | Price | Billing | Fulfilment |
|---|---|---|---|
| **The Kit** | $149 | one-time | Instant download / repo access |
| **Calibration Run** | $449 | one-time | Download + fitted profiles returned on your data |

## Who it is for

The engineer who owns a classification path in production — triage, routing, moderation,
extraction checks, agent-step gating — who has been asked whether the team can move it to Jev
for the cost and latency win, and now has to decide where to set the threshold.

## Who it is not for

Anyone without labels. The kit fits on your ground truth; there is no way around collecting a
few hundred labelled decisions per question, and it will tell you so rather than guess.

## Tags

`ai` `llm` `developer-tools` `python` `calibration` `typesafe` `jev` `mlops` `classification`

## Honest-claims checklist (keep it this way)

- Distortion figures are cited third-party measurements, never presented as ours
- Every performance number on the sales page is held-out, on the shipped dataset, reproducible
  by the buyer with one command
- No claim that Jev is good or bad — only that its probabilities need fitting before use
- No income claims, no "400× savings" headline; the 400× is TypeSafe's figure for inference
  cost, not a promise about a buyer's bill
- Not affiliated with TypeSafe AI, and the listing says so
