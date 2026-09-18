# ASTRA REVIEW PACK — Hermes reconciled control plane

For GPT-6 Astra, when available. Final architecture / adversarial review.

**Revised 2026-09-18** after a reconciliation review. This pack now describes the
**reconciled** architecture across three implementations, not the single-branch model in the
first draft. The credential-boundary question (Q7) is unchanged and still unresolved.

**Read in this order:** `docs/RECONCILIATION.md`, then `docs/V2-CONTROLLER-ARCHITECTURE.md`
(revised in place, `[REVISED]` markers), then in `DRLXAI/hermes-orchestrator`: `AGENTS.md`,
`docs/THREAT_MODEL.md` (Boundary 0), `REVIEW-8-BLOCK.md`,
`config/control-plane-freeze.sha256`.

---

## The situation in one paragraph

Three orchestrators exist. `main` @ `615ee9f` and `feat/control-plane` @ `877fb63` are
divergent siblings from merge base `b8dfd98` that have never been merged: `main` carries its
own router, permission gate, verifier and YAML task format (+14,251 lines), `feat/control-plane`
carries a rigorously reviewed authority surface (+18,962 lines). A third, `f2d64d9`
(durable-workflow: heartbeat, continuous run mode, stale-worker recovery), exists only on the
user's Mac and was not readable during this review. The reconciliation makes
`feat/control-plane`'s ledger authoritative for all fourteen responsibilities, retires `main`'s
orchestration stack (~14,000 lines) after donating four mechanisms, and retires
durable-workflow after donating its loop policy. The scheduler is a new unfrozen launchd tick.
The seven frozen modules are not rewritten.

## Four defects verified during reconciliation, all in unfrozen `dispatcher.py`

Confirm these first; every milestone depends on them.

| | Defect | Evidence |
| --- | --- | --- |
| **B-1** | `recover()` can never reconcile a `failed` run. `_reconcile_journal_terminal` accepts `"failed"` and is unreachable. Crash between the two DB commits ⇒ task permanently `assigned`, worktree lease held, `recover` returns 0 forever. Most common terminal path. | `dispatcher.py:1884`, `:1666`, `:1588`, `:839`, `:1893` |
| **B-2** | `recover()` pass 2 skips every worker but one. | `dispatcher.py:37`, `:1890` |
| **B-3** | Nothing automated promotes `pending → ready`. Sole promoter's only production caller is a frozen, human-typed verb. | `taskstore.py:937`, `:2161`; `control-plane.py:138`; `grep -c evaluate dispatcher.py` = 0 |
| **B-4** | HEALTHY is computed by a strictly weaker predicate than the one that gates execution. `ready` and `assigned` are not findings. | `control-plane.py:127`, `:145-155`; `routing.py:429-495` |

---

## Eleven unresolved questions, highest value first

Each states the decision taken, the argument against it, and what would change it.

### Q1 — Is the doctor sufficient to make "silently" false?
**Taken:** every tick dry-runs `dispatch_policy.evaluate_authorization` for every `ready` task,
once per candidate worker, and a non-empty undispatchable set is a notifying FINDING. This is
the answer to B-4 and to the user's core invariant.
**Against:** it asserts the execution predicate *at scan time*, not at *dispatch time* — a task
can pass the doctor and still be denied 40 seconds later, and the converse. It also runs the
twelve-check evaluation O(ready × workers) every 60 s, which is the most expensive thing in the
tick. And it is a *second* caller of the authorization predicate, so a future divergence
between the doctor's argument set and the dispatcher's is a new class of silent lie.
**Changes my mind:** a way to make the dispatcher itself *record* every denial durably —
`evaluate_authorization` is pure and mints nothing by design, which is why denials leave no
trace. If denial recording can be added without compromising that purity, the doctor becomes
redundant and should be deleted rather than maintained.

### Q2 — Should `dispatcher.db` be collapsed into `control-plane.db`, and when?
**Taken:** collapse is the right end state (B-1 exists *only* because two SQLite files cannot
commit atomically), but it is sequenced **last** (R8) because it requires editing frozen
`taskstore.py` for a schema v8→v9 migration, and B-1 has a three-line fix in unfrozen code.
**Against:** this leaves a hand-rolled two-phase commit with no coordinator in production for
the entire life of the migration, patched rather than eliminated, on the path every terminal
outcome traverses. Every milestone R0–R7 runs on top of a known-unsound commit protocol.
**Changes my mind:** a demonstration that the patched two-file protocol has no *remaining*
crash window (I believe it does: the fix makes the wedge recoverable on the next tick, not
impossible), or a judgement that reopening the control-plane gate early is cheaper than
carrying the risk.

### Q3 — Tick versus daemon
**Taken:** a launchd tick that opens the ledger, acts and exits. `THREAT_MODEL.md` records that
a long-lived cached `TaskStore` changes the ratified Boundary 0 premise. Reconciliation
strengthened this: every guarantee is already per-process and crash-safe, and a tick that exits
is its own stall bound.
**Against:** the tick must now also restructure `run_task` from launch-and-block to
launch-and-return so it does not block on a 90-minute run. That moves completion into
`recover()` — i.e. onto the path that B-1 just proved is the least-tested code in the system.
**Changes my mind:** evidence that launch-and-return meaningfully widens the crash window
between spawn and journal, or a supervisor construction that holds no `TaskStore` across ticks.

### Q4 — Is "DENY-ONLY" enforceable, or only stated?
**Taken:** exactly one writer of approval (`taskstore.grant_approval` + `add_task`'s
undeclinable forcing); every other component may refuse and may force `approval_required=True`
at admission, but may never record, cache, satisfy or substitute a grant.
**Against:** "may force `approval_required=True`" is itself a write into the authority's
namespace. An admission gate that can *always* force approval can deadlock the queue as
effectively as one that can grant it — a different failure, equally silent, and B-4 means it
would read as HEALTHY (`awaiting_approval` *is* a finding, so this one is actually visible —
confirm that).
**Changes my mind:** nothing, if the grep-based test (no writes to `approval_granted` outside
`taskstore.py`) is judged sufficient. Tell me if a stronger structural pin exists.

### Q5 — Is retiring ~14,000 lines of `main` correct, or overconfident?
**Taken:** `main`'s orchestration is retired; four mechanisms are donated first (`TASK_TYPES`,
`DETERMINISTIC_TASK_TYPES`, `_confine_write_roots`, the structured verdict shape).
**Against:** `main` has 730 lines of verifier tests and 434 of skill-registry tests. A
judgement that a 14k-line tree contributes exactly four mechanisms is the kind of conclusion
that is either obviously right or badly wrong, and it was reached by eight parallel readers in
one pass, not by running the code.
**Changes my mind:** any fifth mechanism in `main` that the surviving system cannot express.
Look hardest at `skill_registry.py` (is a skill a task type the reconciliation has no home
for?) and at `verifier.py`'s content checks.

### Q6 — Does a progress-gated heartbeat actually detect the hang?
**Taken:** a heartbeat on a supervisor timer is definitionally redundant with
`runner_identity_state == "matched"`; only a heartbeat gated on observed worker progress is a
new fact. `stale + matched` is the only new case.
**Against:** "progress" is defined by proxies (tool-call count, output bytes, worktree mtime) a
looping model can satisfy indefinitely — re-reading the same file advances tool calls forever.
The proxy set may be exactly as gameable as the timer it replaces.
**Changes my mind:** a progress signal that is monotone in *work completed* rather than *work
attempted*. If none exists, say so, and the wall-clock ceiling becomes the real mechanism with
the heartbeat demoted to telemetry.

### Q7 — Credential scoping for third-party CLI adapters *(UNCHANGED, still the sharpest gap)*
**Taken:** a broker injects only capability-scoped keys, never into `paths.spec`, never into
the worktree or logs.
**Against:** `claude` and `codex` read their own config files and hold their own credentials.
Hermes cannot scope what it does not inject, so a task with `code_edit` may still sit inside a
process that can reach every provider credential on the machine. The two real mitigations — a
dedicated macOS user, or a per-adapter `HOME` — **both reopen Boundary 0** (a second UID / a
distinct authority domain), which is a named automatic reopening condition.
**Needed:** a ruling on whether per-adapter `HOME` under the same UID is a "distinct authority
domain," and if not, what the honest residual is. **Still the one question that cannot be
resolved without reopening a ratified decision.**

### Q8 — Status API transport versus Boundary 0
**Taken:** `state/status.json` (atomic rename) plus a CLI. No listener, because tripwire **T1**
fires on "an externally reachable / network authority path."
**Against:** friction against "ChatGPT should be able to ask what's happening now."
**Changes my mind:** an argument that a read-only, no-authority loopback endpoint is
categorically outside T1 — the tripwire's wording is about *authority* paths, and status
carries none. If that holds, state it in `THREAT_MODEL.md` rather than assuming it.

### Q9 — Non-vacuous verification: general contract or per-profile checklist?
**Taken:** per-profile positive assertions; exit 0 is necessary, never sufficient.
**Against:** checklists rot. The cheapest route to "completed" is always a task that changes
nothing.
**Changes my mind:** a general contract — every verifier emits a structured count of *what it
examined*, and a zero count is a contract failure independent of exit code. Enforceable once in
`verifier_runner.py`. `main`'s donated `VerificationResult` shape may already be the right
carrier for this.

### Q10 — Does stall→escalation buy premium tiers for infrastructure problems?
**Taken:** a stall after real progress is non-transient; a stall with zero tool calls and zero
output is transient; crossing the metered boundary additionally requires one attributed
non-stall failure.
**Against:** a 24 GB machine under memory pressure produces exactly the "stalled after
progress" signature. Reconciliation sharpened this into a concrete question the unreadable
branch may already have answered badly: the **literal reason string** passed to
`record_failure` decides transient-vs-not, and liveness prose like `"worker heartbeat lost"`
classifies non-transient and walks a free tier-1 task onto metered tier 5.
**Changes my mind:** a host-pressure signal (swap-in rate, RSS trajectory, Ollama queue depth)
that separates model hang from host pressure without the heuristic.

### Q11 — Counting dispatches as a budget proxy
**Taken:** count our own dispatches (observation), never estimate tokens (fabrication).
**Against:** one Opus architecture run can exceed fifty local dispatches; a ceiling on count is
a ceiling on the wrong variable.
**Changes my mind:** a provider-reported usage figure Hermes can *read*. That belongs in
`quota_state.remaining`, which already exists and is deliberately populated only when something
external actually reported it.

---

## Settled, and worth attacking anyway

1. **`idle_reason` may never be empty when nothing runs.** Is a property test sufficient, or is
   there a structural form — as `THREAT_MODEL.md` argues for T3 — that pins it better?
2. **The freeze boundary already falls on the right seam**: all seven frozen files are
   *decide*-side; `dispatcher.py`, `dispatch_runner.py`, `dispatch_exec.py` and
   `verifier_runner.py` are *start*-side and unfrozen. Verify this claim independently — the
   entire "zero frozen edits through R7" plan rests on it.
3. **The filesystem is the rendezvous; chat identity is advisory.** Any failure mode where a
   lost `external_ref` costs more than one redispatch?
4. **One writer per repository**, because `.git/index` is repo-global. Path claims are a
   collision detector, not a concurrency enabler. Is that too conservative?
5. **`slot = 1` to start.** Continuous single-lane versus bursty three-lane on a 24 GB M4 Pro.
6. **The eleven blocker questions** in `RECONCILIATION.md` §16 — are they the *minimal* set
   that unblocks every CONDITIONAL row, or is one of them redundant and one missing?
