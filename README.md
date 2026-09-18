# Hermes — architecture review and reconciliation

Design work for turning the Hermes orchestrator into a continuous autonomous work controller.
**Design only — nothing here is implemented, deployed, or installed.**

- [`docs/RECONCILIATION.md`](docs/RECONCILIATION.md) — **read first.** How three divergent
  implementations become one control plane: who is authoritative for each of fourteen
  responsibilities, what is retired, what is donated, the minimum database architecture, the
  migration, and the eleven questions that unblock the rest.
- [`docs/V2-CONTROLLER-ARCHITECTURE.md`](docs/V2-CONTROLLER-ARCHITECTURE.md) — the full V2
  design, **revised in place** after the reconciliation. `[REVISED]` marks corrected sections.
- [`docs/ASTRA-REVIEW-PACK.md`](docs/ASTRA-REVIEW-PACK.md) — eleven unresolved questions for
  GPT-6 Astra's final adversarial review.

Reviewed: `feat/control-plane` @ `877fb63` and `main` @ `615ee9f`. A third implementation
(`integrate/control-plane-dispatcher` @ `f2d64d9`) exists only on the Mac and was not readable.

## There are three orchestrators, not one

`main` and `feat/control-plane` are divergent siblings from merge base `b8dfd98` that have
**never been merged**. Neither contains a line of the other.

| | past merge base | ships |
| --- | --- | --- |
| `main` @ `615ee9f` | +14,251 / 58 files | `model_router.py`, `policy_gate.py`, `verifier.py`, `project_registry.py`, `skill_registry.py`, local-qwen worker, YAML task format |
| `feat/control-plane` @ `877fb63` | +18,962 / 43 files | `taskstore.py`, `dispatch_policy.py`, `routing.py`, `escalation.py`, `quota.py`, `worktree_lease.py`, `dispatcher.py` |
| `f2d64d9` (Mac only) | unknown | `durable_workflow.py`, `state/durable-workflows.db` |

So **two routers and two permission systems were already on GitHub** before durable-workflow
existed. Six of the nine duplications found have nothing to do with the branch in question.

## Four defects verified in the control plane

All in `dispatcher.py`, which is **not** frozen. The seven frozen digests verify OK.

- **B-1** — `recover()` can never reconcile a `failed` run. `_reconcile_journal_terminal`
  accepts `"failed"` and is unreachable. A crash between the journal commit and the ledger
  commit wedges the task at `assigned` with its worktree lease held, permanently. This is the
  most common terminal path in the system.
- **B-2** — `recover()` pass 2 skips every worker except `qwen-coder-local`.
- **B-3** — nothing automated promotes `pending → ready`. The sole promoter's only production
  caller is a frozen, human-typed verb; `dispatcher.py` references it zero times.
- **B-4** — HEALTHY is computed by `routing.recommend()`; execution is gated by
  `dispatch_policy.evaluate_authorization()`'s twelve checks; **nothing compares them.** A
  100%-undispatchable queue exits HEALTHY.

B-4 is the structural reason the Mac goes idle *silently* rather than *noisily*.

## The invariant

> IF APPROVED ELIGIBLE WORK EXISTS, THE MAC MUST NOT SILENTLY BECOME IDLE.

Attacked through five independent lenses. **All five broke it** — because of B-1, B-2, B-3 and
B-4 respectively, plus the unreadable third store. With those four closed, the strongest honest
claim is:

> The Mac may still stop. It may not stop **silently**. Every tick either dispatches, or
> records a named reason it did not, or fails to run at all — and the third case is caught by a
> watchdog that shares no code with the tick.

## Next task

Fix B-1 in `scripts/lib/dispatcher.py` only. One commit, three parts. No new module, no
scheduler, no LaunchAgent, no frozen file, no schema change. `RECONCILIATION.md` §18.
