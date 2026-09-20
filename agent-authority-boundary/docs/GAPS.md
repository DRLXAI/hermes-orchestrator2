# Gap assessment

Written against the build proposal. Honest about what is not done.

## Built and tested (34 tests, zero dependencies)

| Proposal item | Status |
|---|---|
| 1 PROTECT — deterministic boundary | **Done.** Verdict lattice, 7 guards, ceiling computed with no model input. |
| 5 Adapter architecture | **Done.** `DecisionAdapter` protocol; authority layer imports no model. |
| 6 Jev advisory-only | **Done.** Can narrow, never widen; confidence convention documented. |
| 7 Guards | **Partial.** action, scope, side effects, approval, reversibility, model pinning, config integrity. Tool scope not done. |
| 9 Adversarial tests | **Done.** 31 tests covering all 9 named attacks + 2000 randomised cases. Mutation-tested. |
| 2 OBSERVE | **Partial.** Observation/EvidenceLog with observed-vs-labelled separation. No storage backend. |
| 3 REVIEW / outcomes | **Partial.** `confirm_outcome` requires a named confirmer; concordance kept separate. No workflow/UI. |
| 4 CALIBRATE | **Partial.** Readiness counts + export. Refuses to conclude without an operator-stated target. |
| 8 Auditability | **Partial.** `Decision.explain()` + append-only log in memory. Not persisted. |

## Not built

- **Persistence.** `EvidenceLog` is in-memory. Nothing survives a restart.
- **CLI / developer interface** (item 11). Library only.
- **Example agent** (item 13).
- **Tool-scope guard** (item 7) — filesystem/resource scope is done; tool allow-lists are not.
- **Threat-model document** (item 13) — the trust-anchor table below is a start, not the document.
- **Migration notes from the Jev Threshold Kit** (item 13).
- **Whop package structure** (item 13).
- **Kit audit remediation** (item 10) — findings below; no kit files changed yet.

## Trust anchors

Where every authority input comes from, and whether the acting agent can influence it.

| Value | Source | Agent-writable | Trusted |
|---|---|---|---|
| action ceiling, scope, reversibility | loaded policy document | no | yes |
| policy fingerprint | computed at load | no | yes |
| pinned model id | policy | no | yes |
| requested action + resources | the agent | **YES** | no — the subject, not an input |
| advice verdict / confidence | decision model | **YES** | no — can only narrow |
| observed resources after acting | execution environment | depends on host | **caller must supply from a trusted observer** |

The last row is the one integrators get wrong. `verify_effect` is only as good as the source of
`observed_resources`. If the agent reports what it touched, the check is theatre.

## Kit audit (item 10) — corrected findings

- **`guard.py`** — `check()` and `is_alias()` are correct working drift detection and are reused
  here. `pinned()` is dead: both branches return the input unchanged and it never warns despite
  its docstring, and it is not exported. **Remove it.**
- **Raw vs calibrated savings** — NOT a bug. `Profile.naive_cost_per_1k` uses raw confidence
  deliberately, with an explicit comment: comparing against the calibrated value would measure
  the fitted threshold against itself and report ~zero saving. `ThresholdChoice.naive` serves a
  different, internal comparison. Both are correct; the naming invites confusion. **Rename, do
  not change behaviour.**
- **Synthetic benchmark** — the README table is generated from `examples/make_dataset.py`, which
  states plainly that it is synthetic, and the README labels the section "the shipped example"
  and cites third-party sources for the distortion's shape. Disclosed, but not at the point of
  display. **Add one inline line marking the table synthetic.**
