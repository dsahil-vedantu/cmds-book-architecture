# Architecture-v2 Orchestrator — Bug Catalog

A live, evidence-based list of architectural issues found during the post-2026-06-22 audit.
Each bug ↔ a reproducible local symptom, a root cause, and a proposed fix shape.

Workflow:
1. Reproduce locally in the prod-mirror (`./scripts/setup-local-prod.sh`).
2. Add the bug here with **evidence** (log lines, query results, code refs).
3. Decide fix shape (tactical patch vs architectural refactor).
4. Implement on a focused branch; verify locally with failure injection.
5. Only then push to `architecture-v2`.

---

## B1 — Watchdog `_scan_once` crashes on every scan: `Job.created_at` does not exist

**Severity:** CRITICAL — the safety net is non-functional in prod.
**Introduced by:** Commit `98f079e` ("fix(watchdog): auto-recover from zombie queued jobs").
**Affects:** All prod deployments on `architecture-v2` from 2026-06-22 onwards.

### Evidence (from local prod-mirror, `backend/logs/local-prod-backend.log`)

```
ERROR:app.core.watchdog:watchdog scan failed
Traceback (most recent call last):
  File ".../app/core/watchdog.py", line 433, in watchdog_loop
    await asyncio.to_thread(_scan_once)
  ...
  File ".../app/core/watchdog.py", line 122, in _scan_once
    Job.created_at < queue_cutoff,
AttributeError: type object 'Job' has no attribute 'created_at'
```

Occurs once every 60 seconds, on every `_scan_once` invocation.

### Root cause

The `Job` model (`backend/app/models/job.py`) defines:
```python
id, book_id, type, status, progress, message, error,
started_at, finished_at, last_heartbeat_at
```
There is **no `created_at` column**. My Tier 1 fix added:
```python
stale_queued = session.execute(
    select(Job).where(
        Job.status == "queued",
        Job.created_at < queue_cutoff,  # ← does not exist
        Job.finished_at.is_(None),
    )
)
```
I assumed `created_at` existed by analogy with other models. It doesn't. The
`set -e` on the script wraps `_scan_once` in `try/except` so the watchdog
process doesn't die — but the entire scan is aborted before doing any work.

### Downstream consequences (observed in prod 2026-06-22)
- Zombie `queued` jobs never get killed → Celery slots never freed.
- Books stuck "extracting" forever (until manual SQL).
- All other reconciliation work in `_scan_once` (stale-running-jobs sweep,
  orphan stage-status detection, stale-orchestrator-lock release) **also
  fails to run** because the function exits before reaching them.

### Proposed fix (tactical)

Replace `Job.created_at` with a column that actually exists. Options:

1. `Job.started_at` — but `queued` jobs have NULL `started_at` (worker hasn't
   started them yet). Doesn't help for the original goal.
2. `Job.last_heartbeat_at` — same problem; NULL for queued.
3. **Add `created_at` to the model + migration.** This is the right fix —
   knowing when a Job row was created is generally useful. Mirror the
   pattern in `Book.created_at`. Migration `0033_job_created_at`.

### Proposed fix (architectural, deeper)
The bigger issue is that we even *need* a zombie-queued sweep. With a proper
outbox pattern (a `pending_dispatch` table written in the same DB
transaction as the stage-status update), there is no "queued Job that
no one picks up" — every dispatch is durable in Postgres until acked by a
worker. Document for B-future.

### Status
- [ ] Fix written
- [ ] Tested locally with failure injection
- [ ] Reviewed
- [ ] Pushed

---

## B2 — Coordinator dispatch is fire-and-forget — single-message-loss permanently strands books

**Severity:** HIGH — root cause of "stuck at schema_ready" symptoms today.
**Affects:** Every stage handoff (`analyse_book → coordinator`, `coordinator → theory`, etc.).

### Evidence (from today's prod incidents)
- Multiple books observed sitting at `status=schema_ready, schema_status=done,
  theory_status=pending` indefinitely.
- The reconciler doesn't catch them (see B3).
- Manual `dispatch("coordinate_extraction", book_id)` from Railway console
  immediately unblocks them — proving the work *would* happen if the
  dispatch had landed.

### Root cause
After every stage worker finishes its primary work, it calls
`dispatch("coordinate_extraction", book_id)` — a one-shot Celery message.
If the message is lost (broker hiccup, container restart in the ack
window, queue purge, worker death after `task_acks_late` but before
processing), there is no retry. The book sits with the "old" stage
in a terminal state and the "next" stage never starts.

### Proposed fix (tactical)
Use `dispatch_after("verify_dispatch", 60, ...)` (already implemented in
`runner.py`) at every coordinator-fire site too. After `analyse_book`
calls `dispatch("coordinate_extraction", ...)`, also schedule
`verify_dispatch` to check 60s later that the book has actually
advanced past `schema_ready`. If not, re-dispatch.

### Proposed fix (architectural)
Outbox pattern. Every state transition writes a `pending_dispatch` row
in the same transaction as the status change. A dedicated dispatcher
worker reads from the table and ensures every entry is delivered to
Celery, with retries, exponential backoff, and a dead-letter queue.

### Status
- [ ] Fix written
- [ ] Tested locally
- [ ] Reviewed
- [ ] Pushed

---

## B3 — Reconciler excludes `needs_review` (and previously `schema_ready`) books

**Severity:** HIGH — closes the only safety net for handoff drops.
**Partially fixed by:** Commit `6ca61e2` (added `schema_ready` to `_INFLIGHT_STATUSES`).
**Still open:** `Book.schema_status != "needs_review"` filter on the reconciler.

### Evidence
`backend/app/core/watchdog.py:317-319`:
```python
candidates = session.execute(
    select(Book).where(
        Book.status.in_(_INFLIGHT_STATUSES),
        Book.schema_status != "needs_review",  # ← skips needs_review books
```

But `app/workers/orchestrator.py:_decide_next_action()` claims to
auto-advance `needs_review`:
```python
if book.schema_status == _NEEDS_REVIEW:
    # Auto-advance: validator flagged findings but the schema IS saved
```
**Contradiction.** Auto-advance never happens because the reconciler
never fires the coordinator for needs_review books, and the
coordinator is the only thing that triggers the auto-advance.

### Proposed fix
Remove the `schema_status != "needs_review"` filter. If `needs_review`
needs a "user must approve before extraction" gate, that gate should
be modeled separately (e.g., `book.user_approval_required: bool`),
not by silently excluding from reconciliation.

### Status
- [ ] Fix written
- [ ] Tested
- [ ] Reviewed
- [ ] Pushed

---

## B4 — Uniform 15-min `STALE_AFTER_S` is wrong for transition states

**Severity:** MEDIUM — adds 14 min of unnecessary wait to stuck-book recovery.

### Evidence
`backend/app/core/watchdog.py:48`:
```python
STALE_AFTER_S = 900  # 15 minutes
```

Applied uniformly in `_reconcile_stalled_books`:
```python
Book.updated_at < cutoff   # 15 min staleness
```

For books *actively running* a stage (Gemini calls), 15 min is reasonable
— theory extraction on a long chapter can legitimately take 5-10 min.
But for `schema_ready` (no work in progress, just waiting for coordinator
re-fire), 15 min is excessive — the dispatch is either lost (re-fire
immediately) or about to land (wait ≤60s, not 15 min).

### Proposed fix
Separate cutoff per book state:
```python
SCHEMA_READY_STALE_S = 90    # no work in progress — re-fire fast
EXTRACTING_STALE_S   = 900   # legitimate Gemini work — give it time
```

### Status
- [ ] Fix written
- [ ] Tested
- [ ] Pushed

---

## B5 — Workers' atomic CAS protects from duplicate work but not lost work

**Severity:** Architectural — the existing CAS is necessary but not sufficient.

### Evidence
Every stage worker uses `cas_set_stage(session, book_id, stage, "running", from_states=("pending","failed"))` on entry. This atomically transitions `pending → running` and exits if it loses the race.

What it does NOT do:
- Tell anyone "I claimed this work."
- Get re-tried if no worker ever arrives to do the CAS.

So CAS guards against duplicate work (good) but adds nothing to the
fire-and-forget dispatch problem (B2).

### Proposed fix
Pair with B2's outbox pattern: outbox tracks "I dispatched X at time T,
waiting for ack." Worker, on CAS success, writes an ack back to the
outbox. Outbox-driver retries any dispatch with no ack after N seconds.

### Status
- [ ] Documented (no immediate code fix; depends on B2 design)

---

## (Add new bugs below as we find them)
