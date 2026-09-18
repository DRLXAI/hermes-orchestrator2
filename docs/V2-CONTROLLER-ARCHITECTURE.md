# Hermes V2 — Continuous Autonomous Work Controller

Design review. **No code changes proposed in this document are implemented.**

> **REVISED 2026-09-18 after the reconciliation review.** Read
> [`RECONCILIATION.md`](RECONCILIATION.md) first — it corrects three claims in this document
> and adds four defects (B-1…B-4) that are prerequisites for everything below. Sections
> corrected in place are marked **[REVISED]**. This is not an appendix: where this document
> was wrong, the wrong text has been replaced.

Reviewed trees: `DRLXAI/hermes-orchestrator` branch `feat/control-plane` @ `877fb63`
(77 files, ~21k lines) **and** branch `main` @ `615ee9f` (+14,251 lines, a second, divergent
orchestration stack). A third implementation exists only on David's Mac and is unreadable.

---

## 0. [REVISED] What the brief described, and where it actually lives

My original §0 asserted that `scripts/durable-workflow.sh`, `scripts/lib/durable_workflow.py`,
`tests/test_durable_workflow.py` and `state/durable-workflows.db` "do not exist."

**That was wrong.** They exist, on David's Mac, on branch
`integrate/control-plane-dispatcher` @ `f2d64d9`, which was never pushed. What I verified was
only that they are absent from GitHub:

```
git ls-remote --heads origin  →  only feat/control-plane @ 877fb63, main @ 615ee9f
git fetch origin f2d64d9      →  fatal: couldn't find remote ref f2d64d9
```

I still cannot read that code, and this document makes no claim about its internals. The
eleven questions in `RECONCILIATION.md` §16 settle every decision that depends on it.

**A third system exists that the brief did not mention and my first review missed.** `main` @
`615ee9f` is not an ancestor of `feat/control-plane`; both descend from `b8dfd98` and have
never been merged. `main` ships its own `model_router.py`, `policy_gate.py`, `verifier.py`,
`project_registry.py`, `skill_registry.py`, a local-qwen worker and a YAML task format — so
**two routers and two permission systems were already on GitHub** before durable-workflow
entered the picture. See `RECONCILIATION.md` §6.

## 1. Current architecture assessment

### [REVISED] There are three systems, not one

My first review assessed only `feat/control-plane`. `main` @ `615ee9f` is a divergent sibling
(merge base `b8dfd98`, never merged) carrying its own `model_router.py`, `policy_gate.py`,
`verifier.py`, `project_registry.py`, `skill_registry.py`, a local-qwen worker and a YAML task
format. A third implementation (`f2d64d9`, durable-workflow) exists only on the Mac.

Their disposition is decided in `RECONCILIATION.md` §8. In brief: **`feat/control-plane`'s
authority surface is authoritative for all fourteen responsibilities**; `main` donates four
mechanisms (`TASK_TYPES`, `DETERMINISTIC_TASK_TYPES`, path confinement, the structured verdict
shape) and is then retired as orchestration; durable-workflow donates its loop policy and is
then retired. The assessment below therefore describes the surviving system.

### What is actually there

```
config/workers.conf ─────────┐
                             ▼
             routing.py  (cheapest capable worker; tier ladder)
                             │
   escalation.py  ───────────┤   (does recorded failure justify a costlier tier?)
   quota.py       ───────────┤   (is this provider permitted at all?)
   worktree_lease.py ────────┤   (one writer per worktree, dev/ino keyed)
                             ▼
             dispatch_policy.py   ── 9 conditions, all must pass ──▶ single-use grant
                             │
                             ▼
             taskstore.py   (SQLite ledger, WAL + synchronous=FULL,
                             every mutation + its audit event in one transaction)
                             │
                             ▼
             dispatcher.py  (ONE adapter: qwen-coder-local via loopback Ollama)
                             │
                   dispatch_runner.py ─▶ dispatch_exec.py
                   verifier_runner.py (digest-pinned, shell-free argv)
```

Entry points, in full:

```sh
./scripts/control-plane.sh  status | plan | add | depend | approve | bind | assign |
                            authorize | evidence | review | succeed | fail |
                            transition | escalation check | quota … | leases | events
./scripts/dispatcher.sh     status | run --id <task> | cancel --id <task> | recover
```

### The finding that explains everything in the brief

**`run` takes `--id`. There is no code path anywhere in the repository that selects a
task.** `taskstore.tasks(status="ready")` exists and its docstring even says *"Ordered
the way a scheduler wants them"* — and nothing calls it to dispatch. `control-plane.sh
plan` computes routing for every ready task and then prints
`"advisory only: no work was dispatched and no model was invoked"`.

There is no loop, no daemon, no LaunchAgent, no cron registration (`jobs/*.yaml` are
`enabled: false` templates by deliberate policy). `grep -rn "while True"` returns two
hits, both SQLite busy-retry loops.

So the failure the brief describes is not a bug, a race, or a reliability defect. It is
the system working exactly as built: **Hermes V1 is a permission system, and David is
its scheduler.** Every question it answers is of the form *"may this run?"* No component
asks *"what should run now?"*, and no component starts anything.

This reframes the whole engagement. The brief asks for durability, recovery and
ownership — Hermes already has unusually strong versions of all three. What it needs is
roughly 1,200 lines of *drive*, plus an activity model, plus adapters.

### Quality assessment of what exists

Unusually high, and I want to be specific because it constrains V2.

- **The two-database split is correct and deliberate.** `control-plane.db` is the
  authority record; `state/dispatcher.db` is the execution journal. `docs/DISPATCHER.md`
  enumerates the exactly-two cross-database reconciliation gaps and handles both.
- **Authority is consumed, not held.** `authorize` mints a nonce bound to
  (task, worker, attempt, model, worktree); `assign` re-derives every condition inside
  the same `BEGIN IMMEDIATE` that performs the mutation and spends the nonce with a
  conditional update. Six-process races land on `assigned, spent: 1`.
- **Evidence is stamped, not asserted.** `add_evidence` takes its author from the
  current assignment. Exit codes and output paths come from the dispatcher's own process
  journal. A worker cannot vouch for itself.
- **Escalation is fabrication-resistant.** A failure only moves the escalation floor if
  it carries the tier of a genuinely spent authorization; identical repeated failures
  count once; transient classes never move the floor at all; failure *text* is data and
  never policy input.
- **Process identity, not pid.** `runner_identity` records process-birth identity and
  `runner_identity_state()` returns `matched | not matched | indeterminate`.
  Indeterminate never acts. This is the correct discipline and it already defeats pid
  reuse, including across reboot.
- **The Growth OS trust boundary is exemplary** and should be the template for every
  future adapter: *a checkout under automation is data; the orchestrator never imports,
  requires, spawns or executes anything inside it.* `tests/test_growth_trust.sh` builds
  a hostile checkout and proves it.
- **Boundary 0 is explicitly ratified** in `docs/THREAT_MODEL.md`: same-UID processes
  are trusted; SQLite is an arbiter, not an exclusion mechanism. This decision has
  named reopening conditions and they are load-bearing for V2 (see §16).

### The constraint V2 must respect

`config/control-plane-freeze.sha256` pins seven modules by SHA-256:

```
taskstore.py  dispatch_policy.py  worktree_lease.py  routing.py
escalation.py  quota.py  control-plane.py
```

`AGENTS.md`: *"Any change to a pinned module reopens the focused control-plane gate and
must ship with the relevant race and authority regressions, the full suite, an updated
freeze manifest, and a fresh independent review."*

**Architectural rule for V2, from which most of the rest of this design follows:**

> Everything new is expressed as *admission-side refusals* and *operations-database
> rows*. Nothing new becomes a ledger state, a ledger transition edge, or a column on a
> frozen table.

I checked this is achievable end-to-end. It is — including approval expiry, stall
handling, holds, path ownership and roadmap provenance — with **zero edits to frozen
files**. `dispatcher.py`, `dispatch_runner.py` and `verifier_runner.py` are *not*
frozen, which is where heartbeats and new adapters belong.

---

## 2. What V1 already solves

Stated precisely, because the brief under-credits some of this and over-credits the
rest.

| Requirement | Status | Mechanism |
| --- | --- | --- |
| Durable task state across crash/reboot | **Solved** | WAL + `synchronous=FULL`; status and history mutate in one transaction |
| Dependency gating | **Solved** | Re-derived from the ledger at authorization; cycles refused at insert |
| Approval blocking | **Solved** | `awaiting_approval`; `grant_approval` requires a registry worker with `kind = human` |
| Bounded retries | **Solved** | `max_attempts`, attempt/audit reconciliation |
| Cost discipline | **Solved, strongly** | no-cheaper-worker check + one-rung escalation + quota-denies-by-default |
| Worker cannot self-certify | **Solved** | evidence stamped from assignment; independent reviewer required for high risk |
| One writer per worktree | **Solved as of `f175b00`** | lease acquired inside the assignment transaction, dev/ino keyed, stale-break requires dead holder **and** age |
| Dead-worker detection | **Solved, for one worker** | `runner_identity_state`; indeterminate never acts — but only reached for `qwen-coder-local` (B-2) |
| Crash-before-launch recovery | **Solved** | compare-and-swap orphan record; real launch always beats orphan cleanup |
| Reboot reconciliation *mechanism* | **[REVISED] BROKEN** | `recover()` is blind to `failed` (B-1) and scoped to one worker id (B-2). See below. |
| Verification is a separate authority | **Solved** | digest-pinned shell-free verifier argv; nonzero verifier exit fails a zero-exit model |
| Credentials out of the worker env | **Solved for the one adapter** | allowlisted environment, no provider credentials |

**[REVISED] Three corrections to this table**, from the reconciliation review. All three
made the system look healthier than it is, and all three are in `dispatcher.py`, which is
**not** frozen — the seven frozen digests verify OK on disk.

- **B-1. `recover()` can never reconcile a `failed` run.** `dispatcher.py:1884` and `:1666`
  both filter `{"succeeded", "cancelled"}`. `_reconcile_journal_terminal` accepts `"failed"`
  (`:1588`) and handles it correctly — **nothing can call it with one**. A crash between the
  journal commit (`dispatcher.db`) and the ledger reconcile (`control-plane.db`) leaves the
  task permanently `assigned` with its worktree lease held, and `dispatcher.sh recover`
  returns `recovered 0` forever. This traverses the *most common* terminal path: every
  nonzero worker exit.
- **B-2. `recover()` pass 2 is scoped to one worker.** `if task.assigned_worker !=
  LOCAL_WORKER: continue` (`:1890`, `LOCAL_WORKER = "qwen-coder-local"` at `:37`). But
  `dispatch_policy.assign` accepts any worker and already has two production callers. Any
  assignment to `qwen3-local` — free, local, enabled, the natural pick for `analysis` work —
  is outside every recovery sweep in the system.
- **B-3. Nothing automated promotes `pending → ready`.** `add_task` hardcodes `'pending'`
  (taskstore.py:937). `TaskStore.evaluate()` is the sole promoter and has exactly one
  production caller: `control-plane.py:138`, a verb a human types. `grep -c evaluate
  scripts/lib/dispatcher.py` returns **0**.

So the durability foundation is strong *in the ledger* and defective *in the dispatcher*.
The frozen half earned its reputation; the unfrozen half has not been through the same gate.
Six of the brief's twenty-four asks are done to a higher standard than the brief specifies;
the seventh — recovery — is not done at all outside one worker and one state.

---

## 3. Exact failure modes remaining

Ordered by how directly each causes the idle Mac.

### F-1 — No selector. *(root cause of the brief)*
Nothing converts "there are 5 ready tasks" into "start one." **Directly causes CASE 1.**

### F-2 — [REVISED] Recovery is untriggered **and** defective
I previously wrote that `recover()` "is correct and is only ever run when a human types
`dispatcher.sh recover`." The second half stands; the first does not. `recover()` is blind to
`failed` (B-1) and scoped to `qwen-coder-local` (B-2). **Supplying a trigger for a defective
reaper makes the wedge arrive faster, not less often.** Fixing both is a prerequisite for the
loop, not a follow-up to it. **CASE 4.**

### F-3 — No activity model
`heartbeat` appears zero times. In `recover()`, an active run whose runner identity is
`matched` and which has written no result falls through every branch and is left
untouched, forever, with its worktree lease held. A hung model is invisible and blocks
its repository indefinitely. **CASE 3, completely unhandled.**

### F-4 — One adapter
`run_task` hard-codes `available=frozenset({LOCAL_WORKER})` and refuses any other route.
Claude, Codex, Sol, CoS and browser execution have no adapter. `workers.conf` describes
them, `routing.py` will recommend them, and nothing can execute them. The registry is
aspirational for 4 of its 5 workers.

### F-5 — No task generation
`add_task` is a CLI verb. A roadmap is a document a human reads and retypes. No
roadmap, milestone, or provenance entity exists — `project` is a free-text column.

### F-6 — No idle classification
Nothing distinguishes "all approved work is complete" from "the loop broke." Both look
like `status` returning exit 0. **CASE 8.**

### F-7 — No resource model
Nothing knows how much RAM is free, whether a build is running, or that two 27B models
will not co-reside in 24 GB unified memory. **CASE 7.**

### F-8 — Ownership is whole-worktree only
The lease is a real, correct exclusion primitive, but its granularity is the entire
checkout. Two tasks touching disjoint directories of one repo serialize completely.
**CASE 6 is over-solved** (correctly, but at the cost of all intra-repo concurrency).

### F-9 — No notifications
`awaiting_approval` is visible only if someone runs `status`. A task can wait for David
indefinitely with no signal leaving the machine.

### F-10 — Status is human-prose, not machine state
`command_status` emits `INFO task x status=ready …` lines. No JSON, no timestamps, no
freshness marker, no way for ChatGPT to distinguish live state from a stale read.

### F-11 — Head-of-line blocking is structurally likely
Not yet a bug (there is no loop), but the naive loop — *pick the highest-priority ready
task, authorize it, dispatch* — stops the entire machine on the first task that is
awaiting approval. **This is the single easiest way to reintroduce the brief's problem
while believing it is fixed.** Called out again in §7 and §24.

### F-12 — Secrets will enter `paths.spec` the moment a second adapter lands
`run_task` writes `{"command": …, "environment": …}` to `paths.spec` as JSON (mode
0600) and it persists for the run's lifetime and beyond. There are no secrets today
because the only adapter is loopback Ollama. A Codex or Claude adapter that needs an API
key will put it there by the path of least resistance. **Pre-emptive finding.**

### F-14 — [NEW] The health predicate is weaker than the execution predicate
`control-plane.py:127` computes `findings = failed + blocked + awaiting_approval`. `ready` is
not a term and `assigned` is not a term, so a task wedged by B-1 and a `ready` queue that is
100% undispatchable **both exit HEALTHY**. Meanwhile `plan` escalates only on
`routing.recommend()`'s `needs_human`, and `recommend()` consults neither quota, nor
`metered`, nor which adapters are wired — so it certifies routes that cannot execute.

> Health is computed by `routing.recommend()`. Execution is gated by
> `dispatch_policy.evaluate_authorization()`'s twelve checks. **Nothing compares the two.**

This is the structural reason the Mac goes idle *silently* rather than *noisily*, and it is
more important than any single bug above. The fix is the doctor (§7.4).

### F-15 — [NEW] Two routers and two permission systems already exist on GitHub
`main` @ `615ee9f` ships `model_router.py` and `policy_gate.py`. Neither is reachable from
`feat/control-plane`, and neither shares a state model with it. Retiring them is part of V2,
not a separate cleanup. See `RECONCILIATION.md` §6 and §8.

### F-13 — `store.evaluate()` is O(tasks × dependencies) with a query per dependency
Irrelevant at 100 tasks, a real tick cost at 10,000. Mentioned only so it is a known
scaling limit rather than a surprise.

---

## 4. Proposed V2 architecture

Four new components. One new database file (the existing `state/dispatcher.db`,
extended and renamed in role to the *operations database*). Zero frozen-file edits.

```
                    launchd  com.hermes.tick   StartInterval 60, RunAtLoad
                              │
                              ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │  hermes-tick   (runs, does one pass, EXITS — never long-lived)   │
   │                                                                  │
   │   1 machine lock (O_EXCL symlink, pid+boot+age stale-break)      │
   │   2 dispatcher.recover()              ← existing, unmodified     │
   │   3 liveness sample  → progress_samples → stall verdicts  [NEW]  │
   │   4 resolve stalls per policy                             [NEW]  │
   │   5 store.evaluate()                  ← existing, public API     │
   │   6 refresh holds (why each task cannot run)              [NEW]  │
   │   7 capacity: slots, resource classes, RAM, disk, load    [NEW]  │
   │   8 SELECT loop over ALL candidates, not just the first   [NEW]  │
   │   9 classify idle; write tick row with a non-empty reason [NEW]  │
   │  10 planner admission, if budget remains                  [NEW]  │
   │  11 write state/status.json atomically; queue notifications      │
   └──────────────────────────────────────────────────────────────────┘
          │ spawns, detached (start_new_session), then exits
          ▼
   ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
   │ lane 0          │   │ lane 1          │   │ lane 2 (heavy)  │
   │ dispatcher run  │   │ dispatcher run  │   │ dispatcher run  │
   │  --id T-101     │   │  --id T-118     │   │  --id T-122     │
   │  └ runner ──────┼───┼── heartbeat.json┼───┼── every 15s     │
   └─────────────────┘   └─────────────────┘   └─────────────────┘

   launchd  com.hermes.watchdog  StartInterval 900
      └─ "was there a tick in the last 5 minutes?"  → notify if not
```

### The central design decision: **tick, not daemon**

The obvious design is a long-lived supervisor process. I am recommending against it, for
a reason specific to this codebase:

`docs/THREAT_MODEL.md`, Boundary 0, records as a review candidate:

> *"that no module-level or cached `TaskStore` exists, since a long-lived holder changes
> the premise from 'many short trusted processes, serialized' to 'one owner plus
> others'."*

A daemon holding an open `TaskStore` **reopens Boundary 0** and therefore the entire
control-plane review gate. A tick that opens the ledger, acts, and exits is
indistinguishable from what `control-plane.sh` already does on every invocation. The
ratified premise survives untouched.

It is also better engineering here:

- **No "the daemon died" failure class.** launchd fires again in 60 seconds regardless.
- **No in-memory state to get stale or corrupt.** Every tick re-derives from SQLite.
- **Reboot persistence is free** — `RunAtLoad` plus the first tick's `recover()`.
- **Crash during a tick is harmless** — the tick is idempotent by construction, and the
  machine lock is stale-breakable.
- **It matches the repo's existing concurrency proof** — `tests/test_concurrency.py`
  already demonstrates six independent processes serializing correctly on the ledger.

The cost is up to 60 seconds of latency between a task finishing and the next starting.
For a system whose current latency is *"until David notices,"* this is not a trade-off.

### Where each new concern lives

| Concern | Home | Frozen? |
| --- | --- | --- |
| Selection, capacity, idle classification | `scripts/lib/controller.py` (new) | n/a |
| Liveness sampling, stall verdicts | `scripts/lib/liveness.py` (new) | n/a |
| Heartbeat emission | `dispatch_runner.py` (edit) | **no** |
| Adapters (Claude, Codex, Sol, CoS) | `scripts/lib/adapters/*.py` (new) | n/a |
| Roadmap/milestone/planner + admission gate | `scripts/lib/planner.py`, `admission.py` (new) | n/a |
| Status + notifications CLI | `scripts/hermesctl.py` (new — **not** `control-plane.py`, which is frozen) | n/a |
| Path claims, resource leases, holds | operations database | n/a |
| Task lifecycle, authority, routing, escalation, quota, worktree lease | **unchanged** | **yes** |

---

## 5. SQLite schema

`state/control-plane.db` — **unchanged.** Schema version stays 8.

`state/dispatcher.db` → the operations database. All additive; existing tables keep
their names so `recover()` and the journal code are untouched.

### 5.1 Extensions to `dispatch_runs`

The file already performs additive `ALTER` discovery under `BEGIN IMMEDIATE` (lines
268–300), so these follow the established pattern exactly:

```sql
ALTER TABLE dispatch_runs ADD COLUMN heartbeat_at      TEXT;
ALTER TABLE dispatch_runs ADD COLUMN heartbeat_seq     INTEGER NOT NULL DEFAULT 0;
ALTER TABLE dispatch_runs ADD COLUMN phase             TEXT;     -- launching|working|verifying
ALTER TABLE dispatch_runs ADD COLUMN stall_strikes     INTEGER NOT NULL DEFAULT 0;
ALTER TABLE dispatch_runs ADD COLUMN lane_id           TEXT;
ALTER TABLE dispatch_runs ADD COLUMN resource_class    TEXT;
ALTER TABLE dispatch_runs ADD COLUMN boot_id           TEXT;     -- kern.boottime at launch
ALTER TABLE dispatch_runs ADD COLUMN deadline_at       TEXT;     -- absolute wall-clock ceiling
ALTER TABLE dispatch_runs ADD COLUMN failure_fingerprint TEXT;
```

`RUN_STATES` stays `('assigned','running','succeeded','failed','cancelled')`. **`stalled`
is deliberately not a run state** — a stall is a *verdict about* a running run, recorded
in `progress_samples`, that resolves into `failed` or `cancelled`. Adding it as a state
would require the same reconciliation matrix to grow a column for a condition that is
never terminal.

### 5.2 Activity

```sql
CREATE TABLE progress_samples (
    sample_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id             TEXT NOT NULL,
    sampled_at         TEXT NOT NULL,
    heartbeat_age_s    INTEGER,
    heartbeat_seq      INTEGER,
    cpu_seconds        REAL,        -- cumulative, from ps -o time=
    rss_kb             INTEGER,
    stdout_bytes       INTEGER,
    stderr_bytes       INTEGER,
    worktree_mtime_max TEXT,
    git_dirty_files    INTEGER,
    tool_calls         INTEGER,     -- from the worker's own run journal
    marker_hits        INTEGER,     -- task-declared progress markers satisfied
    verdict            TEXT NOT NULL
        CHECK (verdict IN ('active','quiet','stalled','indeterminate')),
    detail             TEXT NOT NULL DEFAULT ''
);
CREATE INDEX progress_samples_run ON progress_samples(run_id, sample_id);
```

### 5.3 Scheduling

```sql
CREATE TABLE controller_ticks (
    tick_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    host             TEXT NOT NULL,
    boot_id          TEXT NOT NULL,
    pid              INTEGER NOT NULL,
    eligible_count   INTEGER NOT NULL DEFAULT 0,
    running_count    INTEGER NOT NULL DEFAULT 0,
    dispatched_count INTEGER NOT NULL DEFAULT 0,
    idle_reason      TEXT NOT NULL DEFAULT '',
    outcome          TEXT NOT NULL DEFAULT 'ok'
        CHECK (outcome IN ('ok','locked_out','error'))
);
CREATE INDEX controller_ticks_time ON controller_ticks(started_at);
```

**The load-bearing invariant of the entire design:**

> `dispatched_count = 0 AND running_count = 0 AND idle_reason = ''` must be impossible.
> The controller must always be able to name why it is not working.

This is the structural form of "previous task completed is not a valid reason to stop."
It is asserted by `tests/test_controller_invariants.py::ControllerAlwaysExplainsIdle`.

### 5.4 Resources and ownership

```sql
CREATE TABLE resource_leases (
    resource     TEXT PRIMARY KEY,   -- 'slot:0' 'ollama' 'browser' 'build:tie' 'premium'
    holder_run   TEXT NOT NULL,
    task_id      TEXT NOT NULL,
    holder_pid   INTEGER,
    holder_boot  TEXT NOT NULL,
    acquired_at  TEXT NOT NULL,
    expires_at   TEXT NOT NULL
);

CREATE TABLE path_claims (
    claim_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_dev     INTEGER NOT NULL,   -- canonical repo identity, NOT its spelling
    repo_ino     INTEGER NOT NULL,
    path_prefix  TEXT NOT NULL,      -- repo-relative, normalized, trailing '/'
    task_id      TEXT NOT NULL,
    run_id       TEXT NOT NULL,
    acquired_at  TEXT NOT NULL,
    released_at  TEXT
);
CREATE UNIQUE INDEX path_claims_live
    ON path_claims(repo_dev, repo_ino, path_prefix) WHERE released_at IS NULL;
```

The unique index only catches *exact* duplicates; prefix containment
(`src/` vs `src/lib/`) is checked in the acquire transaction under `BEGIN IMMEDIATE`,
both directions. Repo identity is `(st_dev, st_ino)` for the same reason
`worktree_lease.py` already uses it: `/Users/david/Projects` and
`/Users/david/projects` are one repository on a case-insensitive filesystem.

### 5.5 Holds — why a task is not runnable, in human terms

```sql
CREATE TABLE holds (
    hold_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    reason      TEXT NOT NULL CHECK (reason IN (
        'approval_spend','approval_publish','approval_visibility',
        'approval_destructive','approval_new_direction','credential_missing',
        'dependency_incomplete','resource_unavailable','policy_denied',
        'external_blocker','quarantined')),
    detail      TEXT NOT NULL,
    raised_at   TEXT NOT NULL,
    cleared_at  TEXT,
    notified_at TEXT
);
CREATE UNIQUE INDEX holds_live ON holds(task_id, reason) WHERE cleared_at IS NULL;
```

Holds are **derived, not authoritative.** They are recomputed every tick from the
ledger and the policy modules. They exist so the status API can say *"waiting for
publication authorization"* instead of *"not ready,"* without inventing a new ledger
state. A hold never gates anything — the frozen policy does the gating.

### 5.6 Roadmaps, milestones, provenance

```sql
CREATE TABLE roadmaps (
    roadmap_id        TEXT PRIMARY KEY,
    project           TEXT NOT NULL,
    title             TEXT NOT NULL,
    source_path       TEXT NOT NULL,
    source_sha256     TEXT NOT NULL,   -- what David actually approved, byte-exact
    approved_by       TEXT NOT NULL,
    approved_at       TEXT NOT NULL,
    task_budget       INTEGER NOT NULL CHECK (task_budget >= 1),
    max_tier          INTEGER NOT NULL,
    max_risk          TEXT NOT NULL CHECK (max_risk IN ('low','medium','high')),
    allowed_paths     TEXT NOT NULL,   -- newline-separated repo-relative prefixes
    status            TEXT NOT NULL
        CHECK (status IN ('approved','exhausted','superseded','revoked'))
);

CREATE TABLE milestones (
    milestone_id TEXT PRIMARY KEY,
    roadmap_id   TEXT NOT NULL REFERENCES roadmaps(roadmap_id),
    ordinal      INTEGER NOT NULL,
    title        TEXT NOT NULL,
    acceptance   TEXT NOT NULL,   -- what "done" means, written by David at approval time
    status       TEXT NOT NULL CHECK (status IN ('pending','open','complete','abandoned')),
    approved     INTEGER NOT NULL CHECK (approved IN (0,1)),
    UNIQUE (roadmap_id, ordinal)
);

CREATE TABLE planner_batches (
    batch_id         TEXT PRIMARY KEY,
    milestone_id     TEXT NOT NULL REFERENCES milestones(milestone_id),
    planner_worker   TEXT NOT NULL,
    requested_at     TEXT NOT NULL,
    raw_output_path  TEXT NOT NULL,
    proposal_sha256  TEXT NOT NULL,
    verdict          TEXT NOT NULL
        CHECK (verdict IN ('admitted','partially_admitted','rejected','malformed')),
    admitted_count   INTEGER NOT NULL DEFAULT 0,
    rejected_count   INTEGER NOT NULL DEFAULT 0,
    reason           TEXT NOT NULL DEFAULT ''
);

CREATE TABLE task_origin (          -- no FK: the tasks table is in the other database
    task_id      TEXT PRIMARY KEY,
    roadmap_id   TEXT NOT NULL,
    milestone_id TEXT NOT NULL,
    batch_id     TEXT NOT NULL,
    admitted_at  TEXT NOT NULL
);

CREATE TABLE proposals (            -- what the planner wanted but was NOT allowed to create
    proposal_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id     TEXT NOT NULL,
    milestone_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    rejected_for TEXT NOT NULL,
    raised_at    TEXT NOT NULL,
    decided_at   TEXT,
    decision     TEXT CHECK (decision IN ('promoted','declined'))
);
```

`proposals` is the structural home of *"new strategic work requiring approval."* A
planner suggestion that fails admission does not vanish and does not become a task — it
becomes a row David can promote into a new milestone. **This is the distinction the
brief asks for, made into a table rather than a convention.**

### 5.7 Worker sessions — the WORKER_IDENTITY_LOST fix

```sql
CREATE TABLE worker_sessions (
    session_id   TEXT PRIMARY KEY,  -- OURS. minted by the dispatcher. authoritative.
    run_id       TEXT NOT NULL,
    worker_kind  TEXT NOT NULL,
    external_ref TEXT,              -- THEIRS. nullable. advisory. never read by policy.
    handoff_path TEXT NOT NULL,     -- the task packet we wrote (0600)
    result_path  TEXT NOT NULL,     -- where a result must appear for us to believe it
    created_at   TEXT NOT NULL,
    last_seen_at TEXT
);
```

The design rule, stated so it cannot be quietly violated:

> **The filesystem is the rendezvous. The chat session is not.**
> A run is identified by the run directory Hermes created and the result file Hermes
> named. `external_ref` is written for forensics and is never an input to any decision.
> Losing it changes nothing: the task is still `assigned`, the authorization is still
> spent, the evidence path is still empty, and the stall detector will redispatch on
> schedule.

`tests/test_controller_invariants.py::ExternalRefIsNeverPolicyInput` asserts by AST that
no module outside the status formatter reads `external_ref`. **CASE 2.**

### 5.8 Notifications

```sql
CREATE TABLE notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL,
    kind            TEXT NOT NULL,
    dedupe_key      TEXT NOT NULL,
    urgency         TEXT NOT NULL CHECK (urgency IN ('info','action','urgent')),
    subject         TEXT NOT NULL,
    body            TEXT NOT NULL,
    delivered_at    TEXT,
    acknowledged_at TEXT,
    delivery_error  TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX notifications_open ON notifications(dedupe_key)
    WHERE acknowledged_at IS NULL;
```

---

## 6. State machine

The brief proposes one flat list of eleven states. **That is the wrong shape**, and the
existing two-database split already shows why: *what work exists* and *what an attempt
is doing* have different lifetimes, different owners and different durability
requirements. A task outlives its attempts.

### Level 1 — task lifecycle (frozen ledger, unchanged)

```
                 ┌──────────────────────────────────┐
                 ▼                                  │
  pending ──▶ awaiting_approval ──▶ ready ──▶ assigned ──▶ succeeded ✝
     │              │                 │  ▲        │
     │              │                 │  └────────┤ (retry_scheduled)
     ├──────────────┴────▶ blocked ◀──┘           │
     │                        │                   ├──▶ failed ✝
     └────────────────────────┴───────────────────┴──▶ cancelled ✝
```

### Level 2 — execution lifecycle (operations database, per attempt)

```
  registered ──▶ launching ──▶ working ──▶ verifying ──▶ succeeded
                     │            │            │
                     │            ├─ ACTIVE ───┤        (verdict, not a state)
                     │            ├─ QUIET ────┤
                     │            └─ STALLED ──┴──▶ terminated ──▶ failed
                     └────────────────────────────────────────────▶ failed
                                                                └──▶ cancelled
```

### Mapping the brief's vocabulary onto this

| Brief state | Where it lives | Note |
| --- | --- | --- |
| `queued` | task `pending` | eligibility not yet established |
| `eligible` | task `ready` | |
| `dispatched` | task `assigned` + run `registered`/`launching` | |
| `running` | task `assigned` + run `working` | |
| `verifying` | task `assigned` + run `verifying` | |
| `completed` | task `succeeded` | requires evidence **and** verifier exit 0 |
| `failed` | task `failed` | attempt budget exhausted |
| `stalled` | **not a state** — a `progress_samples.verdict` | resolves to run `failed`, task `ready` or `failed` |
| `blocked` | task `blocked` + `holds.reason` | |
| `waiting_approval` | task `awaiting_approval` + `holds.reason` | reason names *which* approval |
| `cancelled` | task `cancelled` | |

The brief invites improvement, so: **the one genuine gap in the ledger's vocabulary is
that `blocked` and `awaiting_approval` cannot say *why*.** "Waiting for credentials,"
"waiting for spending authorization" and "waiting for publication authorization" are
three materially different human actions with one ledger state between them. The
`holds` table supplies the distinction without adding states — which matters, because
adding a state to `STATUSES` means editing a frozen file *and* growing `TRANSITIONS`,
and every new edge is a new bypass to review.

---

## 7. Scheduler algorithm

```python
def tick() -> int:
    lock = machine_lock()                      # O_EXCL symlink; pid + boot_id + age
    if lock is None:
        record_tick(outcome="locked_out")      # a previous tick is still running
        return 0

    with lock:
        boot = current_boot_id()
        tick_row = open_tick(boot)

        # ---- 1. reconcile before deciding anything -----------------------
        dispatcher.recover()                   # existing, unmodified
        reap_dead_lanes(boot)                  # any run whose boot_id != boot is dead
        expire_resource_leases(boot)
        release_orphan_path_claims()

        # ---- 2. is anything that claims to be running actually working? --
        for run in journal.active():
            sample = liveness.sample(run)       # §12
            journal.record_sample(run, sample)
            if sample.verdict == "stalled":
                resolve_stall(run, sample)      # §13

        # ---- 3. recompute eligibility -----------------------------------
        # [REVISED] The tick MUST call this itself. It is the sole pending->ready
        # promoter and its only production caller is control-plane.py:138, a verb a
        # human types (B-3). A tick built on dispatcher.sh verbs alone sees an empty
        # ready set FOREVER. control-plane.py is frozen, so this cannot live there.
        store.evaluate()                        # public frozen API, called from unfrozen code
        holds = refresh_holds(store)            # derived, explanatory only

        # ---- 3b. THE DOCTOR [NEW] ---------------------------------------
        # Ask the EXECUTION question, not the routing question, for every ready
        # task -- once per candidate worker, not just the router's pick.
        undispatchable = doctor.scan(store, workers)   # see 7.4

        # ---- 4. what can physically start right now? --------------------
        capacity = resources.capacity()         # slots, ollama, build, browser,
                                                # premium, RAM, disk, load average
        running = journal.active()

        # ---- 5. THE LOOP. Iterate ALL candidates. Never stop at the first.
        dispatched = []
        denials    = []
        for task in candidates(store):          # ordered §7.1
            if capacity.exhausted():
                denials.append((task, "capacity"))
                break                           # ordering is stable; retry next tick
            verdict = try_dispatch(task, capacity)
            if verdict.ok:
                dispatched.append(task.id)
                capacity.consume(verdict.resources)
            else:
                denials.append((task, verdict.reason))
                raise_hold(task, verdict.reason)
                continue                        # ◀◀ CASE 5. THE critical line.

        # ---- 6. explain ourselves ---------------------------------------
        # [REVISED] undispatchable feeds classify_idle: a task the loop never
        # ATTEMPTED produces no denial, so denials alone cannot see it.
        reason = "" if (dispatched or running) else classify_idle(store, denials, undispatchable, capacity)
        close_tick(tick_row, dispatched, running, reason)

        # ---- 7. generate more work if the queue is genuinely dry ---------
        if reason in ("IDLE_PLANNING", "IDLE_LEGITIMATE"):
            planner.consider(store)             # §8, budget-bounded

        emit_notifications()
        write_status_json()                     # atomic rename
    return 0
```

### 7.1 Candidate ordering

```
ORDER BY  hold_free DESC,          -- tasks with no live hold first
          priority  DESC,
          milestone_ordinal ASC,   -- finish milestone 1 before starting milestone 2
          dependency_fanout DESC,  -- unblock the most downstream work first
          created_at ASC,
          id ASC
```

`dependency_fanout` (how many tasks depend on this one) is the one non-obvious term and
it earns its place: it is what makes the queue *drain* rather than widen.

### 7.2 `try_dispatch` — order matters, cheapest refusal first

```
1. hold check            — live hold?                       → deny (no cost)
2. resource pre-check    — needed classes free? RAM? disk?  → deny (no cost)
3. route                 — routing.recommend(...)           → deny if needs_human
4. path claims           — acquire, BEGIN IMMEDIATE         → deny, release
5. authorize             — dispatch_policy.assign(...)      → deny, release claims
6. resource leases       — acquire slot + classes           → deny, release
7. spawn lane detached   — Popen(start_new_session=True)
```

Steps 1–2 are free and reject most non-starters before any policy evaluation. Step 5 is
the existing frozen path and is called exactly as `dispatcher.run_task` calls it today —
in fact the lane child *is* `dispatcher.sh run --id X`, so step 5 happens inside the
lane and the controller's job reduces to: *choose, claim, spawn.*

That is deliberate. **The controller never touches the authority path.** It cannot
widen it, because it never calls it.

### 7.3 Idle classification — CASE 8

Evaluated in order; the first match wins:

| Reason | Condition | Notify |
| --- | --- | --- |
| `IDLE_ERROR` | `ready > 0`, capacity free, **no recorded denial** | **urgent** |
| `IDLE_POLICY` | `ready > 0`, every denial is quota/escalation/no-cheaper-worker | action |
| `IDLE_CAPACITY` | `ready > 0`, denials are all resource/lease | no (transient) |
| `IDLE_APPROVAL` | `ready = 0`, `awaiting_approval > 0` | action, batched |
| `IDLE_BLOCKED` | `ready = 0`, `blocked > 0` | action |
| `IDLE_PLANNING` | `ready = 0`, an open milestone has task budget left | no |
| `IDLE_LEGITIMATE` | no ready, no pending, no approval, every open milestone complete | info, once |

`IDLE_ERROR` is the state the brief describes. It is now a first-class, named,
notified, tested condition rather than an absence of activity.

### 7.4 [NEW] The doctor — the piece that actually makes the invariant true

Classifying idle from *recorded denials* is strictly weaker than it looks: it can only see
tasks the loop attempted. A task that is never attempted produces no denial and therefore no
evidence of its own undispatchability.

```python
def scan(store, workers):
    # Pure. Mints nothing, consumes nothing, writes nothing.
    out = []
    for task in store.tasks(status="ready"):
        verdicts = [
            dispatch_policy.evaluate_authorization(
                task, workers, store=store, quota=QuotaLedger(store),
                leases=dispatch_policy.lease_directory_for(store),
                history=store.attempt_history(task.id),
                available=wired_adapters(),        # what can ACTUALLY execute
            )
            for candidate in candidate_workers(task, workers)  # EVERY candidate, not the pick
        ]
        if all(not v.allowed for v in verdicts):
            out.append((task, [v.summary for v in verdicts]))  # verbatim failing check names
    return out
```

**A non-empty result is a FINDING**: non-zero exit, a notification, and the failing check
reported verbatim (`quota: codex/* quota status is EXHAUSTED`), never paraphrased.

This one check catches every sibling of the brief's complaint at once: the escalation
dead-zone where `escalation_floor` outruns the dispatcher's `available` narrowing; unbound
worktrees; projects absent from `dispatcher-targets.json`; the derived-approval mismatch
between `evaluate()` and `_ledger_objection`; and stale-`ready`-with-a-cancelled-dependency.

It must live in a **new unfrozen module** (`scripts/lib/doctor.py` + `scripts/doctor.sh`).
It cannot be a `control-plane.py` subcommand — that file is frozen at digest `f73fb7a4…`.

---

## 8. Task-generation model

```
  approved roadmap document (sha256 pinned at approval)
        │
        ▼
  milestone (approved=1, acceptance text written by David)
        │
        ▼
  planner worker  ──▶ strict JSON proposal  ──▶ raw_output_path (kept forever)
        │                                              │
        │                                              ▼
        │                         ┌──────────────────────────────────────┐
        │                         │  ADMISSION GATE — deterministic      │
        │                         │  Python. No model. No network.       │
        │                         └──────────────────────────────────────┘
        │                                    │              │
        │                              admitted          rejected
        │                                    │              │
        ▼                                    ▼              ▼
   planner_batches                  store.add_task()    proposals table
                                    + task_origin       (David promotes or declines)
```

### The gate — every rule is a refusal, none is a judgement

1. **Milestone binding.** `milestone_id` must equal the one requested. The planner
   cannot retarget its own output.
2. **Project binding.** `project` must equal the roadmap's project.
3. **Path scope.** `owns_paths ⊆ roadmap.allowed_paths`. Exact prefix containment.
4. **Risk is computed, never accepted.** Risk is derived from touched paths, capability
   set and verify profile. **A declared risk may only raise the computed value, never
   lower it.** (Mirrors the existing rule: *"Hermes decides what a classification costs,
   not the caller."*)
5. **Tier ceiling.** Required capabilities must be satisfiable at or below
   `roadmap.max_tier`. A task that can only run on Astra is not admissible under a
   roadmap approved for tier 6.
6. **Verifier binding.** `verify_profile` must exist in `config/verifiers.json`. A task
   with no verifier is admitted `approval_required=1` and never runs unattended.
7. **Budget.** `admitted_total(roadmap) < roadmap.task_budget`. Hard stop. Exhaustion
   sets `roadmap.status='exhausted'` and notifies once.
8. **Approval derivation.** Action class → `approval_required`, computed by the gate
   (§15). The planner's opinion is not consulted.
9. **Dedupe.** Normalized-description hash against all non-terminal tasks in the project.
10. **Structural validity.** Malformed JSON, unknown keys, missing fields → whole batch
    `malformed`, zero tasks, one notification. **Never partial trust of a malformed
    payload.**

Rules 1–3, 5 and 7 are the containment. **Nothing the planner writes can widen the
roadmap's scope, because the scope was frozen — with a document hash — at approval
time.**

### Worked example: the Agent Infra Guide roadmap

David approves a roadmap with `task_budget=40`, `max_tier=6`, `allowed_paths=content/`,
`data/`, `tools/`, and nine milestones (currentness, content depth, Evidence Layer,
benchmark harness, benchmark collection, machine-readable evidence, calculator
improvements, commercial validation, distribution).

- Milestone 3 "Evidence Layer" is `open`. Planner emits 6 candidate tasks.
- 5 admitted → `pending` → `evaluate()` → `ready`.
- 1 rejected: it proposes `infra/deploy.yml`, outside `allowed_paths` → `proposals`.
- Milestone 9 "distribution" contains "publish to Whop." The gate computes
  action class `publish` → `approval_required=1`. It is admitted as a real, ordered,
  dependency-correct task that will sit in `awaiting_approval`, be reported by the
  status API, and **be skipped by the scheduler while other milestones run.**
  **CASE 5, structurally.**

### Milestone completion is the one thing the planner may never decide

"All tasks succeeded" is *not* milestone completion, because the planner controls how
many tasks exist. Completion requires the `acceptance` text — written by David at
approval — to be verified, and it always notifies. Otherwise the planner can end a
milestone by emitting nothing, and "the roadmap is done" becomes the planner's opinion.

### Planning is itself a task

It consumes a lane, a budget entry and a routing decision. Otherwise planning is
invisible work of unbounded cost. Planner routes to **Sol (tier 3)** by default;
Opus 5 only for a *new roadmap*, which is a human-initiated act anyway.

---

## 9. Model / worker routing policy

`routing.py` is frozen, vendor-neutral, and already correct: it decides from
`cost_tier`, `capabilities` and `max_risk`, and `tests/test_routing.py` proves a
completely renamed fleet produces an identical decision. **V2 adds no routing code. It
adds registry rows and one budget counter.**

### Proposed `config/workers.conf` (config change only, no freeze impact)

| tier | worker | kind | metered | capabilities added | max_risk | use |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `qwen-coder-local` | local | no | `extraction, formatting, classification` | low | repetitive transforms, extraction, formatting, mechanical edits |
| 2 | `qwen3-local` | local | no | `analysis, review` | medium | low-risk analysis, first escalation, cheap review |
| 2 | `gemma-local` | local | no | `classification, extraction` | low | high-volume classification |
| **3** | **`sol`** | remote | yes | `planning, code_edit, analysis, review, orchestration` | medium | **the workhorse: normal planning, coordination, implementation, supervision** |
| 5 | `codex-gpt` | remote | yes | `computer_use, browser, terminal` | medium | repo/browser/terminal execution |
| 6 | `claude-opus-46` | remote | yes | `code_edit, refactor, docs, analysis` | medium | substantial repo implementation, large coherent coding/content jobs |
| 8 | `claude-opus-5` | remote | yes | `architecture, security, arbitration` | high | architecture, hard planning, adversarial review |
| 9 | `astra` | remote | yes | `final_review, architecture, research_synthesis` | high | **`enabled = false` until available** |
| 20 | `human-owner` | human | no | `approval` | high | terminal rung |

### The failure mode this creates, and its fix

Routing is *cheapest capable worker*. If task classes are described loosely, everything
will declare `analysis` — which qwen-coder-local lacks — and the whole queue lands on
Sol. Cost discipline then depends on prose.

**Fix: `config/task-classes.json` maps `task_type → exact required capability set`, and
the admission gate assigns capabilities from the class, not from the planner's text.**

```json
{
  "extract":        ["extraction"],
  "reformat":       ["formatting"],
  "classify":       ["classification"],
  "mechanical_edit":["code_edit"],
  "implement":      ["code_edit", "test_run"],
  "content_draft":  ["docs"],
  "content_deep":   ["docs", "analysis"],
  "analyze":        ["analysis"],
  "plan":           ["planning"],
  "repo_exec":      ["computer_use"],
  "architect":      ["architecture"],
  "adversarial":    ["architecture", "review"],
  "final_review":   ["final_review"]
}
```

A variable rename is `mechanical_edit`. It declares `code_edit`. qwen-coder-local has
`code_edit`. It routes to tier 1 and cannot route higher without an attributed failure.
**The class table is what actually enforces local-first — not the ladder, which only
enforces it once the class is honest.**

### Premium budget — honest counting, not fabricated metering

The repo refuses to estimate tokens, correctly: *"a fabricated remaining count that
drifts from the provider's real position would be worse than no number."* I am not
proposing to break that. I am proposing to count **the one thing we actually observe**:

```sql
CREATE TABLE dispatch_budget (
    provider      TEXT NOT NULL,
    day_utc       TEXT NOT NULL,
    dispatches    INTEGER NOT NULL DEFAULT 0,
    ceiling       INTEGER NOT NULL,
    PRIMARY KEY (provider, day_utc)
);
```

A dispatch to a metered provider is a fact Hermes performs. Counting our own actions is
not estimation. Exceeding the daily ceiling is `IDLE_POLICY` + one notification, not a
silent stop. This sits **beside** quota (permission) and does not replace it.

### Astra reservation

`enabled = false` plus `final_review` — a capability **no planner-generated task may
declare** (admission gate rule 5). Astra is reachable only by a task David creates. When
Astra becomes available, flipping `enabled = true` changes nothing on its own.

---

## 10. Concurrency and resource policy

### The honest ceiling

24 GB unified memory. A 27B model at q4 resident is ~17 GB. **Two local models cannot
co-reside, and a local model plus a substantial build cannot co-reside.** Any design
promising three parallel heavy lanes on this machine is lying.

### Resource classes

| Class | Capacity | Rationale |
| --- | --- | --- |
| `slot` | **2** | total concurrent lanes; raise to 3 only after M3 proves clean |
| `ollama` | **1** | a second resident 27B thrashes; model swap has real cost |
| `build:<repo>` | 1 per repo | |
| `build:heavy` | **1 global** | |
| `browser` | 1 | one automation session, period |
| `premium` | 2 | concurrent metered dispatches |
| `net_heavy` | 2 | |

**Mutual exclusions:** `ollama` ⊗ `build:heavy`. `browser` ⊗ `build:heavy`.

### Admission gates, checked every tick before dispatch

```
free_memory_pages (vm_stat: free + inactive + speculative) × page_size
    ≥ class_requirement + 3 GB headroom
load_average_5m < ncpu                          (for heavy classes)
free_disk > 5 GB                                (else: no dispatch at all, notify)
thermal_pressure != "critical"                  (pmset -g therm)
```

**CASE 7** is then a resource pre-check that never reaches routing, costs nothing, and
records `IDLE_CAPACITY` rather than an error — because it is not an error.

### Recommendation on concurrency, stated plainly

The brief says *"do not assume maximum concurrency is desirable."* I will go further:

> **Start at `slot = 1`.** The value of this system is that it never stops, not that it
> runs three things at once. One lane that runs continuously for 20 hours does far more
> than three lanes that corrupt a repository once. Raise to 2 only when the tasks are
> in genuinely different repositories, and only after §11's path claims have a clean
> record in M3.

---

## 11. Ownership and locking

Three levels, strongest first.

### Level 1 — worktree lease (exists; keep unchanged)

Atomic `os.symlink` under `state/leases/`, keyed by `(st_dev, st_ino)`, acquired inside
the assignment transaction, broken only when the holder is **dead and** the lease is
aged. This is the real exclusion primitive and it is correct.

### Level 2 — path claims (new)

Repo-relative prefixes, acquired under `BEGIN IMMEDIATE`, overlap checked in both
containment directions. A task declares `owns_paths`; a task that declares nothing
claims `/` and degrades exactly to today's behaviour.

### Level 3 — resource leases (new)

Named singletons with holder pid, boot id and expiry.

### The uncomfortable truth about intra-repo concurrency

**Git is repo-global.** `.git/index`, `HEAD`, `index.lock`, the ref store and the stash
are shared by every process in the checkout. Two tasks with perfectly disjoint path
claims will still collide on `git add`, `git commit`, `git checkout` and `git stash`.

So path claims **are not a concurrency enabler.** They are a *collision detector*. The
honest policy:

```
isolation = worktree   (default for anything that runs git write commands)
isolation = inplace    (permitted ONLY for read-only or non-git-writing tasks
                        with disjoint path claims)
```

One writer per repository remains the rule. Real intra-repo parallelism comes from
`git worktree`, which the system already understands. This pushes back on the brief's
implied hope, and it is the right answer.

### Enforcement — declarations must be checked, not trusted

At verification, `git status --porcelain` (minus `.gitignore` and the profile's
generated-file list) is compared against the claim. **A write outside the claim is a
verification failure**, not a warning. Without this, `owns_paths` is decoration.

---

## 12. Heartbeat and stall detection

### The principle

> **Liveness is claimed by the worker. Progress is observed by the controller.
> A stall is declared on the absence of *observed progress*, never on the absence of a
> *claim*.**

A heartbeat thread keeps beating while the model loops on the same thought. A heartbeat
alone proves the process has a scheduler slot. That is all it proves.

### [REVISED] Two rules the reconciliation added

**A heartbeat written on a timer by the supervising process detects nothing.** It is
definitionally redundant with `runner_identity_state() == "matched"`: it stops when the process
stops (already detected) and keeps beating through a total model hang (the only case worth
detecting). **A heartbeat is a new fact only if it is gated on observed worker progress.**

**Heartbeat staleness must never act alone.** The joint gate:

```
stale + gone          →  crashed           — existing finalize path handles it
stale + reused        →  crashed           — existing path; pid reuse correctly rejected
stale + indeterminate →  do nothing        — existing discipline; preserve it
stale + matched       →  THE ONLY NEW CASE — a live process making no progress
```

And: **in-run enforcement belongs in `dispatch_runner.py`, not in the 60 s tick.** Sampling
from outside on a 60 s cadence cannot bound a runaway between samples. `verifier_runner.py`
already implements the correct bounded-poll supervisor (deadline + cancellation check + SIGTERM
→ wait 5 → SIGKILL) and it can be lifted verbatim into `dispatch_runner.py`'s bare
`child.wait()`. Two layers, one owner: in-run enforcement in the runner, cross-run adjudication
in `recover()`.

### Worker side — `dispatch_runner.py` (not frozen)

A daemon thread writes `heartbeat.json` every 15 s via the file's existing
`atomic_json()` (tmp + `os.replace`):

```json
{"seq": 412, "at": "2026-09-17T18:22:31Z", "phase": "working",
 "tool_calls": 37, "last_tool": "edit_file", "bytes_out": 184203}
```

Costs ~30 lines. Works for any adapter, including ones wrapping a foreign CLI, because
the wrapper — not the model — writes it.

### Controller side — six independent signals

| Signal | Source | Defeats |
| --- | --- | --- |
| heartbeat seq + age | `heartbeat.json` | process death, total hang |
| cumulative CPU seconds | `ps -o time= -p` | blocked on a dead socket |
| stdout/stderr size | `stat` | silent model |
| tool-call count | worker journal | model talking, not acting |
| worktree max mtime + `git status` count | filesystem | thinking without producing |
| task-declared progress markers | task packet | content work with no git delta until the end |

### Verdict

```
INDETERMINATE  any sampling error (ps failure, unreadable heartbeat, negative age
               from a sleep/wake clock jump)         →  NEVER ACTS. Matches the
                                                        existing recover() discipline.
ACTIVE         heartbeat advanced AND at least one external signal advanced
QUIET          heartbeat advanced, no external signal advanced
STALLED        QUIET for N consecutive samples (N × 60 s ≥ class stall window)
            OR heartbeat age > 3 × interval while the pid is alive
            OR now > deadline_at                     (absolute ceiling, always fatal)
            OR pid gone with no result               (existing path)
```

### Windows by task class

| class | quiet window | wall-clock ceiling |
| --- | --- | --- |
| `mechanical_edit`, `extract`, `reformat` | 5 min | 20 min |
| `implement` (local) | 8 min | 45 min |
| `implement` (Claude/Codex, substantial) | 25 min | 120 min |
| `content_deep` | 20 min | 90 min |
| `repo_exec` / browser | 15 min | 60 min |
| verification phase | 10 min | 30 min |

**CASE 3** is a `STALLED` verdict after `ceil(window / 60)` consecutive `QUIET` samples.

---

## 13. Retry and recovery design

### The anti-loop rule the brief asks for

```sql
failure_fingerprint = sha256(failure_class ‖ exit_code ‖ normalize(last 2 KB of stderr))
```

> **A task is never retried at a tier that has already produced the same fingerprint.**

It must escalate or stop. This composes with the existing escalation policy, which
already collapses identical failures to one piece of evidence. Together they make "retry
the same broken action forever" unreachable: repetition neither buys a tier nor a retry.

### Recovery matrix

| Trigger | Detection | Action |
| --- | --- | --- |
| runner pid dies, result written | `recover()` reads result | finalize; existing |
| runner pid dies, no result | identity `not matched` | transient failure, lease released; existing |
| crash after assign, before journal | nonce not in journal | CAS orphan record; real launch wins; existing |
| **dispatcher killed, runner alive** | run `assigned`, start gate unopened | **existing** — recovery reopens the gate under revalidated authority |
| **live runner, no progress** | §12 STALLED | **new** — see below |
| **reboot mid-task** | `run.boot_id ≠ current` | **new** — definitionally dead regardless of pid; identity check also catches it |
| external worker identity lost | result file never appears → stall | **new** — redispatch; nothing depended on the identity |
| Ollama / provider unreachable | preflight failure | transient; circuit breaker |

### Stall resolution — the sequence

```
1. SIGTERM the runner's process group    (it is its own session leader)
2. wait 30 s   ── a model mid-`git commit` gets a chance to finish
3. SIGKILL the group; wait 5 s
4. PRESERVE the run directory ENTIRELY. Never delete evidence. Ever.
5. journal.finish(state='failed', failure_reason='stalled: no progress for {N}m')
6. store.record_failure(task, reason=...)   → existing escalation semantics
7. release path claims + resource leases; worktree lease released by the ledger
8. quarantine the worktree if `.git/index.lock` survives  ── never hand it to a
   second worker with a stale lock
```

### Is a stall transient or non-transient? — this decides whether stalls buy money

```
stall with ZERO tool calls and ZERO output      → TRANSIENT
    (it never got going; almost always infrastructure)
stall AFTER real progress                       → NON-TRANSIENT
    (a model that hangs mid-task should be replaced, not re-run)
```

**And a hard rule on top:** a task may **not** cross the metered boundary on stall
evidence alone. Escalation from a free tier to a paid tier requires at least one
attributed, non-stall failure. Otherwise a flaky local Ollama becomes a pump that
converts memory pressure into Claude spend. *(§24, R-8.)*

### Circuit breakers

Three consecutive infrastructure failures for one worker → that worker is skipped for
15 minutes, one notification. Without this, a dead Ollama grinds the entire queue to
`failed` in a single tick sequence.

### Transient retry budget

Transient failures correctly do not move the escalation floor — so they must have their
own bound, or an unreachable endpoint retries forever. Budget 3, exponential backoff
with jitter (60 s / 240 s / 900 s), then reclassified non-transient.

### Boot backoff

After a reboot, several tasks recover simultaneously and all retry at once. First tick
after a boot change dispatches **at most one** task. Prevents a recovery storm from
re-breaking whatever the reboot interrupted.

### Quarantine

A task that exhausts `max_attempts` across ≥ 2 tiers → `failed` + `holds.quarantined` +
one notification. **Never auto-reopened.**

---

## 14. Verification gates

Existing foundation is strong: shell-free argv, SHA-256 digests over the verifier
sources, verifier state written outside the writable target, strict terminal JSON
schema, nonzero verifier exit overrides a zero model exit.

### What V2 adds

**Profiles** — `config/verifiers.json`, keyed `(project, verify_profile)`:

```
code    : test_run (+count>0) · lint · typecheck · build · diff-scope · secret-scan
content : link-check · source-citation · frontmatter/SEO · claim-verification*
data    : schema validation · row-count delta bounds · null-rate bounds
deploy  : HTTP status · expected-content assertion · error-log delta · rollback-ready
```

`*` claim-verification is high-risk and therefore already requires an independent
reviewer under the existing rules.

### The new gate condition

```
succeeded  requires  ALL of:
   (a) ≥ 1 work-evidence row for THIS attempt          [exists]
   (b) verifier exit 0                                  [exists]
   (c) diff scope ⊆ declared path claim                 [NEW — §11]
   (d) at least one POSITIVE assertion in the verifier  [NEW — below]
   (e) independent review, if risk = high               [exists]
```

### (d) — vacuous verification

A test command matching zero tests exits 0. A link checker with zero links exits 0. A
build of an unchanged tree exits 0.

> **Exit 0 is necessary and never sufficient. Every profile must assert a positive
> signal:** test count > 0 and > 0 tests touching the claimed paths; build artifact
> mtime advanced; HTTP body contains an expected token; link checker examined ≥ 1 link.

The repo already has this instinct — *"Exit zero with only described commands is a
failure, not completion."* This generalizes it to every profile. Without (d), the
cheapest path to "completed" is a task that does nothing.

---

## 15. Approval gates

### Approval is computed, never declared

`config/approval-policy.json` maps action classes → approval requirement. The admission
gate computes `approval_required` from four inputs, and **the description keyword screen
is additive only — it can force approval, it can never remove one**:

| Input | Forces approval when |
| --- | --- |
| touched paths | `infra/`, `.github/workflows/`, `deploy/`, `*credential*`, `*.env*`, `secrets/` |
| verify profile | `deploy` |
| capabilities | `security`, `computer_use` + external target, `final_review` |
| description screen | publish, deploy, purchase, subscribe, announce, delete, drop, migrate, rotate, revoke, make public, go live |

### Always approval-gated

spending money · publishing publicly · changing visibility · ads · external
announcements · destructive actions · deleting important data · production migrations ·
credential/security changes · **any push to a default branch** · **any new milestone**

### Never interrupt for

code edits · tests · lint · typecheck · local commits to a feature branch · research ·
drafts · reversible local changes · routine failure recovery · retries · escalation
within an approved roadmap's tier ceiling · planner runs within budget

### Approvals expire — a genuine accidental-publication vector

An approval granted three weeks ago for "publish the Whop product" should not fire after
the roadmap changed underneath it. `approval_expires_at` (default 7 days, 24 h for
`publish`/`spend`). An expired approval is a **dispatch-time refusal + hold**, not a
ledger transition — because `TRANSITIONS` has no `ready → awaiting_approval` edge and
adding one means editing a frozen file. The controller simply declines and explains.

This composes with `eaa9124` ("revoke approval when worktree binding changes"), which
already invalidates an approval whose target moved.

### Approval UX

```sh
hermesctl approvals                      # one screen, grouped by reason
hermesctl approve T-118 --for 24h        # single-use, TTL'd
hermesctl decline T-118 --reason "..."   # → cancelled, with a reason
```

Approvals are batched into **one** notification digest, at most every 30 minutes.
Twelve separate approval banners is how a human learns to ignore the channel.

---

## 16. Security model

### Boundary 0 must not be reopened by accident

`docs/THREAT_MODEL.md` names the automatic reopening conditions: *filesystem or source
tampering; remote dispatch; a second UID; container or CI execution; or any other
distinct authority domain.* V2 touches two of these, so:

- **No long-lived daemon holding a `TaskStore`** (§4). Ticks only.
- **The status API must not be a network listener.** A loopback HTTP server is "an
  externally reachable authority path" under tripwire **T1**. `status.json` + a CLI
  instead (§18). If David later wants remote status, that is a separate, explicitly
  reviewed decision — it is in the Astra pack, not smuggled in here.
- Remote *dispatch* (sending work to Claude/Codex) is **not** remote authority: the
  authority decision stays local and the provider is a callee. Worth stating explicitly
  in `AGENTS.md` so a future reader does not reopen Boundary 0 over an adapter.

### Credential scoping — least privilege, per task

```
macOS Keychain  ──▶  broker (runs as the tick, never as the worker)
                          │
                          │ only the keys this task's capabilities map to
                          ▼
                     inherited fd / 0600 file OUTSIDE the worktree
                          │  worker reads once, unlinks
                          ▼
                     worker process environment
```

**Rules:**
1. Secrets never enter `paths.spec`. **(F-12 — this is the leak the current design will
   grow the moment a second adapter lands.)** The spec is JSON on disk for the life of
   the run and beyond.
2. Secrets never enter the worktree, the run directory, stdout, stderr, or any
   notification.
3. `config/credential-scopes.json` maps capability → key names. A task with
   `code_edit` gets no publishing token. A content task gets no deploy key.
4. A deterministic redactor runs over stdout/stderr before **any** status output or
   notification. The raw run directory stays 0700.
5. **Third-party CLI adapters are a partial defeat of this.** `claude` and `codex` read
   their own config files and hold their own credentials; Hermes cannot scope what it
   does not inject. The honest mitigations are a dedicated macOS user account or a
   per-adapter `HOME`, and both reopen Boundary 0. **This is the sharpest unresolved
   security question in the design — Astra pack, Q7.**

### Prompt injection

The governing rule:

> **Nothing a worker reads may change what a worker is allowed to do.**
> Task identity, path claims, verifier argv, approval state and tier all come from the
> ledger and orchestrator-owned config. None is derived from model output, repository
> content, or fetched web content.

The planner is the one component that reads content and emits structure — and its output
passes through a deterministic gate with no model in it. **A successful injection into
the planner yields a rejected batch.** That is the whole point of the gate.

Residual: the planner reads a repository that previous workers wrote. A worker could
plant text to influence a later planning round. Mitigation: the planner's input set is
an explicit allowlist (the roadmap document, `docs/`, task history), never the full
working tree.

### Repository trust

The Growth OS boundary generalizes: **a checkout under automation is data.** Never
import it, never execute its hooks, never use a repo-provided script as a verifier.
Verifiers are orchestrator-owned and digest-pinned — already true, keep it true for
every new profile.

### Git configuration

- Workers push only to `hermes/*` branches. Any push to a default branch is an
  approval-gated action class.
- `git config --local` in a worktree is worker-writable, so `core.hooksPath` is
  worker-controllable. Verification must run `git -c core.hooksPath=/dev/null`.
- Worker git identity is distinct (`hermes-worker@localhost`) so provenance is legible
  in `git log`.

### Local model trust

Loopback Ollama is not "trusted" — it is *cheap*. Its output is untrusted input to a
deterministic gate, identically to a frontier model's. No policy branch anywhere asks
which vendor produced a string. `tests/test_routing.py` already enforces the analogous
property for routing; extend it to the admission gate.

---

## 17. Reboot persistence

Two LaunchAgents in `~/Library/LaunchAgents/`. Both deliberately dumb.

### `com.hermes.tick.plist`

```xml
<key>ProgramArguments</key>
<array>
  <string>/Users/david/Projects/hermes-orchestrator/scripts/hermes-tick.sh</string>
</array>
<key>RunAtLoad</key>        <true/>
<key>StartInterval</key>    <integer>60</integer>
<key>ProcessType</key>      <string>Background</string>
<key>StandardOutPath</key>  <string>/Users/david/Projects/hermes-orchestrator/logs/tick.out</string>
<key>StandardErrorPath</key><string>/Users/david/Projects/hermes-orchestrator/logs/tick.err</string>
<key>EnvironmentVariables</key>
<dict><key>PATH</key><string>/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
```

**Not `KeepAlive`** — the tick is supposed to exit. `KeepAlive` on a fast-exiting program
produces a throttled restart loop and obscures real failures.

`StartInterval` will start a second tick while the first still runs. That is exactly why
the machine lock exists: the second exits 0 with `outcome='locked_out'`.

### `com.hermes.watchdog.plist` — `StartInterval 900`

One job: *"was there a `controller_ticks` row in the last 5 minutes?"* If not, notify.

This is the component that catches **the controller itself silently stopping** — the
brief's original complaint, one level up. Without it, an unloaded LaunchAgent or a
permanently-held lock reproduces the exact symptom V2 exists to fix, and nothing
notices. It must not share code with the tick, so a bug in the tick cannot silence it.

### Boot sequence

```
boot → launchd loads agents → RunAtLoad fires tick #1
     → machine lock acquired (stale lock from the old boot broken: boot_id differs)
     → recover(): journal ↔ ledger reconciled both directions
     → every run whose boot_id ≠ current boot is definitionally dead
     → stale worktree/resource/path leases released
     → evaluate() rebuilds eligibility
     → boot backoff: dispatch at most ONE task this tick
     → normal operation from tick #2
```

**CASE 4.** Note again: `recover()` already does the hard part. V2 supplies `RunAtLoad`.

### Login vs boot

A LaunchAgent runs at user login, not at boot. On a headless always-on Mac mini,
enable auto-login, or use a LaunchDaemon — but a LaunchDaemon runs as root, which is
**a second UID and reopens Boundary 0.** Recommendation: **LaunchAgent + auto-login +
`caffeinate -s`**, and disable "put hard disks to sleep."

---

## 18. Status API design

### Transport

1. `state/status.json` — rewritten atomically (`tmp` + `os.replace`) at the end of every
   tick.
2. `hermesctl status [--json]` — reads the ledger and operations DB live.

No network listener (§16, tripwire T1). Any local tool — ChatGPT via a terminal or
Codex, a shell alias, a Shortcut — can read the file.

### The freshness contract

Every response carries:

```json
{"generated_at": "2026-09-17T18:24:03Z", "tick_age_s": 41, "stale": false,
 "controller": "healthy"}
```

> **If `tick_age_s > 180`, the consumer must treat the entire document as UNKNOWN, not
> as current state.**

This is what makes the API report *actual* state. A status file is written by a process
that may have died immediately afterwards; without a freshness marker the last good
snapshot is indistinguishable from live truth, and that is precisely how "everything
looks fine" while the Mac is idle.

### Output — exactly the shape the brief asks for

```
CONTROLLER   healthy · tick 41s ago · lanes 1/2 · boot 6d 04h

ACTIVE
  T-118  AIG Evidence Layer schema
         worker claude-opus-46 · run 18m · heartbeat 23s ago · phase working
         files changed 4 · tool calls 37 · cpu 11m · ACTIVE
         verification pending (profile: code)

QUEUED
  T-119  AIG benchmark harness          blocked by T-118
  T-121  AIG benchmark collection       blocked by T-119
  T-124  AIG calculator rounding fix    eligible · routes to qwen-coder-local (tier 1)

WAITING APPROVAL
  T-131  Publish Whop product           reason: approval_publish
         raised 2d 04h ago · notified 2d 04h ago · never dispatched

HOLDS
  T-140  Meta ads spend                 reason: credential_missing (META_ADS_TOKEN)

IDLE ERROR
  (absent — work is running)
```

and when it is genuinely wrong:

```
IDLE ERROR
  3 eligible tasks exist, 2 lanes free, no denial recorded in the last 4 ticks.
  Last successful dispatch: T-117, 1h 12m ago.  →  urgent notification sent 3 ticks ago.
```

### Machine shape

```json
{
  "generated_at": "...", "tick_age_s": 41, "stale": false,
  "controller": {"state": "healthy", "lanes_used": 1, "lanes_total": 2,
                 "last_dispatch_at": "...", "idle_reason": ""},
  "active": [{"task_id": "T-118", "title": "...", "worker": "claude-opus-46",
              "run_seconds": 1080, "heartbeat_age_s": 23, "phase": "working",
              "activity": "ACTIVE", "files_changed": 4, "tool_calls": 37,
              "verification": "pending", "deadline_at": "..."}],
  "queued": [...], "waiting_approval": [...], "holds": [...],
  "milestones": [{"id": "M-3", "title": "Evidence Layer", "status": "open",
                  "tasks_total": 6, "tasks_done": 1, "budget_left": 11}],
  "errors": []
}
```

Every field is a durable fact with a timestamp. Nothing is inferred from a conversation.

---

## 19. Notification design

### Notify

| Event | Urgency | Batched |
| --- | --- | --- |
| approval required | action | yes, 30 min digest |
| credential missing | action | yes |
| task failed after exhausting attempts across ≥2 tiers | action | no |
| stall recovery failed (worktree quarantined, lock survives) | **urgent** | no |
| `IDLE_ERROR` | **urgent** | no, but rate-limited to 1/hour |
| `IDLE_POLICY` (quota exhausted / daily ceiling hit) | action | yes |
| milestone complete | info | no |
| roadmap task budget exhausted | action | no |
| controller has not ticked in 5 min (watchdog) | **urgent** | no |
| planner batch rejected / malformed | info | yes |
| disk below 5 GB | action | no |

### Do not notify

Task started · task succeeded · routine retry · transient failure within budget ·
escalation within an approved ceiling · `IDLE_CAPACITY` · lock contention · planner
admitted cleanly.

### Delivery

1. `notifications` table (durable, deduped, acknowledgeable).
2. macOS Notification Center via `osascript` — local, zero egress.
3. `state/NOTIFY.md` — a plain inbox, newest first, that any tool can read.
4. *Optional, opt-in:* an `ntfy`/Pushover topic for phone delivery. **This is outbound
   network egress from the orchestrator and must be a separate, explicit config
   decision** — not a default. Body is redacted and contains task IDs, never content.

### Discipline

- `dedupe_key` + a partial unique index: one open notification per condition, ever.
- Unacknowledged `urgent` re-notifies at +1 h and +4 h, then stops and stays visible in
  status. An alert channel that repeats forever gets muted, and a muted channel is worse
  than no channel.
- Max one notification per kind per 30 min, globally.

---

## 20. [REVISED] Migration path

**Superseded by `RECONCILIATION.md` §15**, which sequences three implementations rather than
one. The milestone list below stands, with one structural change: **R0–R2 are prerequisites,
not preliminaries.** All five invariant attacks pass through them, and shipping a scheduler
before they land ships a machine that wedges silently and faster.

```
R0  FIX B-1        3-line filter fix + per-iteration try/except + regression test  [unfrozen]
R1  FIX B-2        de-scope recover() pass 2 from LOCAL_WORKER                     [unfrozen]
R2  THE DOCTOR     dispatchability scanned with the SAME predicate that gates      [new module]
                   execution; non-empty undispatchable set = FINDING + notify
--- everything below is the original M0..M6, renumbered ---
R3  = M0 + M1      observability, then the tick with dispatch_enabled=false
R4  = M2           enable dispatch, one lane, qwen-coder-local, pilot worktrees
R5  = M3 + liveness  heartbeat-on-progress, in-run bounded poll, stall reaper, ownership
R6  DONATE MAIN    task-classes.json, DETERMINISTIC_TASK_TYPES, structured verdict,
                   project_registry  →  then DELETE main's orchestration stack
R7  ABSORB f2d64d9 answer RECONCILIATION.md §16  →  donate loop policy  →  delete branch (3)
R8  COLLAPSE DB    dispatcher.db → control-plane.db schema v9   [FROZEN EDIT — own review]
R9  = M4 + M5 + M6 adapters, planner, verification profiles
```

**Frozen-file impact is unchanged for R0–R7: zero.** R8 is the one deliberate frozen edit, and
it is sequenced last on purpose — see `RECONCILIATION.md` §10 for why the two-database
collapse is the right end state and the wrong first move.

## 21. Acceptance-test suite

Written in the repo's existing style (plain `unittest`, run from `scripts/test.sh`).
The eight brief cases plus twelve that the brief does not name and that I believe are
more likely to bite.

### The brief's cases

```python
# tests/test_controller_acceptance.py

class Case1_NextTaskStartsAutomatically:
    # 5 ready AIG tasks; finish one; assert the very next tick dispatches another,
    # and that lanes_used returns to capacity without human input.

class Case2_LostWorkerIdentityDoesNotLoseTheTask:
    # Dispatch an external worker; destroy external_ref and the chat session;
    # assert task stays `assigned` with a spent authorization, the stall detector
    # fires on schedule, and a redispatch succeeds on a different path.
    # Plus the AST assertion that no policy module reads external_ref.

class Case3_LiveButStalledIsDetected:
    # Runner alive, heartbeat advancing, zero tool calls / stdout / mtime change.
    # Assert: QUIET × N → STALLED → SIGTERM → grace → SIGKILL → run dir preserved
    #         → failure recorded → lease released → task re-eligible.

class Case4_RebootMidTask:
    # Simulate: change boot_id, kill runners without results, restart tick.
    # Assert reconciliation, no duplicate evidence, no double-spent authorization,
    # and AT MOST ONE dispatch on the first post-boot tick.

class Case5_ApprovalGatedTaskDoesNotStopTheQueue:
    # Highest-priority ready task requires publish approval; a lower-priority
    # independent task is eligible.
    # Assert: the approval task is NOT dispatched, the other one IS, in the SAME tick,
    # and idle_reason is empty because work is running.
    #   ◀◀ the single most important test in the suite.

class Case6_PathCollisionPrevented:
    # Two tasks, overlapping owns_paths, same repo.
    # Assert the second is denied at claim time, before authorization,
    # and that a worker writing outside its claim FAILS VERIFICATION.

class Case7_ExpensiveWorkloadNotStackedOnExpensive:
    # A build holds build:heavy; a local-model task needs `ollama` (mutually exclusive).
    # Assert: not dispatched, idle_reason = IDLE_CAPACITY, NO notification,
    # and it dispatches on the tick after the build finishes.

class Case8_LegitimateIdleIsDistinguishable:
    # All approved work complete.
    # Assert idle_reason = IDLE_LEGITIMATE, exactly one info notification,
    # and that injecting one ready task flips it to a dispatch on the next tick.
```

### The cases the brief does not name

```python
class FailedRunIsReconcilableAfterCrash:          # [NEW] B-1 regression
    # Commit a journal terminal 'failed', raise before _reconcile_journal_terminal,
    # reopen from fresh connections, assert recover() returns the task to 'ready'
    # with attempts incremented AND the worktree lease released.
    # Companion: one unreconcilable row must not stop a second wedged run in the sweep.

class RecoveryIsNotScopedToOneWorker:             # [NEW] B-2 regression
    # Assign to qwen3-local, kill the runner, assert recover() reclaims it.
    # Property form: for EVERY worker id in config/workers.conf, a dead runner is reclaimed.

class TickPromotesPendingItself:                  # [NEW] B-3 regression
    # add_task -> tick -> assert the task reached 'ready' with no human verb invoked.

class HealthPredicateEqualsExecutionPredicate:    # [NEW] B-4 regression
    # For every ready task the doctor calls undispatchable, status MUST report a finding.
    # Property test: there is no ledger state where execution denies and health says HEALTHY.

class ControllerAlwaysExplainsIdle:
    # Property test over generated ledger states: NO tick may ever end with
    # dispatched=0 AND running=0 AND idle_reason=''.

class HeadOfLineBlockingIsImpossible:
    # N undispatchable tasks ahead of one dispatchable task, for every denial
    # reason in the enum. The dispatchable task must start in that same tick.

class StallDoesNotBuyAPaidTier:
    # Repeated stalls with no attributed non-stall failure must never raise the
    # escalation floor across the metered boundary.

class IdenticalFailureIsNotRetriedAtTheSameTier:
    # Same failure_fingerprint twice → escalate or stop. Never a third identical run.

class MachineLockIsStaleBreakable:
    # Lock held by a dead pid from a previous boot → broken. Held by a live pid →
    # never broken. Unreadable → never broken.

class WatchdogFiresWhenTheTickDies:
    # No controller_ticks row for 5 minutes → urgent notification.

class VacuousVerificationFails:
    # Test command matching zero tests exits 0 → task must NOT reach succeeded.

class PlannerCannotWidenScope:
    # Adversarial planner output: retargeted milestone, out-of-scope paths,
    # lowered risk, capability escalation, duplicate tasks, budget overrun,
    # prompt-injection payloads in every string field.
    # Assert: zero admitted tasks, batch recorded, proposals created, no exception.

class SecretsNeverReachTheSpecFile:
    # AST + runtime: no credential-scoped value appears in paths.spec, stdout,
    # stderr, status.json, or any notification body.

class ApprovalExpiryIsEnforcedAtDispatch:
    # Approve, advance the clock past the TTL, tick → refusal + hold,
    # and specifically NOT a ledger transition.

class RecoveryStormIsBounded:
    # 10 tasks recovered on one boot → at most one dispatch that tick.

class IndeterminateNeverActs:
    # ps failure, unreadable heartbeat, negative clock delta → no kill, no
    # failure record, no lease release. (Mirrors the existing recover() rule.)
```

---

## 22. Implementation sequence

Estimates assume Claude Opus 4.6 doing the implementation with David reviewing, which
is what this system is for.

| # | Work | New LOC | Frozen? | Closes |
| --- | --- | --- | --- | --- |
| **0a** | **[NEW] Fix B-1 — reconcile `failed` runs (both sites) + per-iteration try/except + tests** | **60** | **no** | **the permanent wedge** |
| **0b** | **[NEW] Fix B-2 — de-scope `recover()` pass 2 from `LOCAL_WORKER`** | **40** | **no** | **orphaned non-qwen assignments** |
| **0c** | **[NEW] The doctor — `evaluate_authorization` dry-run over the ready queue** | **120** | **no** | **B-4, silent idle** |
| 1 | Operations-DB migrations (additive ALTERs + new tables) | 150 | no | — |
| 2 | Heartbeat in `dispatch_runner.py` | 40 | no | F-3 |
| 3 | `liveness.py` — six signals + verdict | 220 | no | F-3, CASE 3 |
| 4 | `hermesctl status --json` + `status.json` | 250 | no | F-10, CASE 8 |
| 5 | Machine lock (reuse the install-lock primitive) | 90 | no | — |
| 6 | `controller.py` tick, **`dispatch_enabled=false`** | 400 | no | F-1, F-6, F-11 |
| 7 | Two LaunchAgents + `hermes-tick.sh` + watchdog | 120 | no | F-2, CASE 4 |
| 8 | **Enable dispatch, local lane only** | 20 | no | **CASE 1** |
| 9 | Stall resolution + fingerprints + circuit breakers | 260 | no | CASE 3 |
| 10 | Resource classes + RAM/disk/load gates | 200 | no | F-7, CASE 7 |
| 11 | Path claims + diff-scope verification | 230 | no | F-8, CASE 6 |
| 12 | Notifications (table, dedupe, osascript, NOTIFY.md) | 180 | no | F-9 |
| 13 | Credential broker + redactor | 200 | no | F-12 |
| 14 | Adapters: Claude / Sol / Codex / CoS | 500 | **no**\* | F-4, CASE 2 |
| 15 | Roadmap + milestone + `hermesctl roadmap` | 240 | no | F-5 |
| 16 | Planner + admission gate + proposals | 450 | no | F-5 |
| 17 | Verifier profiles + positive assertions | 300 | no | — |
| 18 | Acceptance suite (§21) | 900 | no | — |
| | **Total** | **≈ 4,750** | | |

\* `workers.conf` is config, not a frozen module. If an adapter appears to require a
change to `routing.py` or `dispatch_policy.py`, stop and re-examine — those modules are
already vendor-neutral by test.

**[REVISED] Steps 0a–0c come first and are non-negotiable.** They are 220 lines, touch no
frozen file, and are independently valuable whatever the unreadable branch contains. Without
0a a crash wedges a task forever; without 0b widening dispatch orphans everything that is not
qwen; without 0c the wedge is invisible and the system reports HEALTHY.

**Order is load-bearing.** Steps 0a–8 close the brief's primary complaint in roughly
1,490 lines. Everything after is hardening and reach. Do not reorder 16 before 8: a
planner that generates work for a system that cannot start it makes the idle-Mac problem
worse, not better.

---

## 23. What NOT to build

| Don't | Why |
| --- | --- |
| **A long-lived supervisor daemon** | Reopens Boundary 0 by holding a cached `TaskStore`; adds a failure class (the daemon dies) that the tick model does not have |
| **A network status API** | Tripwire T1. Reopens Boundary 0 for a convenience a file provides |
| **New ledger states or transition edges** | Every edge is a bypass to review; the freeze exists for good reasons; holds + refusals cover every case in the brief |
| **Token metering / spend estimation** | The repo's refusal is correct. Count dispatches — a fact we observe — not tokens we guess |
| **Chat-identity tracking as execution state** | This is the WORKER_IDENTITY_LOST root cause. The filesystem is the rendezvous |
| **Redis, Celery, RabbitMQ, k8s, Temporal, Airflow, Prefect** | SQLite + launchd is sufficient for one machine and two orders of magnitude more tasks than exist |
| **A web dashboard** | `status.json` + a CLI. A dashboard is a second source of truth that drifts |
| **Retry-until-success** | Fingerprint dedupe + bounded attempts + escalation. Unbounded retry is how autonomy becomes expensive |
| **Auto-merge to a default branch** | Approval-gated, permanently |
| **Asking the model whether it finished** | Verification exists precisely so this question is never asked |
| **Unbounded planner** | Budget, path scope, tier ceiling, document hash |
| **Multi-machine / distributed** | One Mac. Adding a second machine reopens Boundary 0 wholesale |
| **Rewriting the authority surface** | It is the best part of the system. V2 is drive, not policy |
| **[NEW] A scheduler inside `durable_workflow.py`** | That is the second scheduler. Its loop policy is donated into one unfrozen `scheduler.py`; the engine is retired |
| **[NEW] A second permission system** | Everything except `taskstore.grant_approval` is **DENY-ONLY**: it may refuse, and force `approval_required=True` at admission, but may never record, cache, satisfy or substitute a grant |
| **[NEW] `main`'s `model_router.py` as a router** | Worker selection *is* model selection. Keep its `TASK_TYPES` table, retire the engine |
| **[NEW] A heartbeat on a supervisor timer** | Definitionally redundant with `runner_identity_state == "matched"`. Only a progress-gated heartbeat is a new fact |
| **Worktrees for every task** | Expensive, and read-only/analysis tasks do not need them |
| **A second orchestrator** | Explicitly what the brief asked to avoid, and correctly |

---

## 24. [REVISED] Risks and edge cases

Adversarial. Ordered by *how quietly this fails*. **R-0a…R-0d are new, are live on the Mac
today, and were verified against the source — they are not hypotheticals.**

**R-0a — A failed run wedges its task and its worktree forever. [B-1, VERIFIED]**
`recover()` filters `{"succeeded","cancelled"}` at `dispatcher.py:1884` and `:1666`. The repair
function accepts `"failed"` and is unreachable. Crash between the two database commits ⇒ task
permanently `assigned`, lease held by a dead pid, `recover` returns 0 forever. Traverses the
most common terminal path in the system.
*Mitigation:* implementation step 0a. **Prerequisite for the loop.**

**R-0b — Widening dispatch orphans everything that is not qwen. [B-2, VERIFIED]**
`recover()` pass 2 skips `task.assigned_worker != LOCAL_WORKER`. Every migration widens
dispatch before recovery, so this gap opens by default.
*Mitigation:* step 0b, landed **before** anything new calls `dispatch_policy.assign`.

**R-0c — A tick on `dispatcher.sh` verbs sees an empty queue forever. [B-3, VERIFIED]**
`add_task` hardcodes `'pending'`; `evaluate()`'s only production caller is the frozen,
human-typed `control-plane.py:138`; `dispatcher.py` references it zero times.
*Mitigation:* the tick calls `store.evaluate()` itself from unfrozen code (§7).

**R-0d — HEALTHY is computed by a weaker predicate than execution. [B-4, VERIFIED]**
`findings = failed + blocked + awaiting_approval`; `ready` and `assigned` are not terms. A
100%-undispatchable queue and a wedged task both exit HEALTHY.
*Mitigation:* the doctor (§7.4). **This is the mitigation that makes "silently" false.**

**R-0e — Three databases, three clocks, no alarm on divergence.**
If `state/durable-workflows.db` carries a status column read as a precondition to advance, the
one component with a clock gates on columns the authority cannot write, and R-0d guarantees
nobody is told.
*Mitigation:* `RECONCILIATION.md` §16 Q1 and Q2 settle it; the engine is retired either way.

**R-1 — Head-of-line blocking silently reproduces the original bug.**
The natural implementation picks the top candidate, hits a denial, and returns. The Mac
idles with 5 eligible tasks. This is the highest-probability regression in the whole
design, because the broken version looks correct and passes a single-task test.
*Mitigation:* `continue`, never `break`, on a per-task denial (§7); `HeadOfLineBlockingIsImpossible`
tests every denial reason with a dispatchable task behind it.

**R-2 — The machine lock is never released.**
A tick killed with SIGKILL between lock acquisition and cleanup leaves a symlink. Every
subsequent tick exits `locked_out`. The system is now permanently, silently stopped —
*exactly the original complaint, one level up.*
*Mitigation:* pid + boot_id + age stale-break (same discipline as worktree leases, which
already get this right); `outcome='locked_out'` for >5 consecutive ticks is an urgent
notification; the watchdog is independent code.

**R-3 — `idle_reason` defaults to empty.**
A missed branch in `classify_idle` produces `''`, which reads as "fine."
*Mitigation:* the §5.3 invariant, tested as a property. A missing reason must be a loud
`IDLE_ERROR`, never a quiet blank.

**R-4 — Heartbeat theater.**
The heartbeat thread is healthiest exactly when the model is stuck, because a looping
model still schedules threads.
*Mitigation:* ACTIVE requires an *external* signal. Heartbeat alone yields QUIET.

**R-5 — Stall kill corrupts a repository.**
`killpg` during `git commit` leaves `.git/index.lock`; the next worker fails
mysteriously, or worse, a partial index is committed.
*Mitigation:* SIGTERM → 30 s grace → SIGKILL; post-kill `index.lock` check; **quarantine
the worktree** rather than reusing it; verification runs with `-c core.hooksPath=/dev/null`.

**R-6 — Diff-scope false positives.**
Builds write into claimed prefixes; `node_modules`, `.next`, `__pycache__`, lockfiles.
Every task then "fails verification" and the queue converts into failures.
*Mitigation:* per-profile generated-path ignore lists derived from `.gitignore`; ship
scope checking in **report-only** mode for two weeks before it gates.

**R-7 — Approval laundering through description text.**
The planner writes "update the distribution configuration" for "publish to Whop."
*Mitigation:* the keyword screen is additive only. The real gate is computed from paths,
capabilities and verify profile — and **declared risk may only raise, never lower**.

**R-8 — Stall inflation buys premium tiers.**
Memory pressure makes local Ollama hang. Each stall is a non-transient failure. The floor
climbs. Within a day everything routes to Claude and the bill is an infrastructure
problem wearing a routing costume.
*Mitigation:* the transient/non-transient stall rule (§13); circuit breakers; **crossing
the metered boundary requires ≥1 attributed non-stall failure**; the daily dispatch
ceiling is the backstop.

**R-9 — Intra-repo concurrency corrupts git state.**
Disjoint path claims do not make `git` concurrent — `.git/index` is shared.
*Mitigation:* `isolation=worktree` for anything that writes via git; `inplace` only for
non-git-writing tasks; **one writer per repo stays the rule** (§11).

**R-10 — Recovery storm after reboot.**
Ten recovered tasks all retry at once, on a machine still starting up.
*Mitigation:* boot backoff — at most one dispatch on the first post-boot tick.

**R-11 — Run-directory disk exhaustion.**
Every run keeps stdout, stderr, spec, state, result forever. A year of continuous
autonomy fills the disk, and SQLite writes fail — which fails *closed*, but noisily and
at the worst time.
*Mitigation:* retention (keep all failures + last 200 successes), GC on tick, refuse all
dispatch below 5 GB free with an action notification.

**R-12 — Clock jumps across sleep/wake.**
Negative ages, absurd durations, a stall declared because the clock moved.
*Mitigation:* the repo's strict UTC `%Y-%m-%dT%H:%M:%SZ` discipline; **any negative
delta is INDETERMINATE**, never a stall.

**R-13 — Ollama model-swap thrash.**
Alternating tiers 1 and 2 evicts and reloads a 17 GB model every task.
*Mitigation:* `ollama` capacity 1; prefer batching same-model tasks within a tick; tune
`keep_alive`.

**R-14 — Verification passes vacuously.**
The cheapest route to "completed" is a task that changes nothing.
*Mitigation:* positive assertions (§14(d)).

**R-15 — `status.json` torn read.**
ChatGPT reads mid-write and gets truncated JSON.
*Mitigation:* atomic rename (the repo's `atomic_json` already does this correctly).

**R-16 — Milestone completion becomes the planner's opinion.**
*Mitigation:* completion requires verifying the human-written `acceptance`, and always
notifies (§8).

**R-17 — Third-party CLI adapters hold their own credentials.**
Hermes cannot scope what it does not inject. `claude` and `codex` read their own config.
*Mitigation:* per-adapter `HOME`, or a dedicated macOS user — **both of which reopen
Boundary 0.** Genuinely unresolved; Astra Q7.

**R-18 — `store.evaluate()` scaling.**
O(tasks × dependencies) with a query per dependency, run every tick. Fine at hundreds;
a real tick cost at ten thousand. It is in a frozen file, so the fix is a controller-side
cache, not an edit. Known limit, not a defect.

**R-19 — Two roadmaps claiming the same paths.**
Nothing prevents two approved roadmaps from targeting `content/`. They will serialize on
the worktree lease, which is safe but invisible.
*Mitigation:* warn at roadmap approval when `allowed_paths` overlap a live roadmap.

**R-20 — The controller succeeds and David stops reading notifications.**
The real long-term failure mode. A system that works produces a channel that gets muted,
and then a genuine urgent alert is invisible.
*Mitigation:* strict notification discipline (§19) — routine success is *never* a
notification. The channel's value is entirely in its silence.
