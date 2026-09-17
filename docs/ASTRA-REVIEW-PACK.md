# ASTRA REVIEW PACK — Hermes V2 controller

For GPT-6 Astra, when available. Final architecture / adversarial review.

**Read first:** `docs/V2-CONTROLLER-ARCHITECTURE.md`, then in the
`DRLXAI/hermes-orchestrator` repository: `AGENTS.md`, `docs/THREAT_MODEL.md`
(Boundary 0), `REVIEW-8-BLOCK.md`, `config/control-plane-freeze.sha256`.

**Context in one paragraph.** Hermes V1 is a rigorously reviewed *authorization* layer
with no scheduler: `dispatcher.sh run --id <task>` requires a human to name the task, so
the machine idles whenever David stops typing. V2 adds a tick-based controller (launchd
`StartInterval 60`, no daemon), an activity/stall model, ownership and resource
policies, and a budget-bounded roadmap→task planner behind a deterministic admission
gate. The frozen authority surface is not modified.

**Ten unresolved questions, highest value first.** Each states the decision taken, the
argument against it, and what would change my mind.

---

### Q1 — Tick versus daemon
**Taken:** a 60 s launchd tick that opens the ledger, acts, and exits; lanes are detached
children. Rationale: `docs/THREAT_MODEL.md` records that a long-lived cached `TaskStore`
*changes the Boundary 0 premise* from "many short trusted processes, serialized" to "one
owner plus others" — so a daemon reopens the ratified trust decision and therefore the
whole control-plane review gate.
**Against:** 60 s of dead time after every completion; lock churn; sampling a stalled run
only once a minute; a tick that crashes mid-dispatch leaves reconciliation to the next
tick rather than to a live supervisor.
**Changes my mind:** evidence that per-tick SQLite open cost or dispatch latency is
materially harmful at the real task rate, *plus* a construction where a supervisor holds
no `TaskStore` across ticks (e.g. a supervisor that only `fork`/`exec`s tick processes)
and therefore does not reopen Boundary 0.

### Q2 — Should a stall raise the escalation floor?
**Taken:** a stall *after real progress* is non-transient (raises the floor); a stall
with zero tool calls and zero output is transient (does not). Crossing the metered
boundary additionally requires ≥1 attributed non-stall failure.
**Against:** this is a judgement encoded as a threshold, and it is the only place in the
design where an infrastructure symptom can convert into money. A 24 GB machine under
memory pressure produces exactly the "stalled after progress" signature.
**Changes my mind:** a signal that separates *model hang* from *host pressure* well
enough to classify without the heuristic — RSS trajectory, swap-in rate, Ollama's own
queue state. If that signal exists, the rule should read from it instead.

### Q3 — Is path-level ownership worth building at all?
**Taken:** build it, but as a *collision detector*, not a concurrency enabler: git's
index, HEAD and ref store are repo-global, so one writer per repository remains the rule
and real parallelism comes from `git worktree`.
**Against:** if it never enables concurrency, ~230 lines and a per-tick transaction buy
only a verification assertion that `git status` could produce directly at verify time.
**Changes my mind:** a demonstration that acquire-time claims catch a class of collision
that verify-time diff-scope checking does not — or a decision to drop claims entirely
and keep only diff-scope enforcement.

### Q4 — Can milestone completion ever be non-human?
**Taken:** no. Completion requires verifying the `acceptance` text David wrote at
approval time, and always notifies. "All tasks succeeded" is rejected as a criterion
because the planner controls how many tasks exist.
**Against:** this makes David a required participant once per milestone, which is a real
autonomy ceiling on a long roadmap — the brief's whole objective is fewer interruptions.
**Changes my mind:** a formulation of `acceptance` that is machine-checkable at approval
time (an argv, like the verifier profiles) rather than prose, so milestone completion
becomes a verification rather than a judgement.

### Q5 — Planner authority when the planner also classifies
**Taken:** the planner proposes; a deterministic gate computes risk, capabilities and
approval from *paths, capability set and verify profile*, and a declared risk may only
raise the computed value, never lower it. Scope is frozen by a roadmap document SHA-256.
**Against:** the planner still chooses `owns_paths` and `verify_profile`, which are the
gate's own inputs. A planner that under-declares its footprint under-declares its risk;
the only backstop is verify-time diff-scope, which runs *after* the work.
**Changes my mind:** a pre-execution way to bound a task's real footprint — an
orchestrator-derived path set from the milestone rather than a task-declared one, or a
sandbox that makes out-of-claim writes impossible rather than merely detectable.

### Q6 — Status API transport versus Boundary 0
**Taken:** `state/status.json` (atomic rename) plus `hermesctl status --json`. No
listener, because tripwire **T1** fires on "an externally reachable / network authority
path."
**Against:** ChatGPT cannot read a local file without a terminal in the loop, which is
friction against the brief's "ChatGPT should be able to ask what's happening now."
**Changes my mind:** an argument that a *read-only, no-authority* loopback endpoint is
categorically outside T1 — the tripwire's wording is about authority paths, and status
carries none. If that holds, a loopback-bound read-only server with a file-permission
token is acceptable and should be stated as such in `THREAT_MODEL.md` rather than
assumed.

### Q7 — Credential scoping for third-party CLI adapters *(sharpest security gap)*
**Taken:** a broker injects only capability-scoped keys, never into `paths.spec`, never
into the worktree or logs.
**Against:** `claude` and `codex` read their own config files and hold their own
credentials. Hermes cannot scope what it does not inject, so a task with `code_edit` may
still sit inside a process that can reach every provider credential on the machine. The
two real mitigations — a dedicated macOS user, or a per-adapter `HOME` — **both reopen
Boundary 0** (a second UID / a distinct authority domain), which is a named automatic
reopening condition.
**Needed:** a ruling on whether per-adapter `HOME` under the same UID is a "distinct
authority domain," and if not, what the honest residual is. **This is the one question I
could not resolve without reopening a ratified decision.**

### Q8 — Is there a general contract for non-vacuous verification?
**Taken:** per-profile positive assertions (test count > 0, artifact mtime advanced,
HTTP body contains an expected token). Exit 0 is necessary, never sufficient.
**Against:** per-profile assertions are a checklist, and checklists rot. The cheapest
path to "completed" is always a task that changes nothing.
**Changes my mind:** a general contract — e.g. every verifier must emit a structured
count of *what it examined*, and a zero count is a contract failure independent of exit
code. That would be enforceable once, in `verifier_runner.py`, instead of per profile.

### Q9 — Is counting dispatches an acceptable budget proxy?
**Taken:** yes. The repo refuses to estimate tokens on the grounds that a fabricated
number that drifts is worse than none; counting Hermes's own dispatches is observation,
not estimation. A daily per-provider ceiling produces `IDLE_POLICY`, not a silent stop.
**Against:** dispatch count and cost are weakly correlated — one Opus architecture run
can exceed fifty local dispatches. A ceiling on count is a ceiling on the wrong variable.
**Changes my mind:** a provider-reported usage figure Hermes can *read* rather than
derive (a usage endpoint, a CLI subcommand). That would be a real observation and belongs
in `quota_state.remaining`, which already exists and is deliberately only populated when
something external actually reported it.

### Q10 — Failure fingerprints versus legitimately identical transient failures
**Taken:** `sha256(failure_class ‖ exit_code ‖ normalize(last 2 KB stderr))`; a task is
never retried at a tier that already produced that fingerprint.
**Against:** two genuine transient failures (Ollama restarting twice) produce an
identical fingerprint and permanently bar a tier that was never actually at fault. The
transient/non-transient split is supposed to cover this, but the fingerprint rule is
applied independently of it.
**Changes my mind:** either scope the fingerprint bar to non-transient failures only
(probably correct, and cheap), or show that stderr normalization already separates the
two well enough in practice.

---

## Things I believe are settled and would like Astra to attack anyway

1. **`idle_reason` must never be empty when nothing runs.** The whole design rests on
   this one invariant. Is a property test over generated ledger states sufficient, or is
   there a structural form — as `THREAT_MODEL.md` argues for T3 over
   `ConsumeTimeRevalidationIsLoadBearing` — that pins it better?
2. **The filesystem is the rendezvous; chat identity is advisory.** Is there any failure
   mode where a lost `external_ref` costs more than one redispatch?
3. **The controller never calls the authority path** — it chooses, claims and spawns, and
   `dispatch_policy.assign` is invoked inside the lane child exactly as today. Is that
   separation real, or does the selector become a de-facto policy input by controlling
   *which* tasks are ever offered?
4. **Zero frozen-file edits through M3.** Verify this claim against the freeze manifest.
   If approval expiry, holds or path claims secretly need a ledger edge, the migration
   plan is wrong.
5. **`slot = 1` to start.** Is continuous single-lane operation genuinely better than
   bursty three-lane operation on a 24 GB M4 Pro, or is that excessive caution?
