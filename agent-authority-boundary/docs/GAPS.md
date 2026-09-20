# Gap assessment

Against the build proposal, checked at the point of writing. 66 tests across four suites, zero
dependencies.

## Built and verified

| Item | Status |
|---|---|
| 1 Persistent OBSERVE layer | **Done.** SQLite, append-only, hash-chained, tamper-evident. |
| 2 Human-confirmed outcome/review workflow | **Done.** Separate approval and outcome queues; both require a trusted named actor. |
| 3 Concordance vs correctness | **Done.** Different row kinds; approval never labels; export cannot carry agreement. |
| 4 Calibration dataset/export | **Done.** Trusted labels only, JSONL, consumable by jevcal or the kit. |
| 5 Target-dependent readiness | **Done.** Wilson interval against the operator's target. Four states incl. TARGET_REQUIRED. |
| 6 CLI | **Done.** 9 subcommands; console script installs. |
| 7 Example agent | **Done.** `examples/guarded_agent.py`, runnable. |
| 8 Tool-scope guard | **Done.** Undeclared tools are UNKNOWN, not permission. |
| 9 Threat model | **Done.** `docs/THREAT-MODEL.md`, including what it does *not* defend. |
| 10 Migration notes | **Done.** `docs/MIGRATION.md`. |
| 11 Kit corrections | **Done.** Applied; the kit's 44 tests still pass. |
| 12 Clean-install onboarding | **Done.** Fresh venv, `pip install`, README quickstart extracted verbatim and executed. |
| 13 Distribution structure | **Partial.** `pyproject.toml` + console script. Not published anywhere. |
| 14 Adversarial + regression pass | **Done.** Plus a five-invariant mutation battery. |

## Invariants, and the mutation that proves each is defended

Inverting any one of these in the source fails the suite:

| Invariant | Failing tests when inverted |
|---|---|
| Model output may never increase authority | 25 |
| UNKNOWN != FALSE | 6 |
| Storage does not confer trust | 4 |
| OBSERVATION != LABEL | 2 |
| DECLARED INTENT != OBSERVED EFFECT | 1 |

The last two are thin. More tests should bear on them directly rather than relying on one or
two cases each.

## Not built

- **Persistence hardening.** No WAL tuning, no concurrent-writer story, no retention/pruning, no
  migration path if the schema changes. One process at a time is the assumption.
- **No external anchoring.** The chain is tamper-evident against edits and deletes, not against a
  full rewrite by someone with database write access.
- **No cryptographic identity.** Trust is exact-string matching on a name in policy.
- **No async / no framework adapters.** Sync library; no LangChain/LlamaIndex/MCP integration.
- **Only one adapter.** The contract is model-independent but has been exercised against Jev only.
- **No Whop package structure** (item 13) — not started, and out of scope until the product is
  approved for sale.
- **`Decision` is not persisted as an object.** It is flattened into the evidence row; there is no
  `load_decision()` to rehydrate one for re-validation after a restart.

## Known weaknesses I would fix before charging anyone

1. **`verify_effect` depends entirely on the integrator.** Documented at length in the threat
   model, but a library cannot tell a git diff from an agent's JSON claiming to be one. The
   honest mitigation is a shipped observer for at least one common case (git worktree diff).
2. **Readiness uses one question at a time.** No multiple-comparisons handling. An operator
   checking twenty questions at 95% will see one spurious MEETS_TARGET by chance.
3. **No drift monitoring loop.** The kit's `check()` idea is promoted to a guard, but nothing
   periodically re-verifies that a model still matches its pin or that the error rate has held.
4. **The Jev adapter is written against documented behaviour, not a live endpoint.** The response
   shape was corrected against published interface documentation during this build; it has never
   been run against the real service.

## Claims audit

Every factual claim in the shipped docs was checked against behaviour. No performance,
calibration, accuracy or savings claim appears anywhere in this package. Test counts in this
document and the README match the suite output at the time of writing (66). The only numeric
claims are test counts and mutation-battery failure counts, both reproducible with
`./run_tests.sh` and the battery in the build log.
