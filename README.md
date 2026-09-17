# Hermes V2 — design review

Architecture/design review for turning `DRLXAI/hermes-orchestrator` into a continuous
autonomous work controller. **Design only — nothing here is implemented.**

- [`docs/V2-CONTROLLER-ARCHITECTURE.md`](docs/V2-CONTROLLER-ARCHITECTURE.md) — the full
  review: current-state assessment, remaining failure modes, V2 architecture, schema,
  state machine, scheduler, planner, routing, concurrency, ownership, stall detection,
  recovery, verification, approvals, security, persistence, status API, notifications,
  migration, acceptance tests, implementation order, what not to build, risks.
- [`docs/ASTRA-REVIEW-PACK.md`](docs/ASTRA-REVIEW-PACK.md) — ten unresolved design
  questions for GPT-6 Astra's final adversarial review.

Reviewed tree: `feat/control-plane` @ `877fb63`.

## The finding in one paragraph

`dispatcher.sh run` takes `--id`. **No code path in the repository selects a task.**
`taskstore.tasks(status="ready")` exists — its docstring even says *"Ordered the way a
scheduler wants them"* — and nothing calls it to dispatch. There is no loop, no daemon,
no LaunchAgent, and `jobs/*.yaml` are `enabled: false` by policy. The idle Mac is not a
bug, a race or a durability defect: **Hermes V1 is a permission system, and David is its
scheduler.** Every component answers *"may this run?"*; none asks *"what should run
now?"*, and none starts anything.

## What that means for V2

The brief asks for durability, recovery and ownership. Hermes already has unusually
strong versions of all three — authority is consumed rather than held, evidence is
stamped rather than asserted, escalation is fabrication-resistant, process identity
defeats pid reuse, and the worktree lease is acquired inside the assignment transaction.
What is missing is **drive**: a selector, a trigger, an activity model, and adapters for
every worker except local Qwen.

Roughly 1,270 lines close the primary complaint (CASES 1, 3, 4, 8 for the local lane).
About 4,750 lines close everything in the brief.

## The two rules the design is built on

1. **Everything new is an admission-side refusal or an operations-database row.** Nothing
   new becomes a ledger state, a transition edge, or a column on a frozen table. This is
   achievable end-to-end — including approval expiry, stall handling, holds, path
   ownership and roadmap provenance — with **zero edits to the seven files pinned by
   `config/control-plane-freeze.sha256`** through migration milestone M3.
2. **Tick, not daemon.** `docs/THREAT_MODEL.md` records that a long-lived cached
   `TaskStore` changes the ratified Boundary 0 premise. A launchd tick that opens the
   ledger, acts and exits is indistinguishable from what `control-plane.sh` already does
   on every invocation — and it removes "the daemon died" as a failure class entirely.

## The invariant that replaces "the task finished, so we stopped"

> A tick may never end with `dispatched = 0 AND running = 0 AND idle_reason = ''`.
> **The controller must always be able to name why it is not working.**

Seven named idle reasons, of which `IDLE_LEGITIMATE` (all approved work complete) and
`IDLE_ERROR` (eligible work, free capacity, no recorded denial) are distinct, notified
and tested.

## Note on the brief

The brief describes `scripts/durable-workflow.sh`, `scripts/lib/durable_workflow.py`,
`tests/test_durable_workflow.py` and `state/durable-workflows.db`, plus a V1 change that
added `heartbeat_at`. None of those exist; `heartbeat` appears zero times in the
repository. §0 of the review maps the brief's vocabulary onto the actual tree.
