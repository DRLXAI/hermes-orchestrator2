# Migrating from the Jev Threshold Kit

## They solve different problems

The kit answers **"at what confidence should I trust this classification?"** — per-question
calibration and cost-optimal thresholds, given labelled data you already have.

This package answers **"when may this agent act, when must it stop, and how do I prove the
boundary held?"** — and is useful on day one with no labelled data at all.

They are complementary, not sequential. Most integrations want the boundary first (it works
immediately) and calibration later (it needs a few hundred labels you do not have yet).

## What carried over

| From the kit | Here | Note |
|---|---|---|
| `guard.is_alias()` | `policy.is_alias()` | Reused as-is. A moving alias is refused at policy load rather than warned about at call time. |
| `guard.check()` drift detection | `_guard_model_pinning` | Same idea, promoted: a drifted model is `STOP`, not a log line. |
| Wilson bounds | `calibration.wilson_interval` | Reimplemented two-sided for readiness intervals. |
| The confidence convention | `adapters/jev.confidence_of` | Carried over **with its caveat documented**: for boolean questions confidence is `|p_yes - 0.5| * 2`, a symmetric distance from a coin flip, not P(correct). |

## What did not carry over, and why

- **Isotonic/temperature fitting, cost sweeps, profile artefacts.** Not reimplemented. Use the kit,
  or [jevcal](https://github.com/abhixhek/jevcal), and feed either from `authority export`.
- **`guard.pinned()`.** Deleted. It was dead: both branches returned the input unchanged and it
  never warned despite its docstring.
- **A fixed sample-count threshold.** The kit refuses to fit under 50 labels and prefers 300+.
  That is a sensible default for *fitting a curve*; it is the wrong shape for *authorising an
  agent*, where the question is always "enough for what target?". Readiness here is derived from
  the operator's stated error target instead.

## If you are running the kit today

Nothing breaks. The kit is unchanged apart from three corrections:

1. `guard.pinned()` removed (dead code, referenced nowhere).
2. Both `saving_per_1k` properties now state their baseline explicitly, with unambiguous aliases
   (`saving_vs_raw_090_per_1k`, `saving_vs_calibrated_090_per_1k`). **No behaviour changed** —
   they were always two deliberately different comparisons, and the code always said so in a
   comment; only the names invited confusion.
3. The README's example table is now marked synthetic at the point of display.

Field names on saved profiles were deliberately left alone so existing artefacts still load.

## Suggested path

1. Write a policy, run `authority check`. Minutes.
2. Wrap your riskiest action. The boundary works with no model and no data.
3. Turn on shadow observation: record advice, confirm outcomes as they resolve.
4. When you have labels, set a target and run `authority readiness`.
5. When it says MEETS_TARGET, `authority export` and hand the dataset to the kit or jevcal.
