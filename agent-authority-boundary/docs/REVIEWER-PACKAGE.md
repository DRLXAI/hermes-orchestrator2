# Independent security review — reviewer package

You are reviewing **Agent Authority Boundary**, a library that constrains what an AI agent is
permitted to do. Its single central claim is:

> **Model output may never increase authority.**

Your job is to break that claim, and the fourteen others below. This package was assembled by
the same author who wrote the code and the tests, which is precisely why an independent review
is needed: every adversarial test here reflects one person's imagination of how it fails.

**Do not soften a failing result.** A demonstrated break is the most valuable output of this
engagement. "Looks fine" is the least valuable.

---

## 1. Architecture summary

Three layers, in dependency order:

**PROTECT** (`policy.py`, `engine.py`, `scope.py`) — deterministic. A fingerprinted policy
document states, per action: a verdict ceiling, permitted resource globs, permitted tools,
reversibility, whether external side effects occur, whether human approval is required. Eight
guards run and the ceiling is the least permissive result. **No model participates.**

**OBSERVE** (`store.py`, `evidence.py`, `review.py`, `observers/git.py`, `effects.py`) — records
what a model advised and what the agent actually did, in an append-only hash-chained SQLite log.
Advice is always untrusted. Effects are trusted only when a configured observer reports them.

**CALIBRATE** (`calibration.py`) — refuses to conclude anything until genuinely labelled evidence
exists, and only against an operator-stated target.

Entry points: `decide()`, `revalidate()`, `verify_effect()`, `observe_git_effect()`,
`readiness()`, `portfolio_readiness()`, `check_drift()`.

## 2. Authority lattice — the mechanism the whole claim rests on

```
STOP (0)  <  ESCALATE (1)  <  REQUIRE_APPROVAL (2)  <  ALLOW (3)
```

Combination is `narrowest()` = `min` over this total order. Deterministic policy computes a
ceiling; everything else — model advice, confidence, observations, reviewer opinion — is folded
in with `min`. **There is no operation in the package that moves a verdict up.**

An adapter's maximum expressible verdict is `ALLOW`, which means "no objection", not "I grant
this". `UNKNOWN_VERDICT = ESCALATE`: missing information is never `ALLOW` and never `False`.

**Attack this first.** If you find any path that produces a verdict exceeding the ceiling
computed without the model, the product's thesis is false.

## 3. Trusted / untrusted boundaries

| Value | Source | Agent-writable | Trusted |
|---|---|---|---|
| action ceiling, scope, tools, reversibility | policy document | no (assumed) | yes |
| policy fingerprint | computed at load | no | yes |
| trusted source list | policy document | no (assumed) | yes |
| pinned model id | policy document | no | yes |
| requested action / resources / tools | the agent | **yes** | no — the subject, not an input |
| model advice, confidence | decision model | **yes** | no — stored `untrusted`, can only narrow |
| agent's account of its own effect | the agent | **yes** | no — yields UNKNOWN |
| git observation | `GitEffectObserver` | depends on deployment | yes if the observer is trusted |
| approval / ground truth | named actor in `trusted_sources` | no | yes |

## 4. Policy fingerprint

`policy.load()` canonicalises the document (sorted keys, tight separators) and SHA-256s it.
Every `Decision` carries the fingerprint it was made under. `revalidate()` returns `STOP` if the
current policy's fingerprint differs. `trusted_sources` is *inside* the fingerprinted document,
so widening trust invalidates in-flight decisions.

## 5. Evidence chain

```
trusted policy → fingerprint → authority ceiling → advisory observation → final narrowed verdict
→ approval if required → independently observed effect → observed outcome → confirmed ground truth
```

Append-only. Each row hashes `(prev_hash, kind, correlation_id, source, trust, payload)`, so an
edit or deletion breaks every subsequent hash; `verify_chain()` detects it. Trust is looked up
from policy at write time and **never** inferred from payload or asserted by the caller. A
rejected write (e.g. an agent self-confirming an outcome) is appended as untrusted *and then*
refused, so the attempt is preserved.

**This is tamper-evidence, not tamper-proofing.** Anyone who can rewrite the whole database
produces a chain that verifies.

## 6. GitEffectObserver

Reads the repository with git, given a repo path and baseline commit from **configuration, not
the agent**. Establishes: repository identity (root commit), baseline reachability,
added/modified/deleted/renamed/type-changed paths, symlinks, submodules, working-tree dirt, head
and tree identity.

Deliberate rules worth attacking:
- a **rename counts both ends** — source and destination are both "touched";
- a **symlink is refused wherever it sits** (a link is a scope-escape primitive);
- a **submodule/gitlink is refused** (another repository grafted in);
- **uncommitted changes are effects**; `allow_dirty=True` still scope-checks the dirty paths;
- unreachable baseline, identity mismatch, missing repo, unusable git binary → **UNKNOWN**, never
  "clean".

Observations are clamped to the decision's verdict, so evidence arriving after the fact cannot
retroactively authorise anything.

## 7. Jev adapter

Advisory only; makes no network calls. `noul` returns a bare probability with no confidence, so
the adapter derives `|p − 0.5| × 2` and labels it `confidence_source="derived"`. `choice`/`score`
carry a server confidence, which is used as-is — **never** `max(probabilities)`. A missing
confidence is `unavailable` → `ESCALATE`, never invented.

**Not validated against the live service** — see `validation/JEV-VALIDATION.md`. Known finding:
the *kit's* client (`jevkit/client.py:150,159`) substitutes `0.0` for a missing confidence. The
adapter does not.

## 8. Calibration / readiness rules

No universal sample threshold. The operator states a `Target(max_error_rate, confidence)`; a
Wilson interval on the observed error rate over **trusted labels only** yields
`TARGET_REQUIRED` (no target set — unanswerable), `INSUFFICIENT`, `MEETS_TARGET` or
`FAILS_TARGET`. Multiple questions: `portfolio_readiness` refuses an aggregate claim by default
(`all_meet_target` returns `None`, not `False`); `correction="bonferroni"` makes it available.

## 9. Adversarial tests already written

```
tests/test_adversarial.py    31   authority lattice, forged confidence, model identity, approval bypass
tests/test_git_observer.py   29   real repositories: scope escape, renames, symlinks, dirty trees, baselines
tests/test_invariants.py     21   observation≠label, declared≠observed, multiple comparisons, drift
tests/test_jev_adapter.py    10   real response shapes, hostile values
tests/test_onboarding.py      3   README quickstart, executed verbatim
tests/test_persistence.py    22   trust model, chain tamper-evidence, review separation
                            ---
                            116
```

Run: `./run_tests.sh`. Zero dependencies; `git` required for the observer suite.

Reproduce the mutation battery yourself: `python3 validation/mutation_battery.py`. It inverts
each invariant in the source, runs every suite, and restores the file.

| Invariant | Tests that fail when inverted |
|---|---|
| model output may never increase authority | 28 |
| UNKNOWN != FALSE | 13 |
| storage does not confer trust | 8 |
| unavailable evidence is never compliant | 6 |
| DECLARED INTENT != OBSERVED EFFECT | 4 |
| OBSERVATION != LABEL | 3 |
| both ends of a rename are observed | 2 |
| no aggregate calibration claim without a correction | **1** |

**The bottom three are thin, and the last is a single assertion.** Weak mutation scores are
exactly where the tests are least likely to catch you — start there.

## 10. Known gaps

`docs/GAPS.md` is the maintained list. Highlights: one observer only (git); single-process store
with no concurrency story; no signing or external anchoring; trust is exact-string name matching;
Jev adapter never run live; no prior independent review.

## 11. Explicit attack objectives

Demonstrate any of these and the result is a finding, regardless of how contrived the setup
looks — note the assumptions you needed.

1. **Model output increasing authority.** Any path where advice yields a verdict above the
   ceiling computed without it.
2. **Scope escape.** A resource changed outside the authorised globs that `assess_git_effect`
   or `verify_effect` reports as compliant.
3. **Symlink / path traversal escape.** Reaching outside the repository via a link, junction,
   hardlink, `..`, case-folding, or Unicode normalisation.
4. **Trusted-policy substitution.** Getting a decision executed against a policy other than the
   one it was made under, or defeating the fingerprint.
5. **Forged evidence becoming trusted.** Any write that lands `trust='trusted'` without the
   source being in `trusted_sources`.
6. **Agent self-report becoming observed effect.** Getting `authorised_effect()` to return the
   agent's own claim.
7. **UNKNOWN becoming FALSE/safe.** Missing policy, missing scope, missing confidence, missing
   observation or missing ground truth read as permission or as a clean result.
8. **Observation becoming a calibration label.** Getting an unlabelled or untrusted row into
   `labelled()` or `export_dataset()`.
9. **Operator agreement becoming correctness.** Getting `operator_agreed` to influence readiness,
   drift or an export.
10. **Post-decision evidence retroactively granting authority.** An observation or outcome that
    raises a verdict after the fact.
11. **Git baseline / repository substitution.** Making the observer read a different repository
    or baseline than configured, or passing an identity mismatch.
12. **Rename/move bypass.** A rename where only one end is checked.
13. **Dirty-tree bypass.** Uncommitted effects that escape observation, including via
    `allow_dirty=True`.
14. **Jev confidence / provenance confusion.** A derived confidence presented as reported, a
    fabricated number presented as Jev-supplied, or provenance lost in the chain.
15. **Aggregate calibration claim without justified correction.** Getting
    `aggregate_claim_available` true, or an "all questions meet target" claim, without an
    explicit correction.

## 12. How to report

For each finding: the objective number, a minimal reproduction, the assumptions required, and
the impact if the assumption holds. Assumption-dependent findings are still findings — say which
assumption. If an objective resists attack, say what you tried; that is evidence too.
