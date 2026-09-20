# Gap assessment

116 tests across six suites, zero dependencies. Checked at the point of writing.

## Built and verified

| Area | Status |
|---|---|
| PROTECT — deterministic boundary | Done. 8 guards; ceiling computed with no model input. |
| Git effect observer | **Done.** Reads the repository directly; 29 tests against real repos. |
| Evidence chain + trust model | Done. Append-only, hash-chained, trust from fingerprinted policy. |
| Review workflow | Done. Approval and outcome are separate queues with separate meanings. |
| Target-dependent readiness | Done. Wilson interval against the operator's stated target. |
| Multiple comparisons | Done. No aggregate claim without an explicit correction. |
| Drift monitoring | Done, minimal. Model-vs-pin and early-vs-late error rate. No scheduler. |
| Jev adapter | Done, against the documented interface. Never run live. |
| CLI, example, packaging | Done. Console script; clean-install verified. |
| Kit corrections | Done. Kit's 44 tests still pass. |

## Invariants and their mutation scores

Inverting any of these in the source fails the suite:

| Invariant | Failing tests |
|---|---|
| Model output may never increase authority | 28 |
| UNKNOWN != FALSE | 13 |
| Storage does not confer trust | 8 |
| Unavailable evidence is never compliant | 6 |
| DECLARED INTENT != OBSERVED EFFECT | 4 |
| OBSERVATION != LABEL | 3 |
| Both ends of a rename are observed | 2 |

The two areas flagged as thin in the previous assessment (2 and 1) are now 3 and 4. They remain
the weakest rows and are where I would add coverage next.

## The multiple-comparison choice, and why

Checking one question at 95% means a 5% chance of a spurious MEETS_TARGET; check twenty and
expect roughly one by luck. The options were to invent a correction or to make the limitation
visible. **Both were taken, explicitly:** `portfolio_readiness` refuses to emit an aggregate
claim by default — `aggregate_claim_available` is `False` and `all_meet_target` returns `None`,
not `False`, because the question is unanswerable rather than answered negatively. Requesting
`correction="bonferroni"` tightens each test so the family-wise error rate matches the stated
confidence, and then the claim becomes available.

Bonferroni was chosen over sharper procedures (Holm, Benjamini–Hochberg) because it is
assumption-free about correlation between questions and conservative in the direction that
matters: the cost of a false "we are calibrated" is an agent acting on a threshold that is not
there. A test proves the correction can actually withdraw a claim that survived uncorrected —
20 questions × 100 perfect labels passes at 95% and fails once corrected.

## Not built

- **One observer only.** Git repositories are verifiable. HTTP calls, database writes, emails,
  payments, shell side effects — none are. For those, `verify_effect` is still only as good as
  whatever the integrator wires in.
- **Persistence hardening.** Single-process assumption. No WAL tuning, no concurrent writers, no
  retention or pruning, no schema migration path.
- **No external anchoring or signing.** The chain is tamper-evident against edits and deletions,
  not against a full rewrite by anyone with database write access.
- **Trust is name-based.** Exact string matching against policy. No key material, no identity
  verification.
- **No async, no framework adapters.** Sync library; no LangChain / LlamaIndex / MCP integration.
- **Whop packaging and sales copy.** Deliberately not started.

## V1 commercial-readiness audit

| Question | Answer |
|---|---|
| Can another developer install it without our environment? | **Yes.** Verified: fresh venv, `pip install`, console script present, README quickstart extracted verbatim and executed against a real repository. |
| Can they protect an agent before having calibration labels? | **Yes.** PROTECT is deterministic and needs no model and no data. |
| Can they independently verify at least one real class of effect? | **Yes, for git repositories.** 29 tests covering out-of-scope writes/deletes/creates, renames at both ends, symlinks, submodules, dirty trees, baseline mismatch, repo identity mismatch, and agent declarations contradicting git. Nothing else is covered. |
| Can they collect evidence without confusing observations with labels? | **Yes.** Structurally: a label requires a trusted named confirmer; mutation-tested. |
| Can they review and confirm outcomes? | **Yes.** Separate approval and outcome queues, in library and CLI. |
| Can they export legitimate calibration data? | **Yes.** Trusted labels only, JSONL, consumable by jevcal or the kit. Concordance cannot leak in. |
| Can they use the Jev adapter without invented confidence? | **Yes.** Reported confidence for choice/score, derived and labelled `derived` for noul, and `unavailable` escalates rather than inventing a number. |
| Does any model output have a path to increasing authority? | **No known path.** Combination is `min` over a total order; no operation raises a verdict; an adapter's ceiling is ALLOW ("no objection"); observations are clamped to the decision's verdict. Verified by 28 failing tests when the lattice is inverted. This is a claim about this library, not about a system that embeds it. |

### What still prevents charging for V1

1. **The Jev adapter has never touched the live service.** It is correct against published
   interface documentation. That is not the same as working.
2. **One observer.** If a buyer's agent does anything other than edit a git repository, the
   central claim does not apply to them.
3. **No independent security review.** Every adversarial test here was written by the same
   author as the code being tested.
4. **No published package, no versioning or support commitment.** Not on PyPI; no changelog,
   deprecation policy, or issue tracker.
5. **Operational unknowns.** Single-process store, no concurrency or retention story, no
   guidance on database placement beyond "somewhere the agent cannot write".
6. **No real-world usage.** Zero external users, zero production hours, no evidence any of this
   survives contact with a buyer's actual agent.
