# Hermes — Reconciliation Review

Determining how three divergent orchestrator implementations become one control plane.

**Architecture only. Nothing implemented, nothing deployed, no LaunchAgent installed, no
frozen code touched, no production state modified.**

---

## 1. Systems inspected

| # | System | Location | Readable? | Size past merge base |
| --- | --- | --- | --- | --- |
| 1 | `main` @ `615ee9f` | GitHub, cloned to `/home/user/hermes-main` | **yes** | +14,251 lines / 58 files |
| 2 | `feat/control-plane` @ `877fb63` | GitHub, cloned to `/home/user/hermes-orchestrator` | **yes** | +18,962 lines / 43 files |
| 3 | `integrate/control-plane-dispatcher` @ `f2d64d9` | **David's Mac only** | **NO** | unknown |

Method: 8 subsystem characterizations reading actual code and tests, 17 responsibility
dimensions each independently adjudicated then adversarially refuted by a second reviewer,
5 invariant attacks, 1 completeness critic. 48 agents, 0 errors. Every load-bearing finding
below I then re-verified myself against the source; line numbers are cited so you can too.

### 1.1 The relationship, established by git rather than assumption

```
                        b8dfd98  (merge base)
                       /        \
     main @ 615ee9f  ●            ●  feat/control-plane @ 877fb63
     +14,251 lines                   +18,962 lines
     model_router.py                 taskstore.py
     policy_gate.py                  dispatch_policy.py
     verifier.py                     routing.py / escalation.py / quota.py
     project_registry.py             worktree_lease.py
     skill_registry.py               dispatcher.py
     local-qwen worker + YAML        state/control-plane.db + state/dispatcher.db
```

`main` is **not** an ancestor of `feat/control-plane`. Neither contains a line of the other.
They have never been merged. `git merge-base` = `b8dfd98`.

### 1.2 What I could not read, and exactly what I need

`f2d64d9` is not on GitHub:

```
git ls-remote --heads origin  →  only feat/control-plane, main
git fetch origin f2d64d9      →  fatal: couldn't find remote ref f2d64d9
```

Every decision below is marked **SETTLED** (correct regardless of what `durable_workflow.py`
contains) or **CONDITIONAL** (with the one fact that settles it, in §13).

---

## 2. Correcting my previous review

Three claims in `V2-CONTROLLER-ARCHITECTURE.md` were wrong. All three made the system look
healthier than it is.

**Wrong 1 — "the brief's files don't exist."** They exist; they are on the Mac and were never
pushed. My §0 correction was itself incorrect and is now rewritten.

**Wrong 2 — "there is one permission system and one router."** There are two of each, both on
GitHub. `main` ships `policy_gate.py` (an exit-code permit contract) and `model_router.py`
(a deterministic 8-component model selector). I reviewed the branch I was pointed at and did
not check whether the default branch had diverged. That is the exact duplication class this
reconciliation exists to prevent, and it was already present before durable-workflow.

**Wrong 3 — "reboot reconciliation is solved; V2 supplies the trigger."** This was the single
most consequential error. `recover()` is not a correct mechanism awaiting a trigger. It is
**blind to the most common terminal state and scoped to one worker id.** See §3.

---

## 3. Three verified defects in the control plane

Found by the reconciliation, re-verified by me directly against
`/home/user/hermes-orchestrator` @ `877fb63`. None of these depends on the unreadable branch.
All seven freeze digests verify OK on disk, so none of this is frozen-file damage — it is all
in `dispatcher.py`, which is **not** frozen.

### B-1 — `recover()` can never reconcile a `failed` run. The repair code is unreachable.

```python
# dispatcher.py:1883-1886   recover(), pass 1
if (
    run["state"] in {"succeeded", "cancelled"}        # ← "failed" is absent
    and self.store.task(run["task_id"]).status == "assigned"
):
    self._reconcile_journal_terminal(run)

# dispatcher.py:1665-1667   finalize()'s re-entry guard — same omission
if run["state"] not in {"assigned", "running"}:
    if run["state"] in {"succeeded", "cancelled"}:    # ← again
        self._reconcile_journal_terminal(run)

# dispatcher.py:1587-1588   the target explicitly accepts "failed"
if expected not in {"succeeded", "failed", "cancelled"}:
    raise DispatcherError(...)
```

`_reconcile_journal_terminal` handles `failed` correctly, including the right agreement
predicate. **Nothing can call it with one.**

The failure sequence, which traverses the most common path in the system:

1. Worker exits nonzero — a failing test, a failed verification, a runner error.
2. `decide_terminal` opens `BEGIN IMMEDIATE` on **`state/dispatcher.db`** and commits
   `state='failed'`.
3. Crash, power loss or SIGKILL before the very next statement, which would open a
   **separate** transaction on a **separate** file (`state/control-plane.db`).
4. Durable result: journal says `failed`, ledger says `assigned`, worktree lease held by a
   dead pid.
5. `dispatcher.sh recover` — the documented remedy — returns `recovered 0`, forever:
   - pass 1 filters `{succeeded, cancelled}`, so the row is skipped;
   - pass 2 acts only `if authorization["nonce"] not in known` (dispatcher.py:1893), and the
     failed row *is* in `known`, so it is skipped;
   - pass 3 iterates `journal.active()` = `WHERE state IN ('assigned','running')`
     (dispatcher.py:839), so a terminal row is excluded.
6. `taskstore._set_status` releases the worktree lease only on **departure from `assigned`**,
   which now cannot happen. The worktree is locked against every future task.

This is a hand-rolled two-phase commit with no coordinator, across two WAL files, traversed on
every terminal outcome. `docs/DISPATCHER.md:52` claims recovery "reconciles their only two
cross-database gaps." There is a third, and it is the common one.

### B-2 — `recover()` is scoped to one worker id

```python
LOCAL_WORKER = "qwen-coder-local"                       # dispatcher.py:37

for task in self.store.tasks(status="assigned"):        # recover(), pass 2
    if task.assigned_worker != LOCAL_WORKER:
        continue                                        # dispatcher.py:1890-1891
```

`dispatch_policy.assign` accepts **any** worker id and already has two independent production
callers (`control-plane.py:215`, `dispatcher.py:1433`). So the moment anything assigns to
`qwen3-local` — free, local, `enabled=true`, and the natural pick for any task needing
`analysis` or `review`, or for any task after one failure raises `escalation_floor` — that
assignment is outside **every** recovery sweep in the system, by construction.

A partial migration necessarily widens dispatch before it widens recovery, so this gap opens
**by default rather than by mistake.**

### B-3 — nothing automated promotes `pending → ready`

```python
"INSERT INTO tasks(... status ...) VALUES (?, ?, ?, ?, 'pending', ...)"   # taskstore.py:937
```

`'pending'` is a literal, not a parameter. The sole promoter is `TaskStore.evaluate()`
(taskstore.py:2161). Its production callers, in full:

```
$ grep -rn "\.evaluate()" scripts/
scripts/lib/control-plane.py:138        # command_plan — a verb a human types

$ grep -c "evaluate" scripts/lib/dispatcher.py
0
```

Any scheduler built on `dispatcher.sh run` / `recover` would loop over
`store.tasks(status="ready")` and find an **empty set forever**, because the rows are still
`pending` and nothing the dispatcher touches can move them.

My V2 tick pseudocode did call `store.evaluate()`, so the design was accidentally right — but
I described it as "existing, public API" and treated promotion as a solved problem. It is one
frozen, human-typed verb away from being unreachable, and `control-plane.py` is frozen, so the
tick **cannot** be added there as a subcommand.

### B-4 (design, not a line) — the health predicate is weaker than the execution predicate

```python
findings = counts["failed"] + counts["blocked"] + counts["awaiting_approval"]   # control-plane.py:127
if findings: return FINDINGS
return HEALTHY
```

`ready` is not a term. `assigned` is not a term. So:

- a task wedged `assigned` by B-1 exits **HEALTHY**;
- a `ready` queue that is 100% undispatchable exits **HEALTHY**.

And `plan` (control-plane.py:145-155) escalates only on `routing.recommend()`'s
`needs_human` — but `recommend()` consults neither quota, nor `metered`, nor which adapters
are actually wired. So `plan` cheerfully certifies a route that cannot execute.

> **Health is computed by `routing.recommend()`. Execution is gated by
> `dispatch_policy.evaluate_authorization()`'s twelve checks. Nothing in the system ever
> compares the two answers.**

That predicate split is the structural reason the Mac goes idle *silently* rather than
*noisily*. It is more important than any single bug above, and closing it is the actual
answer to David's invariant (§15).

---

## 4. Q1 — What does f2d64d9 solve that the control plane does not?

I cannot read it, so I will not characterize its code. What I *can* do is verify, against the
readable branch, whether the gaps it claims to fill are real. All four are:

| f2d64d9 claims | Control plane, verified |
| --- | --- |
| continuous run mode | **absent.** No loop, no daemon, no launchd plist, no cron. `dispatcher.sh run` takes `--id`. |
| `heartbeat_at` | **absent.** `grep -rn heartbeat` over the whole checkout returns nothing. |
| safer dead/stale worker recovery | **partially present and defective** — see B-1, B-2. |
| automatic advancement of defined steps | **absent.** `evaluate()` unwired (B-3); no selector. |
| approval blocking | **present and stronger** — `approval_required`/`approval_granted` + `_ledger_objection` re-check inside `BEGIN IMMEDIATE`. |
| `runner_pid` | **present and stronger** — pid *plus* process-birth identity, 4-way classified. |

So f2d64d9 is aimed at genuine holes. Four of its six claims target things the control plane
provably does not have. **Its diagnosis is right.** Whether its *mechanisms* are safe is
exactly what §13 settles — and two of its claims (approval, runner_pid) overlap machinery the
control plane already does better, which is where the duplication risk lives.

## 5. Q2 — What does the control plane solve that durable-workflow does not?

Thirty commits of adversarially-reviewed authority machinery that a one-commit workflow engine
will not plausibly have reproduced:

- **Single-use authorization.** A nonce bound to (task, worker, attempt, model, worktree),
  minted by `authorize`, spent by `assign` under a conditional update inside one
  `BEGIN IMMEDIATE`. Six concurrent processes end at `assigned, spent: 1`.
- **Consume-time revalidation.** Every condition that justified a grant is re-derived when it
  is spent. Clearing quota, disabling a worker or renaming a model invalidates an outstanding
  grant rather than merely denying the next one.
- **Evidence stamped from the assignment**, not chosen by the caller; exit codes and output
  paths come from the dispatcher's own process journal.
- **Escalation attribution.** A failure moves `escalation_floor` only if it carries the tier of
  a genuinely spent authorization; identical failures count once; transient classes never move
  it; failure text is data and never policy input.
- **Quota that denies by default** — only an explicit, unexpired `AVAILABLE` record permits.
- **Worktree leases keyed by `(st_dev, st_ino)`**, acquired *inside* the assignment
  transaction, breakable only when the holder is dead **and** the lease is aged.
- **Process-birth identity** — `matched / not matched / indeterminate`, where indeterminate
  never acts. This is the correct pid-reuse defense and it is already built.
- **An audit trail reconciled against the attempt counter**, so a hand-edited ledger is
  refused rather than believed.

Anything that replaces these loses them. **Nothing in the reconciliation proposes to.**

## 6. Q3 — Where they duplicate responsibility

Only one of these three overlaps involves the unreadable branch. Two are already on GitHub.

| Responsibility | Claimants | Class |
| --- | --- | --- |
| **routing** | `main/model_router.py` vs `fcp/routing.py` | **incompatible semantics** — model-profile selection vs worker selection; main's unit has no worker, no pid, no liveness, no identity |
| **approval / permission** | `main/policy_gate.py` vs `fcp` approval columns + `dispatch_policy` | **incompatible semantics** — an exit-code permit vs a durable, re-checked, consumed grant |
| **verification** | `main/verifier.py` vs `fcp/verifier_runner.py` + evidence rules | **incompatible semantics** — a structured verdict object with no attempt binding vs an attempt-scoped, digest-pinned, out-of-target process |
| **task definition / identity** | `main` YAML files vs `fcp` ledger rows | **harmful duplication** — a file path is not a durable identity |
| **escalation** | `main/model_router.request_escalation()` vs `fcp/escalation.py` | **harmful duplication** — main's is advisory, unpersisted, and read by nothing |
| **terminal outcome** | `dispatcher.db.dispatch_runs.state` vs `control-plane.db.tasks.status` | **harmful duplication** — this *is* B-1 |
| **continuous execution** | nothing on GitHub vs f2d64d9's `run()` | **CONDITIONAL** |
| **liveness/heartbeat** | nothing on GitHub vs f2d64d9's `heartbeat_at` | **CONDITIONAL** |
| **recovery** | `fcp/recover()` vs f2d64d9's stale-worker sweep | **CONDITIONAL** |

The uncomfortable summary: **of the nine duplications, six already exist on GitHub and have
nothing to do with the branch David was worried about.**

## 7. Q4 — Where the state models conflict

Three genuine conflicts, in descending severity.

**C-1 — two databases decide one terminal outcome.** `dispatch_runs.state` commits first and
`tasks.status` is reconciled afterwards across a non-atomic boundary. `dispatcher.py:1585`
calls the journal decision "authoritative." It is not: the ledger is. This inversion is the
mechanism of B-1.

**C-2 — `main` has no state model at all.** A YAML file with `enabled: false` is a *shadow*
state with no transitions, no attempt count and no audit. It cannot be reconciled with an
8-state transition table; it can only be replaced.

**C-3 — a third state machine, if f2d64d9 has one.** If `state/durable-workflows.db` carries a
status/step column that is *read as a precondition to advance*, then the one component with a
clock gates on columns the authority cannot write, and the one component that can see the
authority has no clock. B-4 guarantees the divergence is never alarmed, because `ready` and
`assigned` are both zero-findings.

---

## 8. Q5 — Authoritative implementation per responsibility

Every row was independently adjudicated and then adversarially attacked. "Refuted" below means
the reviewer found a substantive defect in the *packaging* of the recommendation — in all 17
cases the authority assignment itself survived; what changed was the adapter or the ordering.

| Responsibility | Authoritative | Retire | Status |
| --- | --- | --- | --- |
| **task identity** | `tasks.id` in `control-plane.db`, canonicalized by `normalize_identifier` | main's YAML-path-as-identity; `execute-local-qwen-task.py` | SETTLED |
| **task state** | `tasks.status`, sole writer `taskstore.py` | `dispatch_runs.state` as an independent terminal authority | SETTLED |
| **approval** | `tasks.approval_required` / `approval_granted`, written only by `grant_approval` + `add_task`'s undeclinable forcing | `policy_gate.py`'s exit-code permit contract, as an authority | SETTLED |
| **dependencies** | `task_dependencies` + `_dependency_check` | `repo_audit.py`'s fabricated `dependency_ids` | SETTLED |
| **dispatch (decide)** | `dispatch_policy.assign` — unchanged, sole decider | main's `--authorize` CLI flag | SETTLED |
| **dispatch (start)** | `dispatcher.py` `run_task` — sole starter, **unfrozen** | `run-local-qwen-task.sh` as a second start path | SETTLED |
| **worker identity** | process-birth identity in `dispatch_runs`, joined by `authorization_nonce` | any bare-pid reclaim | CONDITIONAL |
| **heartbeat** | `dispatch_runs.heartbeat_at`, emitted by `dispatch_runner.py` | a third DB for liveness | CONDITIONAL |
| **stall detection** | `dispatcher.py` + `dispatch_runner.py`, unfrozen, two layers | main's `COMMAND_TIMEOUT_SECONDS = None` | SETTLED |
| **recovery** | `dispatcher.recover()`, extended in place, single recovery agent | a second reclaim sweep anywhere | CONDITIONAL |
| **verification** | `verifier_runner.py` + `completion_failure`, gated by frozen evidence rules | `worker.py`'s `completion_markers` | SETTLED |
| **ownership / locking** | `worktree_lease.py` + `bind_worktree`, reachable only via `assign` | any second lease namespace | CONDITIONAL |
| **routing** | `routing.py` `recommend()` | `model_router.py` **in whole, as a routing engine** | SETTLED |
| **escalation** | `escalation.py` + `record_failure` + `_escalation_check` | `request_escalation()`, `EscalationRecommendation` | SETTLED |
| **audit history** | `task_events` + `task_evidence` + `task_authorizations` | `dispatcher.db` as a second audit trail — demoted to telemetry sidecar | SETTLED |

**The seam is already in the right place.** All seven frozen files are *decide*-side.
`dispatcher.py`, `dispatch_runner.py`, `dispatch_exec.py` and `verifier_runner.py` are
*start*-side and unfrozen. So the start half can absorb an execution engine, a heartbeat, a
stall reaper and a continuous loop **without reopening the control-plane review.** Every
recommendation above respects that line.

### Donations — mechanisms worth lifting out before retirement

From `main`, four things are genuinely better than their fcp counterparts:

1. **`TASK_TYPES` + `COMPLEXITY_TIER`** (`model_router.py:140-178`) — 24 task types with a
   reasoning/coding family split. Becomes `config/task-classes.json`, the only thing permitted
   to fill `required_capabilities`. **This is what actually enforces local-first**, and it is
   the mechanism my V2 §9 invented from scratch without knowing it already existed.
2. **`DETERMINISTIC_TASK_TYPES`** (`model_router.py:171-178`) — lint/typecheck/test/build/format
   should never reach a model at all. fcp has no such notion, so today a failing lint task can
   climb the escalation ladder toward Claude. This is a real, cheap safety win.
3. **`policy_gate._confine_write_roots`** (`policy_gate.py:180-203`) — resolve-before-compare
   path confinement. Becomes an admission-side path-claim predicate. Retired as an *authority*,
   kept as an *input*.
4. **`verifier.py`'s structured verdict** (`Check`, `VerificationResult`, the
   failure-vs-warning severity split) — becomes the **stdout contract** of fcp's already-trusted
   verifier process, replacing `"promoted-target verification exited 1"` with named failing
   checks.

And `project_registry.resolve()`'s closed-set project identity replaces `tasks.project` being
free text validated by nothing.

**Retired outright: ~14,000 of main's 14,251 lines**, as orchestration. The DataWise adapters
and skills are a separate product concern and are out of scope for this reconciliation — they
neither claim nor conflict with any control-plane responsibility.

---

## 9. Q6 — Disposition of durable-workflow: **C then E**

**C (donate selected mechanisms), then E (retire after migration).** Not A, not B, not D.

- **Not A (separate subsystem)** — two subsystems with clocks is precisely the "two schedulers"
  outcome. And per B-4 their divergence would never be alarmed.
- **Not B (compatibility wrapper)** — a wrapper is compatibility machinery forever, which David
  explicitly ruled out and which is the wrong answer for a single-user single-machine system.
- **Not D (replace parts of the control plane)** — the parts it would replace (approval,
  worker identity) are the parts the control plane does *better*, backed by 30 commits of
  adversarial review and an exploit-driven test suite.
- **C then E** — lift the three things it plausibly has and fcp provably lacks (a continuous
  loop's interval/exit policy, a heartbeat write cadence, a stall verdict), re-home them into
  unfrozen fcp modules, then delete `durable_workflow.py` and `state/durable-workflows.db`.

The donation is **conditional on §13's answers**. If its heartbeat is a supervisor timer rather
than a progress sample, there is nothing to donate on that dimension (see Q8). If its pid is
unqualified, that mechanism is not a lesser duplicate — it is an unsafe one, and must not be
adopted at any price.

## 10. Q7 — Minimum authoritative database architecture

**Target: one. Interim: two. Never three.**

| Database | Verdict |
| --- | --- |
| `state/control-plane.db` | **Authoritative.** Task truth, approval, dependencies, authorizations, evidence, audit, quota. |
| `state/dispatcher.db` | **Demoted, then absorbed.** Stops being a place where a terminal outcome is *decided*; becomes pure process telemetry (pids, birth identities, gate timings, stdout/stderr paths, heartbeat). |
| `state/durable-workflows.db` | **Deleted.** In every branch of the decision tree. |

### Is the two-database split a real authority boundary? No.

Both files are same-UID, `0600`, opened by the same process, under a ratified Boundary 0 that
treats same-UID processes as trusted. The split is a **review** boundary, not a **security**
boundary — which means there is no principled reason to keep it forever, and a concrete reason
to close it: **B-1 exists only because two files cannot commit atomically.**

### But do not collapse it yet, and this is a deliberate disagreement with my own analysis

The persistence adjudicator recommended collapsing `dispatcher.db` into `control-plane.db` as
schema v9. That is the right end state and the wrong first move:

- absorbing `dispatch_runs` means editing **`taskstore.py`**, the most-reviewed file in the
  repo, which reopens the focused control-plane gate and demands a fresh independent review;
- B-1 has a **three-line fix in unfrozen code** that makes the wedge *recoverable*;
- the collapse makes it *structurally impossible*.

Do the cheap fix now; do the structural fix deliberately, once the loop is proven and under its
own review. Sequencing is the whole point: a schema migration performed while the scheduler is
still unvalidated risks losing both.

**Until the collapse lands, one rule holds absolutely:** `dispatcher.db` may *record* a terminal
outcome but may never be the thing that *decides* it. The ledger decides. The journal reports.

## 11. Q8 — Heartbeat: what is reusable, what is unsafe

I cannot compare to code I have not read. I can state the two rules that determine whether
f2d64d9's heartbeat is worth anything, and both are derivable from the readable branch.

**Rule 1 — a heartbeat written on a timer by the supervising process detects nothing.**

`dispatch_runner.py` already supervises the worker child and already has its process-birth
identity journaled. A thread in that supervisor writing `heartbeat_at` every 15 s is
**definitionally redundant with `runner_identity_state() == "matched"`** — it stops exactly
when the process stops, which the existing identity check already detects, and it keeps beating
throughout a total model hang, which is the only case worth detecting.

> A heartbeat is only a new fact if it is gated on **observed worker progress** — tool-call
> count, output bytes, worktree mtime — having advanced since the last sample.

So the first question to ask of `durable_workflow.py` is not *"does it have a heartbeat"* but
*"does it beat on a timer or on progress?"* If timer: nothing to donate. If progress: that is
the missing piece and it should be lifted verbatim.

**Rule 2 — heartbeat staleness must never act alone.** The joint gate:

```
stale + gone      →  crashed          — existing finalize path already handles this
stale + reused    →  crashed          — existing path; pid reuse correctly rejected
stale + indeterminate → do nothing    — existing discipline, preserve it
stale + matched   →  THE ONLY NEW CASE — a live process making no progress
```

Only the last row is new work. Everything else is already correct and must not be re-implemented
by a second reaper.

**What is unsafe, unconditionally:** any reclaim driven by a bare `runner_pid` without a
process-birth witness (`ps -o lstart=`, `/proc/<pid>/stat` field 22, or `psutil.create_time`).
A bare pid cannot distinguish matched from reused. If `durable_workflow.py` kills or reclaims on
a bare pid, that mechanism is retired rather than donated, whatever else it gets right.

**What is insufficient in my own V2 design:** I proposed six liveness signals and a per-class
quiet window. That still stands, but it was specified as a *controller-side sampler* running on
a 60 s tick. Sampling every 60 s from outside cannot bound a runaway between samples. The
reconciliation is clearer: **in-run enforcement belongs in `dispatch_runner.py`** (a bounded
poll loop with a wall-clock deadline and an idle budget around the child, which
`verifier_runner.py:150-175` *already implements* and can be lifted verbatim into
`dispatch_runner.py:189`'s bare `child.wait()`), and **cross-run adjudication belongs in
`recover()`**. Two layers, one owner.

## 12. Q9 — `run()` vs the tick

**`run()` becomes an internal primitive; `tick` becomes the scheduler.** Concretely:

| Aspect | Disposition |
| --- | --- |
| `run()`'s **loop** — interval, exit condition, ordering of evaluate/select/dispatch/recover | **Donated** into a new unfrozen `scripts/lib/scheduler.py`. This is durable-workflow's one genuinely novel contribution. |
| `run()`'s **state** — any status/step column it consults as a precondition | **Retired.** Step ordering becomes `task_dependencies` edges; approval blocking becomes a read of the ledger columns. |
| `run()` as a **CLI verb** | **Retired** after migration. One scheduler, one entry point. |
| `dispatcher run --id` | **Retained** as the deterministic single-task primitive the tick calls, and as the manual escape hatch. |

Three constraints the tick must satisfy that my previous design under-specified:

1. **The tick must call `store.evaluate()` itself, from unfrozen code, before looking for
   work.** Per B-3, a tick built on `dispatcher.sh` verbs alone sees an empty ready set
   forever. And because `control-plane.py` is frozen, the tick cannot be a subcommand there —
   it needs its own module.
2. **`run_task` must be restructured from launch-and-block to launch-and-return.** Today it
   ends in `runner.wait()` (dispatcher.py:1531). A bounded tick that exits cannot block on a
   90-minute Claude run. The next tick's `recover()`/`finalize()` closes it out — which is
   exactly what that machinery is for, and it is all unfrozen.
3. **The tick must sweep the ready set, not take its head.** Head-only selection with a tick
   that exits is head-of-line blocking with extra steps.

Tick-not-daemon still stands, and the reconciliation strengthened the argument: every
control-plane guarantee is already per-process and crash-safe (lease + nonce + finalizer
election), a tick that exits is its own stall bound, and a daemon is one more long-lived
process that itself needs liveness monitoring.

## 13. Q10 — Recovery, compared case by case

| Case | Control plane, verified | durable-workflow |
| --- | --- | --- |
| **pid reuse** | **Correct.** `runner_identity_state` 4-way; indeterminate never acts. | **Unknown — blocker Q8** |
| **stale heartbeat** | **Absent.** No heartbeat exists. | **Unknown — blocker Q6** |
| **duplicate dispatch** | **Correct.** Single-use nonce + conditional update in one `BEGIN IMMEDIATE`; 6-process race proven. | **Unknown — blocker Q3** |
| **crash between state transitions** | **BROKEN — B-1.** The common path wedges permanently. | **Unknown — blocker Q1** |
| **reboot** | Mechanism sound (birth identity mismatches after boot ⇒ correctly "not matched") but **untriggered**, and **B-1/B-2 gate it**. | **Unknown** |
| **worker alive but useless** | **Absent.** A matched, result-less, progress-less run falls through every branch of `recover()` and is left forever. | **Claimed — blocker Q6, Q7** |
| **worker dead before state update** | **Correct** for `qwen-coder-local`. **Absent for every other worker — B-2.** | **Unknown — blocker Q7** |

## 14. Q11 — One approval model

**Authoritative:** `tasks.approval_required` / `tasks.approval_granted` in `control-plane.db`,
written **only** by `TaskStore.grant_approval` (taskstore.py:1108-1187) and `add_task`'s
undeclinable forcing (taskstore.py:918-928); consumed by exactly two readers that must agree —
`dispatch_policy`'s `approval_check` and `_ledger_objection` inside `_grant_and_assign`'s
`BEGIN IMMEDIATE`.

**The rule that prevents a second permission system, stated so it cannot be quietly violated:**

> Every other component in the system — `policy_gate`, an admission gate, a planner, and any
> workflow engine from branch (3) — is **DENY-ONLY**. It may refuse to call
> `dispatch_policy.assign`. It may force `approval_required = True` at admission. It may
> **never** record, cache, satisfy, expire-into-permitted, or substitute for a grant.

A deny-only component cannot become a competing authority, because the only thing it can do is
narrow. This is checkable by test: grep for writes to `approval_granted` outside `taskstore.py`
and assert there are none.

`main`'s `policy_gate.py` is retired *as an authority* — its exit-code permit contract
(policy_gate.py:51-53, 288-332) and both CLI on-ramps are deleted. Its path-confinement
predicate survives as a deny-only admission input.

One addition from my previous review still stands and matters more now: **approvals need a
TTL**, enforced as a dispatch-time refusal plus a hold, never as a ledger transition (there is
no `ready → awaiting_approval` edge and adding one means editing a frozen file).

## 15. Q12 — Migration

One authoritative control plane plus small adapters. Total new code ≈ 1,100 lines; total
retired ≈ 14,000.

```
R0  FIX B-1        3-line filter fix + per-iteration try/except + regression test   [unfrozen]
R1  FIX B-2        de-scope recover() pass 2 from LOCAL_WORKER                      [unfrozen]
R2  THE DOCTOR     dispatchability check with the SAME predicate that gates         [new module]
                   execution; non-empty undispatchable set = FINDING + notify
R3  LAUNCHAGENT    one bounded tick: recover() → evaluate() → sweep ready           [new module]
                   (dispatch_enabled=false; report-only for one week)
R4  ENABLE         dispatch_enabled=true, one lane, qwen-coder-local, pilot only
R5  LIVENESS       heartbeat on progress + in-run bounded poll + stall reaper       [unfrozen]
R6  DONATE MAIN    task-classes.json, DETERMINISTIC_TASK_TYPES, structured verdict,
                   project_registry → then DELETE main's orchestration stack
R7  ABSORB f2d64d9 answer §16's questions → donate loop policy → delete branch (3)
R8  COLLAPSE DB    dispatcher.db → control-plane.db schema v9    [FROZEN EDIT — own review]
R9  ADAPTERS       Claude / Sol / Codex behind the unchanged authority path
```

**R0–R2 are prerequisites, not preliminaries.** Every one of the five invariant attacks passes
through them. Shipping a scheduler before R0–R2 means shipping a machine that wedges silently
and faster.

## 16. Q13 — The eleven questions that settle the deferred decisions

Each is a single answerable question about `scripts/lib/durable_workflow.py` or
`state/durable-workflows.db`. Answers unblock the CONDITIONAL rows in §8.

1. Does it contain a `CREATE TABLE` with a **status / state / phase / step_index** column for a
   unit of work? *(Settles: task state, task identity, dependencies, persistence, continuous
   execution. Yes ⇒ a second state machine in a third file. No ⇒ a sidecar.)*
2. Does it contain any column, table or CLI verb that **records an approval being granted**, as
   opposed to reading `tasks.approval_granted`? And is there any path that advances past an
   approval point without calling `dispatch_policy.assign`? *(Settles: approval. Yes to either
   ⇒ a second permission system.)*
3. Does it obtain execution authority by calling **`dispatch_policy.assign(store, task_id,
   worker_id)`**, or by writing its own started/claimed/running row? *(Settles: dispatch —
   third caller of one decider, or second decider.)*
4. Does it construct a `taskstore.TaskStore` against **any path other than
   `<checkout>/state/control-plane.db`**? *(Settles: ownership. `dispatch_policy.py:542`
   derives the lease namespace from `Path(store.path).parent/'leases'`, so any other ledger
   path is a second lease namespace and one-writer-per-worktree stops holding.)*
5. In its dead/stale-worker path, does it call `taskstore.record_failure`, and **what is the
   literal reason string**? *(Settles: escalation. `"service unavailable: …"` classifies
   transient and holds the floor flat; liveness prose like `"worker heartbeat lost"` does not,
   and two of those walk a free tier-1 task onto metered tier 5.)*
6. Is `heartbeat_at` written **on a timer by the supervisor**, or only when an observed
   worker-progress value has advanced? *(Settles: heartbeat, stall detection — see Q8 Rule 1.)*
7. Before freeing a worktree or returning a task to ready, does it signal the process group and
   then **confirm the group is dead**, or reclaim on pid-liveness/heartbeat-age alone?
   *(Settles: recovery, ownership. Reclaim-without-confirmed-death is two writers on one
   worktree.)*
8. Is `runner_pid` qualified by a **process-birth witness** before any kill, or is it a bare
   integer? *(Settles: worker identity — see Q8 Rule 2.)*
9. Does its continuous mode call **`store.evaluate()`** itself, and does it **sweep** the ready
   set or take only the head? *(Settles: continuous execution — see B-3.)*
10. Does its continuous mode invoke **`Dispatcher.recover()`**, or run its own reclaim sweep?
    *(Settles: "adopt its invoker" vs "retire its second recovery agent" — opposite
    dispositions.)*
11. Does it call **`taskstore.add_task` at runtime** during step advancement, and if so does it
    `bind_worktree` before or after `grant_approval`? *(Settles: task generation. `add_task`
    takes no worktree and no depends_on, so runtime creation opens a window where a step is
    `ready` with an incomplete edge set — and `bind_worktree` **revokes** a granted approval at
    taskstore.py:1231-1246.)*

These are answerable by `grep` and `.schema`. Pushing the branch answers all eleven at once.

---

## 17. The invariant

> **IF APPROVED ELIGIBLE WORK EXISTS, THE MAC MUST NOT SILENTLY BECOME IDLE.**

I attacked this through five independent lenses. **All five broke it.** The honest answer:

### No — not as currently implemented, and not as I previously designed it.

Not because the reconciliation is wrong, but because four specific things must land first, and
my previous design contained only one of them.

| # | Lens | Breaks because | Required |
| --- | --- | --- | --- |
| 1 | crash & reboot | B-1 wedges the task; `recover()` returns 0 forever; lease held | **R0** |
| 2 | partial migration | B-2 — dispatch widens before recovery does, by default | **R1** |
| 3 | human factors | B-3 — nothing promotes `pending`; nothing survives a reboot | **R3** |
| 4 | adversarial policy | B-4 — correct fail-closed denials are individually right and collectively invisible | **R2** |
| 5 | the missing code | a third store with a clock gating on columns the authority cannot write | **§16 + R7** |

### What makes the invariant true

Three of the four are bug fixes. The fourth is the architectural one, and it is the piece my
previous review did not have:

> **The doctor: health must be computed with the same predicate that gates execution.**
>
> Every tick, for every task in `store.tasks(status="ready")`, run
> `dispatch_policy.evaluate_authorization(...)` as a dry run — it is pure, mints nothing and
> consumes nothing — **once per candidate worker, not just the router's pick**. A task for
> which every candidate denies is UNDISPATCHABLE. A non-empty undispatchable set is a
> **FINDING** that exits non-zero and notifies, reporting the failing check name verbatim.

My V2 §7.3 classified idle from *denials recorded during dispatch attempts*. That is strictly
weaker: it cannot see a task that was never attempted — because capacity was exhausted, or the
project is absent from `dispatcher-targets.json`, or no adapter is wired for its worker, or
`escalation_floor` outran the dispatcher's `available` set. The doctor sees all of those,
because it asks the *execution* question rather than the *routing* question.

With R0–R3 landed, the invariant holds in the following precise sense, which is the strongest
honest claim available:

> The Mac may still stop. It may not stop **silently**. Every tick either dispatches, or
> records a named reason it did not, or fails to run at all — and the third case is caught by
> a watchdog that shares no code with the tick.

---

## 18. Next implementation task — exactly one

**Fix B-1 in `scripts/lib/dispatcher.py` only. One commit, three parts that must land
together.** No new module, no scheduler, no LaunchAgent, no frozen file, no schema change.

1. Change the terminal-reconcile filter from `{"succeeded", "cancelled"}` to
   `{"succeeded", "failed", "cancelled"}` at **both** sites — `dispatcher.py:1884` (recover
   pass 1) and `dispatcher.py:1666` (finalize's re-entry guard).
   `_reconcile_journal_terminal` already accepts `"failed"` (dispatcher.py:1588) with the
   correct agreement predicate, and `validate_run_authority` fails closed on nonce mismatch, so
   a stale failed row against a newer live assignment cannot double-count an attempt.

2. Wrap **each iteration** of all three `recover()` loops in `try/except`, collecting per-run
   failures into the returned list instead of propagating. **This is a hard prerequisite of
   part 1, not a nicety.** Today the filter short-circuits on `run["state"]` *before* calling
   `self.store.task(run["task_id"])`, so a `dispatch_runs` row whose task no longer resolves is
   harmless. Adding `"failed"` dereferences it, and `taskstore.task()` raises
   `TaskStoreError("unknown task: …")`, which would abort the whole sweep and leave every later
   wedged task untouched. `dispatch_runs` is never pruned, so those rows accumulate for the life
   of the install. That range currently contains exactly one `except`, for a timestamp parse.

3. Add regression tests to `tests/test_dispatcher.py`: commit a journal terminal `failed`
   decision, raise before `_reconcile_journal_terminal`, reopen from fresh connections, and
   assert `recover()` returns the task to `ready` with `attempts` incremented and the worktree
   lease released — plus a companion asserting that one unreconcilable row does not stop a
   second wedged run in the same sweep.

Why this first: it is three lines of logic plus error handling, it is in unfrozen code, it is
independently valuable whatever §16 returns, it unwedges a failure mode that is live on the Mac
**right now**, and every scheduler design — mine, durable-workflow's, or a merged one — is
unsafe to run until it lands, because a scheduler's job is to keep producing terminal outcomes
and the common terminal outcome currently wedges.
