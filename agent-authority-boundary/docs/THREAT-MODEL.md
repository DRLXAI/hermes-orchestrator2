# Threat model

## What this defends

An AI agent with the ability to act — edit files, call tools, cause external effects — where a
decision model participates in deciding what happens. The asset is **authority**: the set of
things the agent may do. The threat is authority being widened by anything other than a human
changing policy.

## What it assumes (and therefore does not defend)

These are the load-bearing assumptions. If one is false, the boundary is decoration.

1. **The policy document is out of the agent's reach.** Policy is the only thing that grants
   authority. An agent that can edit the policy file has already won. Store it where the agent
   cannot write, and treat its fingerprint as the thing you actually trust.
2. **The evidence store is out of the agent's reach.** Hash chaining makes tampering *evident*,
   not impossible. Anyone who can write the database can rewrite the whole chain. `verify_chain()`
   catches edits and deletions by a process that does not rebuild every subsequent hash.
3. **`observed_resources` comes from something other than the agent.** This is the assumption
   integrators break most often. See below.
4. **The process calling `decide()` is trusted.** This library is a boundary inside your program,
   not a sandbox. It does not contain a hostile process; it constrains a cooperative one and
   detects an uncooperative one.
5. **Trusted source names are unique and controlled.** `trusted_sources` is matched by exact
   string. If an agent can register a source name, it can mint trust.

## Trust anchors

| Value | Source | Agent-writable | Trusted |
|---|---|---|---|
| action ceiling, scope, tools, reversibility | policy document | no (assumption 1) | yes |
| policy fingerprint | computed at load | no | yes |
| trusted source list | policy document | no (assumption 1) | yes |
| pinned model id | policy document | no | yes |
| requested action / resources / tools | the agent | **yes** | no — the subject, not an input |
| model advice, confidence | decision model | **yes** | no — recorded `untrusted`, can only narrow |
| agent's account of its own effect | the agent | **yes** | no — yields UNKNOWN |
| observed effect from a trusted observer | your observer | depends (assumption 3) | yes |
| approval | named approver in `trusted_sources` | no | yes |
| ground truth | named confirmer in `trusted_sources` | no | yes |

## The attack that actually works

**Wiring the agent's own report in as the observer.** Everything else in this document is
enforced by code. This one is enforced by the integrator's judgment.

**Partially closed for git.** `authority.observers.git.GitEffectObserver` reads the repository
itself — added, modified, deleted, renamed (both ends), type-changed paths, symlinks,
submodules, working-tree dirt, and the resulting tree identity. It takes the repo path and
baseline commit from YOUR configuration, never from the agent, and refuses outright on a
repository identity mismatch or an unreachable baseline. An agent's own declaration is admitted
only as a claim to be contradicted by that evidence.

**Still open everywhere else.** HTTP calls, database writes, emails, payments and shell side
effects have no observer. For those, the library cannot tell the difference between a real
observation and an agent's JSON claiming to be one.

`store.authorised_effect()` returns `None` when only untrusted observers reported — deliberately
UNKNOWN rather than empty — so a correct integration fails closed. But if you register the agent
in `trusted_sources`, or call `record_effect(observer="fs-observer")` with data the agent
supplied, the boundary reports success while authorising nothing.

An observer must be something the agent cannot author: a git diff computed by the host, a
filesystem audit log, an eBPF probe, a container diff. Not a field in the agent's response.

## Residual risks, stated plainly

- **Tamper-evidence, not tamper-proofing.** No external anchoring (no notarisation, no append-only
  filesystem, no remote witness). A full-database rewrite verifies cleanly.
- **No cryptographic signing.** Rows are chained, not signed. Row provenance rests on the writing
  process being honest about `source`.
- **Trust is name-based.** No key material, no identity verification. `approver="david"` is
  trusted because the string matches policy.
- **No replay protection on correlation ids.** A caller reusing a correlation id appends to an
  existing chain.
- **Time is not trusted.** `recorded_at` is local clock, unverified, and not used in any decision.
- **The library does not execute anything.** It returns a verdict. Whether your code honours
  `executable` is outside this boundary entirely.

## Invariants under test

| Invariant | Enforced by | Tested in |
|---|---|---|
| Model output may never increase authority | `narrowest` = `min` over a total order | `test_adversarial.py` (31 tests; mutation-tested) |
| UNKNOWN != FALSE | `UNKNOWN_VERDICT = ESCALATE`; `ground_truth=None` | both suites |
| Operator agreement != correctness | separate row kinds; approval never labels | `test_persistence.py` |
| Observation != label | `labelled()` requires `trust='trusted'` | `test_persistence.py` |
| Confidence != authority | confidence never reaches a ceiling computation | `test_adversarial.py` |
| Declared intent != observed effect | `verify_effect`, `authorised_effect`, the git observer | `test_git_observer.py` (29), `test_invariants.py` |
| Unavailable evidence is never compliant | `assess_git_effect` returns ESCALATE | `test_git_observer.py` |
