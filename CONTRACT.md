# CMDS Architecture Contracts (v2)

> Status: **draft — Phase 0**
> Branch: `architecture-v2`
> Goal: make the extraction pipeline reliable, observable, and resumable
> by fixing root-cause foundations rather than patching symptoms.

This document records the **non-negotiable contracts** the v2 architecture
must satisfy. Every implementation decision flows from these. If a piece of
code violates a contract, the code is wrong — not the contract.

---

## 1. Identity Contract (the first foundation)

### Rule

**Section identity is a UUID, assigned at creation, never renamed.**

- Every `section`, `question`, `figure` has a primary-key UUID.
- All foreign keys and joins use UUIDs.
- Slugs are **display-only** — stored, shown in URLs, but **never used to look anything up**.

### Implications

| Field | Role | Allowed to change? |
|---|---|---|
| `section.id` (UUID) | Primary key, FK target | NEVER |
| `section.display_slug` | URL slug, breadcrumb | Yes (re-derived from title) |
| `section.title` | Human-readable | Yes |
| `question.section_id` (FK → section.id) | Join key | Set once, immutable |
| `figure.section_id` (FK → section.id) | Join key, nullable | Set once after linking |

### What this kills

- Today's "Ammonia silently dropped" bug (slug mismatch between schema and DB)
- `schema_alignment.py` patch (becomes unnecessary — UUIDs don't drift)
- Three-tier fallback lookups in `final_merge.py` and `figure_embedder.py`
- Question `section_ref` as a free-form string ("PRACTICE QUESTIONS - CLASSROOM WING")

### Schema regeneration rule

When a book is re-analysed:
- Existing sections matched by **(title, page_start)** keep their UUIDs.
- New sections (added by the new schema) get new UUIDs.
- Removed sections keep their UUIDs and content in DB, marked `archived=true`.
- **UUIDs never change for matched sections.**

This is the same rule `schema_alignment` tries to enforce today — but as a contract,
not a patch wrapped in `try/except`.

---

## 2. State Contract

### Rule

**`book.status` is computed, not written.** Per-stage status fields are the source of truth.

### Per-stage status

```
book.schema_status:     pending → running → done | failed
book.theory_status:     pending → running → done | failed | partial
book.questions_status:  pending → running → done | failed | partial
book.figures_status:    pending → running → done | failed | partial

book.status (derived):
  all done → ready
  any failed → failed
  any partial → partial
  any running → processing
  any pending → queued
```

### What this kills

- `extract.py:578` unconditional `book.status = "ready"` (the lie that started today's chaos)
- `api/books.py:219-236` GET-endpoint mutating state to "self-heal"
- "Ready" books with empty content surface as `partial` instead, with specific gap list

---

## 3. Verification Contract

### Rule

**Every stage runs `verify()` before flipping its status to `done`.**

If verify fails, status = `partial` (with details) or `failed`. **No stage ever
flips to `done` without verify passing.**

### Verify gates per stage

| Stage | Verification |
|---|---|
| **Schema** | `len(sections) > 0`, every `page_start ≤ page_end`, every page in PDF covered by ≥1 leaf section, all parent_ids resolve |
| **Theory** | Every schema section with `content_kind in {theory, mixed}` has ≥1 block OR explicit `empty_content=true` marker |
| **Questions** | Every schema section with `content_kind in {questions, mixed}` has bank with ≥ N questions OR explicit `empty=true` |
| **Figures** | Every detected figure has either a `section_id` FK OR is in the explicit `unattached` bucket. No silent drops. |

### What this kills

- Premature "ready" with 0 content
- Silent drops in the merger (no section can be skipped without it appearing in extraction_log)

---

## 4. Idempotency Contract

### Rule

**Every Celery task is safe to run multiple times on the same input.**

Running `task_theory(book_id, section_id)` twice produces the same result as
running it once. No corruption, no duplication, no state divergence.

### Implications

- Diff/upsert per section, not wipe-and-rebuild
- Task acquires `(book_id, stage)` advisory lock; second concurrent task exits cleanly
- Stage status checked at task start: if already `done`, return early

### What this kills

- Today's race conditions on multiple `/analyse` triggers
- The 15-min zombie task after book deletion
- `_persist_unit`'s wipe-pending-rejected-questions bug

---

## 5. Atomicity & Concurrency Contract

### Rule

**Operations that modify a book serialize cleanly.**

- `POST /analyse` on a book that's already running → 409 Conflict
- `DELETE /book` cancels any running task before removing the row
- Celery task heartbeats every 30s; watchdog requeues stale (no-heartbeat > 5 min) tasks
- Multi-section operations (e.g. theory worker fanout) are independent — one failure doesn't block others

### What this kills

- Concurrent `/analyse` writers fighting over `book.schema`
- Mid-task deletes leaving zombie state
- "Stuck forever" — every state has a max lifetime

---

## 6. Observability Contract

### Rule

**Every failure is structured, tied to an entity, and visible.**

- `extraction_log` table records every (book_id, stage, section_id, attempt) outcome
- `try/except: continue` is forbidden except at task boundary (where the catch must log + write extraction_log row)
- Errors carry: error_class, message, retry_count, duration, traceback (for crashes)

### What this kills

- 14 documented silent-failure points from the audit
- "How did this book end up empty?" debugging dead-ends

---

## 7. Renderer Contract

### Rule

**One canonical document model. All output formats are pure functions of it.**

```
build_canonical_document(book) -> Document   # Pure, no side effects

render_preview(doc) -> HTML
render_docx(doc) -> bytes
render_markdown(doc) -> str
render_json(doc) -> dict
```

- No renderer reads from DB independently.
- No renderer mutates state.
- Same Document → identical structure across all formats (testable invariant).

### What this kills

- Preview vs DOCX vs Markdown divergence
- `seed_draft_items_from_merge` mutating data during render
- Final-draft as a persisted shadow document (drift from sections)

---

## 8. Anti-patterns (explicitly forbidden)

The following patterns appear in v1 code and are **banned in v2**:

1. **`try/except: logger.warning; continue`** — silent error swallowing
2. **`section_by_id.get(slug)` returning None → `continue`** — silent drop
3. **Wipe-and-rebuild** in figure embedder, question persister
4. **Auto-heal/auto-reseed on every read** — recompute hidden side effects
5. **State mutation in GET endpoints** — self-healing reads
6. **"Empty success" returns from Gemini-backed tasks** — `{ok: True}` regardless of content
7. **Free-string foreign keys** (e.g. `question.section_ref = "PRACTICE QUESTIONS - CLASSROOM WING"`)
8. **Multiple parallel implementations of the same operation** (`extract_questions_v1` + `_v2` + `_v3`)
9. **Overlay/shadow tables** for "let user try a different version" (regenerations table)
10. **Implicit serial pipelines** with no per-stage state (the `analyse_book` mega-task)

---

## 9. Migration Approach

- **Strangler fig pattern**: new contracts run alongside old code, behind feature flags
- **Additive first**: add new columns/code, leave old paths working
- **Backfill before flip**: existing data must satisfy new contracts before new code becomes authoritative
- **Delete last**: old code stays for 2 weeks after new code is the default; only then is it removed
- **Rollback always available**: every phase ships independently and can be reverted via feature flag or branch revert

---

## 10. Out of scope (Phase 1)

Deliberately deferred to keep Phase 1 focused:

- **Regeneration system rewrite** (Part 2 of the user's roadmap)
- **Frontend changes** (some Phase 2 needed eventually, but not for foundation)
- **Multi-tenancy / auth changes**
- **Performance optimization** beyond what falls out of removing wipe-and-rebuild
- **New PDF format support** beyond single-column / multi-column

---

## Sign-off

Once this contract is reviewed and accepted, all v2 work must conform. PRs
violating these contracts are rejected. Patches that work around contract
violations are rejected — the underlying contract violation must be fixed.

| Author | Date | Notes |
|---|---|---|
| Architecture-v2 branch | 2026-06-05 | Initial draft, Phase 0 |
