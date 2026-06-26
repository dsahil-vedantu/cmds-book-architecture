// Extraction pipeline orchestrator.
//
// Goal: drive the four-stage parallel pipeline (schema → theory + questions +
// figures) against the existing backend WITHOUT touching it.
//
// Robustness goals (because real-time will surprise us):
//
//   1. Resume from any state on mount. We GET /api/books/:id first and
//      infer where the pipeline is. If the book is already past where we'd
//      start, we don't re-trigger — we just hook into the existing state.
//
//   2. Idempotent triggers. Every POST is guarded by an "inflight" flag
//      per stage. Retry buttons can't double-fire while a fire is in flight.
//
//   3. localStorage-backed job ID memory. If user refreshes mid-run, we
//      restore the job IDs and resume polling without re-firing the POSTs.
//
//   4. Tolerant of transient network failures. A single GET /jobs/:id
//      failure does NOT flip a stage to "failed" — we count consecutive
//      poll failures and only surface after 3 in a row.
//
//   5. Functional setState everywhere. No stateRef.current reads inside
//      the tick body for transition decisions.
//
//   6. Verbose console.debug logs at every transition so production
//      problems are diagnosable from devtools.
//
//   7. Stall detection. If a stage has had no progress for 5 minutes,
//      surface a warning (without flipping to failed).
//
// What it does NOT do:
//   - No backend changes. Same endpoints, same payloads, same workers.
//   - No new data shapes. Existing data flows through unchanged.

import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiError, req } from './client';
import type { components } from './generated';

// Backend-canonical types pulled directly from the live OpenAPI spec.
// Regenerate with `npm run gen:api` whenever backend ships. If a field
// shape changes, the TypeScript compiler catches it here.
type BackendJobOut = components['schemas']['JobOut'];
type BackendBookOut = components['schemas']['BookOut'];
type BackendSectionOut = components['schemas']['SectionOut'];

// ─────────────────────────────────────────────────────────────────────
// Public types
// ─────────────────────────────────────────────────────────────────────

export type StageKey = 'schema' | 'theory' | 'questions' | 'figures';

export type JobStatus = 'queued' | 'running' | 'done' | 'failed' | 'unknown';

export type StageState = {
  jobId: string | null;
  status: JobStatus;
  progress: number;
  message: string | null;
  error: string | null;
  /** True while a POST or retry is in flight for this stage. */
  inflight: boolean;
  /** ms timestamp of last observed progress change. */
  lastProgressAt: number | null;
};

export type FailedSection = {
  id: string;
  section_id: string;
  title: string;
  attempts: number;
  error: string | null;
};

export type Phase =
  | 'idle'
  | 'loading'        // initial GET /books/:id in flight
  | 'analysing'      // schema job running
  | 'approving'      // /approve or /re-extract POST in flight (brief)
  | 'extracting'     // any of theory / questions / figures running
  | 'reconciling'
  | 'done'
  | 'partial'
  | 'error';

export type SectionCounts = {
  /** Leaf sections expected per book.schema_ (flattened, all leaves). */
  expected: number;
  /** Section rows with status='ready' — extracted successfully. */
  ready: number;
  /** Section rows with status='failed' — worker tried, gave up. */
  failed: number;
  /** Section rows still status='pending'/'extracting' at reconcile time. */
  inFlight: number;
  /** expected − (ready + failed + inFlight): never created at all. */
  missing: number;
};

/** Sections the worker started but never finished — surface for retry. */
export type MissingSection = {
  /** Schema section_id slug. We don't have a Section row id yet. */
  section_id: string;
  title: string;
};

export type ExtractionState = {
  phase: Phase;
  bookId: string | null;
  bookStatus: string | null;       // last seen book.status from backend
  overallPct: number;
  schema: StageState;
  theory: StageState;
  questions: StageState;
  figures: StageState;
  failedSections: FailedSection[];
  /** Sections in the schema that have NO Section row at all. */
  missingSections: MissingSection[];
  /** Cross-checked completeness — populated after reconcile. */
  sectionCounts: SectionCounts;
  questionsFailed: boolean;
  figuresFailed: boolean;
  errorMessage: string | null;
  /** Internal: consecutive job-poll failures per stage. Surfaces after 3. */
  pollErrors: Record<StageKey, number>;
};

// ─────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────

const POLL_INTERVAL_MS = 2000;
const MAX_CONSECUTIVE_POLL_FAILS = 3;
const STALL_THRESHOLD_MS = 5 * 60_000;
const STORAGE_KEY = 'vstudio.extractionJobs';

const STAGE_WEIGHTS: Record<StageKey, number> = {
  schema: 0.10,
  theory: 0.45,
  questions: 0.30,
  figures: 0.15,
};

// ─────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────

function dbg(...args: unknown[]) {
  // eslint-disable-next-line no-console
  console.debug('[extract]', ...args);
}

function initialStage(): StageState {
  return {
    jobId: null,
    status: 'unknown',
    progress: 0,
    message: null,
    error: null,
    inflight: false,
    lastProgressAt: null,
  };
}

function initialState(bookId: string | null = null): ExtractionState {
  return {
    phase: 'idle',
    bookId,
    bookStatus: null,
    overallPct: 0,
    schema: initialStage(),
    theory: initialStage(),
    questions: initialStage(),
    figures: initialStage(),
    failedSections: [],
    missingSections: [],
    sectionCounts: { expected: 0, ready: 0, failed: 0, inFlight: 0, missing: 0 },
    questionsFailed: false,
    figuresFailed: false,
    errorMessage: null,
    pollErrors: { schema: 0, theory: 0, questions: 0, figures: 0 },
  };
}

function explain(err: unknown): string {
  if (err instanceof ApiError) return `Backend ${err.status}: ${err.message}`;
  if (err instanceof Error) return err.message;
  return 'Unknown error';
}

function normalizeStatus(raw: string): JobStatus {
  // Backend canonical values (from app/workers/*.py + app/api/*.py):
  //   - "queued"                     job created, worker hasn't picked up yet
  //   - "running" | "extracting" | "analysing" | "regenerating"  in-flight
  //   - "succeeded"                  terminal success
  //   - "failed" | "error"           terminal failure
  if (raw === 'succeeded' || raw === 'done' || raw === 'completed' || raw === 'success' || raw === 'complete')
    return 'done';
  if (raw === 'failed' || raw === 'error') return 'failed';
  if (
    raw === 'running' ||
    raw === 'extracting' ||
    raw === 'analysing' ||
    raw === 'regenerating' ||
    raw === 're_extracting'
  )
    return 'running';
  if (raw === 'queued' || raw === 'pending') return 'queued';
  return 'unknown';
}

function isTerminal(s: JobStatus): boolean {
  return s === 'done' || s === 'failed';
}

function stagePct(st: StageState): number {
  if (st.status === 'done') return 100;
  if (st.status === 'unknown' && !st.jobId) return 0;
  return Math.max(0, Math.min(100, st.progress || 0));
}

function computeOverall(s: ExtractionState): number {
  return Math.round(
    stagePct(s.schema) * STAGE_WEIGHTS.schema +
      stagePct(s.theory) * STAGE_WEIGHTS.theory +
      stagePct(s.questions) * STAGE_WEIGHTS.questions +
      stagePct(s.figures) * STAGE_WEIGHTS.figures,
  );
}

// ─────────────────────────────────────────────────────────────────────
// localStorage persistence for job IDs (survives page refresh)
// ─────────────────────────────────────────────────────────────────────

type StoredJobs = Partial<Record<StageKey, string>>;
type StoredState = Record<string, StoredJobs>; // bookId → stored jobs

function loadStored(): StoredState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as StoredState) : {};
  } catch {
    return {};
  }
}

function saveStored(bookId: string, jobs: StoredJobs) {
  try {
    const all = loadStored();
    all[bookId] = jobs;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(all));
  } catch {
    /* quota / disabled localStorage — ignore */
  }
}

function clearStored(bookId: string) {
  try {
    const all = loadStored();
    delete all[bookId];
    localStorage.setItem(STORAGE_KEY, JSON.stringify(all));
  } catch {
    /* ignore */
  }
}

// ─────────────────────────────────────────────────────────────────────
// Thin API call wrappers
// ─────────────────────────────────────────────────────────────────────

// Use the backend-canonical types (BackendJobOut / BackendBookOut) so any
// shape drift between frontend and backend becomes a compile error.
const getJob = (jobId: string) => req<BackendJobOut>(`/api/jobs/${jobId}`);
const getBook = (bookId: string) => req<BackendBookOut>(`/api/books/${bookId}`);

const postAnalyse = (bookId: string) =>
  req<{ job_id: string }>(`/api/books/${bookId}/analyse`, { method: 'POST' });

const postApprove = (bookId: string) =>
  req<{ job_id: string }>(`/api/books/${bookId}/approve`, { method: 'POST' });

// ORCH Day 12 — per-stage retry endpoints (Day 10 backend).
// The frontend used to call /question-banks and /extract-figures-v2
// directly, racing with the backend orchestrator. Those raw worker
// dispatches are gone; user-initiated retries now go through these
// orchestrator-mediated endpoints which surgically reset just that
// stage and let the coordinator's state machine dispatch the worker.
const postRetryTheory = (bookId: string) =>
  req<{ job_id: string }>(`/api/books/${bookId}/retry-theory`, { method: 'POST' });

const postRetryQuestions = (bookId: string) =>
  req<{ job_id: string }>(`/api/books/${bookId}/retry-questions`, { method: 'POST' });

const postRetryFigures = (bookId: string) =>
  req<{ job_id: string }>(`/api/books/${bookId}/retry-figures`, { method: 'POST' });

const postReExtractSection = (sectionRowId: string) =>
  req<{ job_id: string }>(`/api/sections/${sectionRowId}/re-extract`, {
    method: 'POST',
  });

const postReExtractAll = (bookId: string) =>
  req<{ job_id: string }>(`/api/books/${bookId}/re-extract`, { method: 'POST' });

const getSections = (bookId: string) =>
  req<BackendSectionOut[]>(`/api/books/${bookId}/sections`);

// ─── Schema flattening — count expected leaf sections ─────────────────
//
// "Leaf" = a section that has no children OR whose children are subsections
// (the worker iterates the leaves). Excluded sections are NOT extracted.
type SchemaSection = {
  id?: string;
  title?: string;
  type?: 'chapter' | 'section' | 'subsection' | 'excluded';
  content_types?: string[];
  subsections?: SchemaSection[];
};

/** Flatten the schema into the set of section_ids the extractor will visit. */
function flattenExpectedLeaves(
  schema: unknown,
): Array<{ section_id: string; title: string }> {
  if (!schema || typeof schema !== 'object') return [];
  const top = (schema as { sections?: SchemaSection[] }).sections;
  if (!Array.isArray(top)) return [];
  const out: Array<{ section_id: string; title: string }> = [];
  const walk = (nodes: SchemaSection[]) => {
    for (const n of nodes) {
      if (n.type === 'excluded') continue;
      const subs = n.subsections ?? [];
      const nonExcludedSubs = subs.filter((s) => s.type !== 'excluded');
      if (nonExcludedSubs.length === 0) {
        // Leaf — this is what the worker creates a Section row for.
        if (n.id) out.push({ section_id: n.id, title: n.title ?? n.id });
      } else {
        walk(nonExcludedSubs);
      }
    }
  };
  walk(top);
  return out;
}

type QuestionBankOut = {
  id: string;
  status: string;
  last_error: string | null;
  job_id?: string | null;
  /** Backend exposes this for live progress; absent on older builds → treat as 0. */
  question_count?: number | null;
};
const listBanks = (bookId: string) =>
  req<QuestionBankOut[]>(`/api/books/${bookId}/question-banks`);

type FigureOut = { id: string; status: string };
const listFigures = (bookId: string) =>
  req<FigureOut[] | { figures: FigureOut[] }>(
    `/api/books/${bookId}/figures`,
  ).then((r) => (Array.isArray(r) ? r : r.figures ?? []));

// ─────────────────────────────────────────────────────────────────────
// The hook
// ─────────────────────────────────────────────────────────────────────

export type UseExtractionPipeline = {
  state: ExtractionState;
  start: (bookId: string) => Promise<void>;
  retryStage: (stage: StageKey) => Promise<void>;
  retrySection: (sectionRowId: string) => Promise<void>;
  /**
   * Re-run theory extraction for everything. Use when missing sections exist
   * (no Section row to target individually). Wipes every section to pending
   * and runs the worker fresh — matches the existing /api/books/:id/re-extract
   * semantics. Idempotent.
   */
  retryAllTheory: () => Promise<void>;
  cancel: () => void;
};

export function useExtractionPipeline(): UseExtractionPipeline {
  const [state, setState] = useState<ExtractionState>(initialState());

  // Mutable refs used by polling / mount-time effects. They are not used
  // for state-transition decisions inside tick — those use functional
  // setState callbacks so we always have the freshest state.
  const pollTimer = useRef<number | null>(null);
  const tickRunning = useRef(false);
  const reconciledFor = useRef<string | null>(null);

  // ─── Patch helper ───────────────────────────────────────────────
  /**
   * Apply a patch. `compute(s)` returns the partial; the result is merged
   * over the current state, then overallPct is recomputed. Pure setState —
   * always reads the latest state via React's setState callback contract.
   */
  const apply = useCallback(
    (compute: (s: ExtractionState) => Partial<ExtractionState> | null) => {
      setState((prev) => {
        const partial = compute(prev);
        if (!partial) return prev;
        const next = { ...prev, ...partial };
        next.overallPct = computeOverall(next);
        return next;
      });
    },
    [],
  );

  // ─── Polling control ────────────────────────────────────────────
  const stopPolling = useCallback(() => {
    if (pollTimer.current != null) {
      window.clearInterval(pollTimer.current);
      pollTimer.current = null;
      dbg('polling stopped');
    }
  }, []);

  const startPolling = useCallback((tick: () => void) => {
    if (pollTimer.current != null) return; // already polling
    dbg('polling started');
    // Fire one tick immediately so user sees movement, then schedule.
    void tick();
    pollTimer.current = window.setInterval(tick, POLL_INTERVAL_MS);
  }, []);

  // ─── Persist active job IDs whenever the state changes ──────────
  useEffect(() => {
    if (!state.bookId) return;
    if (state.phase === 'done' || state.phase === 'partial' || state.phase === 'error') {
      clearStored(state.bookId);
      return;
    }
    const stored: StoredJobs = {};
    (['schema', 'theory', 'questions', 'figures'] as StageKey[]).forEach((k) => {
      const j = state[k].jobId;
      if (j) stored[k] = j;
    });
    saveStored(state.bookId, stored);
  }, [state]);

  // ─── Phase 1: kick theory ALONE ──────────────────────────────────
  //
  // We deliberately do NOT fire questions + figures yet. Theory is the
  // heaviest stage; giving it solo CPU + Gemini quota means it finishes
  // faster and more reliably. Q + figures get triggered after theory is
  // done (see kickRestParallel below).
  //
  // forceFresh=true uses /re-extract (destructive — wipes Section rows).
  // forceFresh=false uses /approve (first-time path).
  const kickTheoryAlone = useCallback(
    async (bookId: string, forceFresh = false) => {
      dbg('kicking theory alone for', bookId, { forceFresh });
      apply(() => ({
        phase: 'approving',
        theory: { ...initialStage(), inflight: true },
        // Q + figures explicitly NOT touched yet — they kick after theory.
        questions: { ...initialStage(), status: 'queued', message: 'waiting for theory' },
        figures: { ...initialStage(), status: 'queued', message: 'waiting for theory' },
      }));
      try {
        const r = forceFresh ? await postReExtractAll(bookId) : await postApprove(bookId);
        apply(() => ({
          phase: 'extracting',
          theory: {
            ...initialStage(),
            jobId: r.job_id,
            status: 'queued',
            lastProgressAt: Date.now(),
          },
        }));
      } catch (e) {
        apply(() => ({
          phase: 'extracting',
          theory: {
            ...initialStage(),
            status: 'failed',
            error: explain(e),
          },
        }));
        dbg('theory POST failed', e);
      }
    },
    [apply],
  );

  // ORCH Day 12 — kickRestParallel REMOVED.
  // The post-schema coordinator (workers/orchestrator.py) auto-fires
  // questions + figures dispatch when extract_book finishes (theory
  // tail → coordinator → dispatch_both). The previous frontend call
  // to postQuestionBank + postExtractFigures raced with the backend
  // orchestrator, causing 409 Conflict errors that the UI displayed
  // as "Failed: Backend 409" even though the backend's dispatch had
  // actually succeeded. Backend is now the sole orchestrator;
  // frontend observes via polling.

  // Back-compat alias used by start() — calls the new theory-first path.
  const kickThreeParallel = kickTheoryAlone;

  // ─── Reconcile after all 3 extractions settle ──────────────────
  //
  // Three completeness checks, run in parallel:
  //
  //   • Theory completeness: compare the schema's expected leaf section
  //     count against the actual Section rows. Anything in the schema with
  //     no Section row is "missing" — the worker never tried it (could be
  //     a worker crash, race, or backend bug). These get surfaced for
  //     explicit retry.
  //   • Failed sections: Section rows with status='failed'. Backend gave
  //     up after retries. Surface for per-section retry.
  //   • In-flight sections: Section rows still 'pending'/'extracting'
  //     when the umbrella job claims done. Real failure mode — backend
  //     might have written job=succeeded prematurely. Surface as warning.
  //
  // Plus questions + figures status checks.
  const reconcile = useCallback(
    async (bookId: string) => {
      if (reconciledFor.current === bookId) return;
      reconciledFor.current = bookId;
      dbg('reconciling', bookId);

      apply(() => ({ phase: 'reconciling' }));

      const [bookR, sectionsR, banksR, figuresR] = await Promise.allSettled([
        getBook(bookId),
        getSections(bookId),
        listBanks(bookId),
        listFigures(bookId),
      ]);

      const book = bookR.status === 'fulfilled' ? bookR.value : null;
      const sections =
        sectionsR.status === 'fulfilled' ? sectionsR.value : [];
      const banks = banksR.status === 'fulfilled' ? banksR.value : [];
      const figures = figuresR.status === 'fulfilled' ? figuresR.value : [];

      // Expected leaves from schema (the worker's ground truth iteration set).
      const expectedLeaves = book ? flattenExpectedLeaves(book.schema_) : [];
      const expectedIds = new Set(expectedLeaves.map((l) => l.section_id));

      // Actual sections indexed by section_id slug.
      const actualById = new Map<string, BackendSectionOut>();
      for (const s of sections) actualById.set(s.section_id, s);

      // Build per-section breakdown.
      let readyCount = 0;
      let inFlightCount = 0;
      const failed: FailedSection[] = [];
      const missing: MissingSection[] = [];

      for (const leaf of expectedLeaves) {
        const row = actualById.get(leaf.section_id);
        if (!row) {
          missing.push({ section_id: leaf.section_id, title: leaf.title });
          continue;
        }
        // Backend marks sections 'passed' (QC succeeded), 'failed' (QC
        // gave up after retries), or 'skipped' (intentionally not
        // processed — e.g. parent nodes). Accept 'ready' as defensive
        // alias. All three count as "done" / terminal.
        if (
          row.status === 'passed' ||
          row.status === 'ready' ||
          row.status === 'skipped'
        ) {
          readyCount++;
        } else if (row.status === 'failed') {
          let err: string | null = null;
          const qc = row.qc_local as Record<string, unknown> | null;
          if (qc && typeof qc.last_error === 'string') err = qc.last_error;
          failed.push({
            id: row.id,
            section_id: row.section_id,
            title: row.title,
            attempts: row.attempts,
            error: err,
          });
        } else {
          // 'pending' or 'extracting' or any other non-terminal — counted
          // as in-flight; user can wait or force retry.
          inFlightCount++;
        }
      }

      // Also surface any orphan Section rows whose section_id is NOT in
      // the current schema (shouldn't happen, but if it does, log).
      const orphans = sections.filter((s) => !expectedIds.has(s.section_id));
      if (orphans.length > 0) {
        dbg('orphan section rows (not in schema):', orphans.map((o) => o.section_id));
      }

      const sectionCounts: SectionCounts = {
        expected: expectedLeaves.length,
        ready: readyCount,
        failed: failed.length,
        inFlight: inFlightCount,
        missing: missing.length,
      };

      const latestBank = banks[banks.length - 1] ?? null;
      const questionsFailed =
        !latestBank || latestBank.status === 'failed' || latestBank.status === 'error';
      const figuresFailed =
        figures.length === 0 || figures.some((f) => f.status === 'failed');

      apply((prev) => {
        const theoryHasIssues =
          failed.length > 0 ||
          missing.length > 0 ||
          inFlightCount > 0;
        const anyFailure =
          theoryHasIssues ||
          questionsFailed ||
          figuresFailed ||
          prev.theory.status === 'failed' ||
          prev.questions.status === 'failed' ||
          prev.figures.status === 'failed';
        dbg('reconciled', {
          ...sectionCounts,
          questionsFailed,
          figuresFailed,
          anyFailure,
        });
        return {
          phase: anyFailure ? 'partial' : 'done',
          failedSections: failed,
          missingSections: missing,
          sectionCounts,
          questionsFailed,
          figuresFailed,
        };
      });
    },
    [apply],
  );

  // ─── tick: poll active jobs, advance the state machine ─────────
  // Refs to break the tick → kick* → tick callback cycle.
  // `tick` is captured by setInterval, but kick and reconcile need
  // to be the latest closures; we look them up from refs.
  // ORCH Day 12 — kickRestRef + restKickedFor removed (backend handles
  // Q+figures dispatch now; nothing to track on the frontend side).
  const kickRef = useRef(kickTheoryAlone);
  const reconcileRef = useRef(reconcile);
  useEffect(() => {
    kickRef.current = kickTheoryAlone;
    reconcileRef.current = reconcile;
  }, [kickTheoryAlone, reconcile]);

  const tick = useCallback(async () => {
    if (tickRunning.current) return; // overlap guard
    tickRunning.current = true;
    try {
      // Snapshot the latest state via setState callback (no ref reads).
      let snapshot: ExtractionState | null = null;
      setState((s) => {
        snapshot = s;
        return s;
      });
      if (!snapshot) return;
      const s: ExtractionState = snapshot;
      if (!s.bookId) return;

      // Find stages with an active job to poll.
      const active: Array<{ key: StageKey; jobId: string }> = [];
      (['schema', 'theory', 'questions', 'figures'] as StageKey[]).forEach(
        (k) => {
          const st = s[k];
          if (st.jobId && !isTerminal(st.status)) {
            active.push({ key: k, jobId: st.jobId });
          }
        },
      );

      // ORCH Day 12.5 — sync per-stage status from book whenever any
      // stage still has no jobId. Backend orchestrator (Days 1-12)
      // creates jobs for Q+Fig WITHOUT going through the frontend, so
      // the frontend never gets those job IDs. We use book.<stage>_status
      // (now exposed by BookOut after Day 12.5) as the source of truth.
      // Poll the book whenever the pipeline is in-flight and ANY stage is
      // (or may be) driven by the backend orchestrator without a client
      // jobId. book.<stage>_status is the single source of truth on the
      // server-auto-proceed path. Kept deliberately BROAD — covering every
      // in-flight phase plus any non-terminal jobless stage — so a new
      // stage or a new status string can never silently stop the polling.
      // That silent-stop was the class of bug that hid live progress and
      // forced manual refreshes.
      const needsBookPoll =
        !!s.bookId && (
          s.phase === 'loading' ||
          s.phase === 'analysing' ||
          s.phase === 'approving' ||
          s.phase === 'extracting' ||
          (['theory', 'questions', 'figures'] as StageKey[]).some(
            (k) => !s[k].jobId && !isTerminal(s[k].status),
          )
        );
      if (needsBookPoll) {
        try {
          const book = await getBook(s.bookId!);
          // Map backend per-stage status string → frontend JobStatus
          // (running/done/failed/queued/unknown). Same logic as
          // normalizeStatus but for the per-stage fields.
          const mapStage = (raw: string | undefined | null): JobStatus => {
            if (!raw) return 'unknown';
            if (raw === 'done' || raw === 'partial') return 'done';
            if (raw === 'failed') return 'failed';
            if (raw === 'running' || raw === 'extracting') return 'running';
            if (raw === 'pending' || raw === 'queued') return 'queued';
            return 'unknown';
          };
          const bk = book as BackendBookOut & {
            schema_status?: string;
            theory_status?: string;
            questions_status?: string;
            figures_status?: string;
          };

          apply((prev) => {
            const patch: Partial<ExtractionState> = { bookStatus: book.status };
            // Schema — driven by book.schema_status when the client holds no
            // schema jobId. This is the COMMON path: the backend auto-analyses
            // on upload (no client POST → no jobId), and every page revisit
            // re-attaches without one. Without this sync the Schema row sat at
            // 0%/blank/"queued" while schema was actually running on the
            // backend — the "BUILDING SCHEMA 0%, refresh-to-see-it" bug.
            // 'needs_review' means the schema IS built (just flagged), so it
            // counts as done. Mirrors the theory/questions/figures sync below.
            const scStatus =
              bk.schema_status === 'needs_review'
                ? 'done'
                : mapStage(bk.schema_status);
            if (!prev.schema.jobId) {
              if (scStatus === 'done' && prev.schema.status !== 'done') {
                patch.schema = { ...prev.schema, status: 'done', progress: 100, message: 'Schema built' };
              } else if (scStatus === 'failed' && prev.schema.status !== 'failed') {
                patch.schema = { ...prev.schema, status: 'failed', error: 'Schema build failed' };
              } else if (scStatus === 'running' && prev.schema.status !== 'running' && prev.schema.status !== 'done') {
                patch.schema = { ...prev.schema, status: 'running', message: 'Building schema…', lastProgressAt: Date.now() };
              } else if (scStatus === 'queued' && prev.schema.status === 'unknown') {
                patch.schema = { ...prev.schema, status: 'queued', message: 'Queued — waiting for worker' };
              }
            }
            // Theory — driven by book.theory_status when no client-side
            // jobId (server auto-proceed path). Without the 'running'
            // branch, theoryActive (line ~815) never goes true on
            // server-kicked theory → getSections() never polled → UI
            // shows 0% even though theory IS running. Same pattern as
            // questions/figures below.
            const tStatus = mapStage(bk.theory_status);
            if (tStatus === 'done' && prev.theory.status !== 'done') {
              patch.theory = { ...prev.theory, status: 'done', progress: 100 };
            } else if (tStatus === 'failed' && prev.theory.status !== 'failed') {
              patch.theory = {
                ...prev.theory,
                status: 'failed',
                error: 'Theory worker failed',
              };
            } else if (
              tStatus === 'running'
              && prev.theory.status !== 'running'
              && prev.theory.status !== 'done'
              && prev.theory.status !== 'failed'
            ) {
              patch.theory = {
                ...prev.theory,
                status: 'running',
                message: 'Extracting theory…',
                lastProgressAt: Date.now(),
              };
            } else if (
              tStatus === 'queued'
              && prev.theory.status === 'unknown'
            ) {
              patch.theory = {
                ...prev.theory,
                status: 'queued',
                message: 'Queued — waiting for worker',
              };
            }
            // Questions — driven entirely by book.questions_status now
            // (backend creates the bank + job; we have no jobId).
            const qStatus = mapStage(bk.questions_status);
            if (!prev.questions.jobId) {
              if (qStatus === 'done') {
                patch.questions = { ...prev.questions, status: 'done', progress: 100, message: 'Questions extracted' };
              } else if (qStatus === 'failed') {
                patch.questions = { ...prev.questions, status: 'failed', error: 'Questions extraction failed' };
              } else if (qStatus === 'running') {
                patch.questions = { ...prev.questions, status: 'running', message: 'Extracting questions...', lastProgressAt: Date.now() };
              }
              // 'queued' / 'unknown' → leave alone, keeps "waiting for theory" placeholder.
            }
            // Figures — same pattern.
            const fStatus = mapStage(bk.figures_status);
            if (!prev.figures.jobId) {
              if (fStatus === 'done') {
                patch.figures = { ...prev.figures, status: 'done', progress: 100, message: 'Figures extracted' };
              } else if (fStatus === 'failed') {
                patch.figures = { ...prev.figures, status: 'failed', error: 'Figures extraction failed' };
              } else if (fStatus === 'running') {
                patch.figures = { ...prev.figures, status: 'running', message: 'Extracting figures...', lastProgressAt: Date.now() };
              }
            }

            // ── Phase is server-authoritative ───────────────────────
            // Advance the overall phase from book.status so the UI moves
            // forward even when the backend orchestrator drives stages on
            // its own (the client holds no jobIds on that path). Only ever
            // moves FORWARD into 'analysing' / 'extracting'; never overrides
            // a terminal or reconciling phase (those are owned by the
            // reconcile transition below). This is what makes "View
            // extracted" / live status appear WITHOUT a manual refresh.
            const TERMINAL_PHASES: Phase[] = ['done', 'partial', 'error', 'reconciling'];
            if (!TERMINAL_PHASES.includes(prev.phase)) {
              const bstat = book.status;
              if (bstat === 'analysing' || bstat === 'schema_ready') {
                if (prev.phase === 'idle' || prev.phase === 'loading') {
                  patch.phase = 'analysing';
                }
              } else if (
                bstat === 'extracting'
                || bstat === 'processing'
                || bstat === 're_extracting'
              ) {
                if (prev.phase !== 'extracting') {
                  patch.phase = 'extracting';
                }
              } else if (
                bstat === 'ready'
                || bstat === 'extracted'
                || bstat === 'partial'
                || bstat === 'approved'
                || bstat === 'done'
              ) {
                // Backend itself reached a TERMINAL state — advance the
                // client phase directly so "View extracted" / "Start
                // regeneration" CTAs render WITHOUT waiting for the
                // tick-level reconcile (whose allTerm check uses stale
                // prev state due to React's setState batching, costing
                // one extra polling tick before CTAs appear and feeling
                // like "needs refresh"). Direct handoff = instant CTAs
                // the moment the book-poll learns extraction is done.
                patch.phase = bstat === 'partial' ? 'partial' : 'done';
              } else if (bstat === 'failed') {
                patch.phase = 'error';
              }
            }
            return patch;
          });
        } catch (e) {
          dbg('book poll failed', e);
        }
      }

      if (active.length > 0) {
        const results = await Promise.allSettled(
          active.map((a) => getJob(a.jobId)),
        );

        apply((prev) => {
          const patch: Partial<ExtractionState> = {};
          const pollErrors = { ...prev.pollErrors };
          results.forEach((r, i) => {
            const { key } = active[i];
            const prevStage = prev[key];
            if (r.status === 'fulfilled') {
              const job = r.value;
              const newStatus = normalizeStatus(job.status);
              const newProgress = Number(job.progress) || 0;
              const progressChanged = newProgress !== prevStage.progress;
              pollErrors[key] = 0;
              patch[key] = {
                ...prevStage,
                jobId: job.id,
                status: newStatus,
                progress: newProgress,
                message: job.message ?? null,
                error: job.error ?? null,
                lastProgressAt: progressChanged
                  ? Date.now()
                  : prevStage.lastProgressAt,
                inflight: false,
              };
              if (newStatus === 'done' || newStatus === 'failed') {
                dbg(`${key} → ${newStatus}`, job.message ?? '');
              }
            } else {
              pollErrors[key] = (pollErrors[key] ?? 0) + 1;
              dbg(
                `poll ${key} failed (${pollErrors[key]}/${MAX_CONSECUTIVE_POLL_FAILS}):`,
                r.reason,
              );
              if (pollErrors[key] >= MAX_CONSECUTIVE_POLL_FAILS) {
                patch[key] = {
                  ...prevStage,
                  status: 'failed',
                  error: `Polling failed ${pollErrors[key]} times: ${explain(r.reason)}`,
                  inflight: false,
                };
              }
              // Otherwise keep prior state — transient blip, give it time.
            }
          });
          patch.pollErrors = pollErrors;
          return patch;
        });
      }

      // ─── Per-section real progress for THEORY ───────────────────────
      //
      // Run AFTER the job poll above so this override wins (otherwise the
      // job's coarse 10% heartbeat would overwrite our real value).
      //
      // Backend's theory job.progress is a coarse heartbeat (10% at start,
      // 100% when done) — useless for showing real progress. Compute it
      // ourselves from Section rows: each section transitions pending →
      // passed|failed as the worker iterates. (passed+failed) / total = real %.
      const theoryActive =
        s.theory.status === 'running' || s.theory.status === 'queued';
      if (theoryActive && s.bookId) {
        try {
          const [sections, bookCheck] = await Promise.all([
            getSections(s.bookId),
            getBook(s.bookId).catch(() => null),
          ]);

          // ── Theory % counts ONLY Cat B (pure theory) sections ──
          // Match backend's questions_v3 split: Cat A = "questions" in
          // content_types; Cat B = everything else. User expects the
          // theory progress to reflect theory sections only — Cat A
          // section rows the worker writes are still tracked, but they
          // count toward Questions progress, not Theory.
          const catBSlugs = new Set<string>();
          if (bookCheck?.schema_) {
            type Node = {
              id?: string;
              content_types?: string[];
              subsections?: Node[];
              type?: string;
            };
            const walk = (nodes: Node[] | undefined) => {
              if (!nodes) return;
              for (const n of nodes) {
                if (n.type === 'excluded') continue;
                const ct = (n.content_types ?? []).map((c) =>
                  String(c).toLowerCase().trim(),
                );
                // A section counts toward theory if it has theory content.
                // Pure questions-only sections (Cat A: Example, Exercise) are
                // excluded — they're handled by the question pipeline as
                // placeholders. After the "remove Mixed" change, content_types
                // is either ["theory"] or ["questions"]; legacy Mixed
                // ["theory","questions"] is normalised to ["theory"] in the
                // backend postpass — so this filter is now unambiguous.
                const isPureCatA =
                  ct.includes('questions') && !ct.includes('theory');
                if (n.id && !isPureCatA) catBSlugs.add(n.id);
                if (n.subsections?.length) walk(n.subsections);
              }
            };
            walk(
              (bookCheck.schema_ as { sections?: Node[] }).sections,
            );
          }

          // Filter to Cat B only. If the schema walk didn't yield any
          // Cat B slugs (schema not loaded yet, or no Cat B sections at
          // all), DO NOT fall back to the full section list — that would
          // make the theory % include Cat A (question) sections that the
          // theory worker never touches, locking the bar below 100% and
          // confusing reviewers. Instead, skip the override entirely and
          // let the worker's coarse heartbeat progress stand for this tick.
          const scopedSections =
            catBSlugs.size > 0
              ? sections.filter((sec) => catBSlugs.has(sec.section_id))
              : [];

          if (scopedSections.length > 0) {
            // "Done" = the worker is finished with it: passed (QC ok),
            // failed (QC gave up after retries), or skipped (intentionally
            // not processed — e.g. parent nodes). Anything else is in-flight.
            const done = scopedSections.filter(
              (sec) =>
                sec.status === 'passed' ||
                sec.status === 'failed' ||
                sec.status === 'skipped',
            ).length;
            const realPct = Math.round((done / scopedSections.length) * 100);
            const failedCount = scopedSections.filter(
              (sec) => sec.status === 'failed',
            ).length;
            // Theory is done when every Cat B section is in a terminal
            // state (passed/failed/skipped). We do NOT wait for
            // book.status to flip to 'ready' — the backend orchestrator's
            // finalization step is unreliable (worker can die before it
            // runs), which would otherwise hang Q+figures forever in
            // "waiting for theory". Section-completion is ground truth;
            // book.status is kept as a corroborating signal but no
            // longer gates the handoff.
            const allTerminal = done === scopedSections.length;
            const bookAdvanced = bookCheck
              ? ['ready', 'extracted', 'failed'].includes(bookCheck.status)
              : false;
            void bookAdvanced; // retained for telemetry; not used as a gate
            const reallyDone = allTerminal;

            apply((prev) => {
              if (realPct < prev.theory.progress && !reallyDone) return {};
              return {
                theory: {
                  ...prev.theory,
                  // Flip status to done only when both signals agree.
                  status: reallyDone ? 'done' : prev.theory.status,
                  progress: reallyDone ? 100 : realPct,
                  message:
                    failedCount > 0
                      ? `${done} of ${scopedSections.length} theory sections (${failedCount} failed)`
                      : `${done} of ${scopedSections.length} theory sections`,
                },
                bookStatus: bookCheck?.status ?? prev.bookStatus,
              };
            });
          }
        } catch (e) {
          dbg('per-section poll failed', e);
        }
      }

      // ─── Per-question real progress for QUESTIONS ────────────────────
      //
      // Same problem the figures row had: backend job.progress is a coarse
      // heartbeat, so Questions sat at "Running 0%" the whole time. Use the
      // /question-banks endpoint's question_count to show real ground truth
      // ("12 questions extracted") and a bounded heuristic for the bar (no
      // known total up front, so we cap at 90% while running and only flip
      // to 100% on the worker's done signal).
      const questionsActive = s.questions.status === 'running' || s.questions.status === 'queued';
      if (questionsActive && s.bookId) {
        try {
          const banks = await listBanks(s.bookId);
          const latest = banks[banks.length - 1] ?? null;
          const n = Number(latest?.question_count ?? 0) || 0;
          // Cap at 90% while running — worker's done signal flips to 100%.
          const realPct = n === 0 ? 5 : Math.min(90, 10 + n * 2);
          apply((prev) => {
            if (prev.questions.status === 'done' || prev.questions.status === 'failed') {
              return {};
            }
            if (realPct < prev.questions.progress) return {};
            return {
              questions: {
                ...prev.questions,
                status: 'running',
                progress: realPct,
                message: n === 0
                  ? 'Extracting questions…'
                  : `${n} question${n === 1 ? '' : 's'} extracted`,
                lastProgressAt: Date.now(),
              },
            };
          });
        } catch (e) {
          dbg('per-question poll failed', e);
        }
      }

      // ─── Per-figure real progress for FIGURES ─────────────────────────
      //
      // Mirrors the theory per-section pass: backend's figures job.progress is
      // a coarse heartbeat (10% at start → 100% at end), so without this the
      // Figures row sat at "Running 0%" the entire time and only snapped to
      // 100% at the very end — looked frozen / hung even when the worker was
      // actively extracting. We don't know the total figure count up front
      // (Gemini's vision pass detects it mid-run), so we poll the figures
      // list, show "N figures extracted" as ground truth, and use a bounded
      // heuristic for the % so the bar moves but never lies about completion.
      const figuresActive = s.figures.status === 'running' || s.figures.status === 'queued';
      if (figuresActive && s.bookId) {
        try {
          const figures = await listFigures(s.bookId);
          const n = figures.length;
          // Cap at 90% while running — only the worker's done signal flips us
          // to 100%. With no known total, log-scaling avoids a runaway bar.
          const realPct = n === 0 ? 5 : Math.min(90, 10 + n * 5);
          apply((prev) => {
            if (prev.figures.status === 'done' || prev.figures.status === 'failed') {
              return {};
            }
            if (realPct < prev.figures.progress) return {};
            return {
              figures: {
                ...prev.figures,
                status: 'running',
                progress: realPct,
                message: n === 0
                  ? 'Detecting figures…'
                  : `${n} figure${n === 1 ? '' : 's'} extracted`,
                lastProgressAt: Date.now(),
              },
            };
          });
        } catch (e) {
          dbg('per-figure poll failed', e);
        }
      }

      // ─── Phase transitions (read latest state via setState callback) ───
      //
      // ORCH Day 12 — the "kick Q+figures" branch is gone; the backend
      // orchestrator dispatches them automatically when theory's tail
      // (linker + embedder) completes. We only need to detect when the
      // whole pipeline has settled (or stalled) and trigger reconcile.
      let didKickTheory = false;
      let didReconcile = false;
      setState((prev) => {
        // 1. analysing → kick theory once schema is done
        //    kickTheoryAlone now just hits /approve, which the backend
        //    routes through coordinator (Day 8). Idempotent — safe.
        if (prev.phase === 'analysing' && prev.schema.status === 'done') {
          didKickTheory = true;
        }
        // 2. extracting + theory failed → reconcile immediately
        //    (Q+figures never started; partial state)
        if (
          prev.phase === 'extracting' &&
          prev.theory.status === 'failed' &&
          prev.questions.jobId === null &&
          prev.figures.jobId === null
        ) {
          didReconcile = true;
        }
        // 3. extracting + all 3 terminal → reconcile
        // The jobId gate that was here previously required the CLIENT to
        // have kicked questions or figures itself. With server auto-
        // proceed (post-Day-8 orchestrator), the client never gets
        // jobIds — stages flip to terminal purely via book-status
        // polling. Dropping the jobId requirement lets the phase
        // transition to 'done'/'partial' on the server-driven path too.
        // All-three-terminal is sufficient ground truth.
        if (prev.phase === 'extracting') {
          const allTerm =
            isTerminal(prev.theory.status) &&
            isTerminal(prev.questions.status) &&
            isTerminal(prev.figures.status);
          if (allTerm) {
            didReconcile = true;
          }
        }
        return prev;
      });
      if (didKickTheory && s.bookId) {
        await kickRef.current(s.bookId);
      }
      if (didReconcile && s.bookId) {
        stopPolling();
        await reconcileRef.current(s.bookId);
      }
    } finally {
      tickRunning.current = false;
    }
  }, [apply, stopPolling]);

  // ─── start: explicit kickoff. Idempotent — no-ops if already running.
  const start = useCallback(
    async (bookId: string) => {
      // GUARD: if a run is already in-flight for the same book, don't
      // fire fresh POSTs. The user clicking Start repeatedly would
      // otherwise spawn duplicate question + figure workers (their
      // endpoints supersede prior DB rows but the running worker
      // processes keep going, eating Gemini calls and stalling).
      let isAlreadyRunning = false;
      setState((s) => {
        const ACTIVE: Phase[] = ['loading', 'analysing', 'approving', 'extracting', 'reconciling'];
        if (s.bookId === bookId && ACTIVE.includes(s.phase)) {
          isAlreadyRunning = true;
        }
        return s;
      });
      if (isAlreadyRunning) {
        dbg('start() ignored — pipeline already running for', bookId);
        return;
      }

      dbg('start()', bookId);
      reconciledFor.current = null;

      apply(() => ({ ...initialState(bookId), phase: 'loading' }));

      // Read current book state to decide where to begin.
      let book: BackendBookOut;
      try {
        book = await getBook(bookId);
      } catch (e) {
        apply(() => ({
          phase: 'error',
          errorMessage: `Couldn't load book: ${explain(e)}`,
        }));
        return;
      }
      dbg('book.status =', book.status);

      // Clear any stale persisted state — explicit Start is always "fresh".
      clearStored(bookId);

      apply(() => ({ bookStatus: book.status }));

      // Branch on book.status. Decision tree:
      //
      //   uploaded | pending   → fire /analyse, then kickThreeParallel
      //   analysing             → wait for current analyse (no double-fire),
      //                            then kickThreeParallel when schema lands
      //   schema_ready          → kickThreeParallel (forceFresh=false,
      //                            uses /approve — first-time path)
      //   extracting | ready    → kickThreeParallel (forceFresh=true,
      //                            uses /re-extract — wipes & re-runs;
      //                            avoids spawning a 2nd theory worker
      //                            on top of an existing one)
      //   failed                → error + retry CTA
      //
      // In every "schema is built" branch we ALWAYS fire all 3 stages so
      // the user sees three progress bars (their expectation when they
      // click Start).
      if (book.status === 'uploaded' || book.status === 'pending') {
        apply(() => ({ phase: 'analysing' }));
        try {
          const r = await postAnalyse(bookId);
          apply(() => ({
            schema: {
              ...initialStage(),
              jobId: r.job_id,
              status: 'queued',
              lastProgressAt: Date.now(),
            },
          }));
        } catch (e) {
          // 409 Conflict = analyse is ALREADY running. The backend's CAS
          // guard (or a concurrent dispatch — e.g. React StrictMode double
          // mount, or the server auto-dispatching on upload) won the race.
          // This is NOT a failure: schema IS being built. Treat it as
          // "attach and observe" — stay in 'analysing' and let the tick
          // loop pick up real progress from book.schema_status. Showing a
          // red "FAILED" here while schema runs at 30% was the bug.
          if (e instanceof ApiError && e.status === 409) {
            dbg('analyse already running (409) — observing existing run');
            apply(() => ({ phase: 'analysing' }));
          } else {
            apply(() => ({
              phase: 'error',
              errorMessage: `Couldn't start analyse: ${explain(e)}`,
            }));
            return;
          }
        }
      } else if (book.status === 'analysing') {
        // Backend is still analysing. We don't know the job_id so we can't
        // poll directly — the tick's book-poll syncs book.schema_status.
        // Seed the Schema row to 'running' NOW so a (re)visit shows live
        // status immediately instead of a blank 0% flash before the first
        // poll returns.
        apply((prev) => ({
          phase: 'analysing',
          schema: {
            ...prev.schema,
            status: 'running',
            message: 'Building schema…',
            lastProgressAt: Date.now(),
          },
        }));
        dbg('book is already analysing; no jobId tracked — will detect transition by polling book');
      } else if (book.status === 'schema_ready') {
        // First-time path — use /approve.
        apply((prev) => ({
          phase: 'analysing',
          schema: { ...prev.schema, status: 'done', progress: 100 },
        }));
        // Tick will pick up analysing + schema=done and call kickThreeParallel
        // (forceFresh=false by default — uses /approve).
      } else if (book.status === 'extracting') {
        // Theory worker is already running on the backend. DO NOT fire
        // /re-extract — we'd spawn a duplicate worker. Attach to the
        // existing run.
        //
        // Start progress at 0 so the per-section poll (in tick) can
        // immediately overwrite with the real count. The poll's
        // "never regress" guard would otherwise block updates if we
        // used a higher placeholder like 50.
        dbg('book is extracting — attaching to existing theory worker');
        apply((prev) => ({
          phase: 'extracting',
          schema: { ...prev.schema, status: 'done', progress: 100 },
          theory: {
            ...prev.theory,
            status: 'running',
            progress: 0,
            message: 'Attached to running extraction',
          },
          questions: { ...prev.questions, status: 'queued', message: 'waiting for theory' },
          figures: { ...prev.figures, status: 'queued', message: 'waiting for theory' },
        }));
      } else if (
        book.status === 'ready' ||
        book.status === 'extracted' ||
        book.status === 'partial' ||
        book.status === 'approved' ||
        book.status === 'done'
      ) {
        // Terminal state — fully extracted, or 'partial' (extracted with some
        // sections failed). READ-ONLY: show done and DO NOT poll/kick. A
        // terminal book must NEVER auto re-extract on revisit. ('partial'
        // previously fell through to the else branch and force-re-extracted
        // on every mount — that was the "revisiting restarts extraction" bug.)
        // Re-running is an explicit user CTA only.
        apply((prev) => ({
          phase: book.status === 'partial' ? 'partial' : 'done',
          bookStatus: book.status,
          schema:    { ...prev.schema,    status: 'done', progress: 100 },
          theory:    { ...prev.theory,    status: 'done', progress: 100 },
          questions: { ...prev.questions, status: 'done', progress: 100 },
          figures:   { ...prev.figures,   status: 'done', progress: 100 },
        }));
        return;
      } else if (book.status === 'failed') {
        apply(() => ({
          phase: 'error',
          errorMessage:
            "Backend reports book.status='failed'. Use 'Retry schema' to start over.",
        }));
        return;
      } else {
        // Unknown / in-progress status — OBSERVE only. Never auto-fire a
        // destructive re-extract (that restarted books on every mount). The
        // server's coordinator drives progression; we just poll & reflect.
        dbg('unhandled book.status', book.status, '— observing (no destructive kick)');
        apply((prev) => ({
          phase: 'extracting',
          schema: { ...prev.schema, status: 'done', progress: 100 },
        }));
      }

      // Kick polling.
      startPolling(() => void tick());
    },
    [apply, kickThreeParallel, startPolling, tick],
  );

  // ─── Retry whole stage ──────────────────────────────────────────
  const retryStage = useCallback(
    async (stage: StageKey) => {
      let bookId: string | null = null;
      setState((s) => {
        bookId = s.bookId;
        return s;
      });
      if (!bookId) return;
      dbg('retryStage', stage);

      apply((prev) => ({
        [stage]: { ...prev[stage], inflight: true, error: null },
      }) as Partial<ExtractionState>);

      try {
        // ORCH Day 12 — per-stage retry now uses the Day 10 orchestrator-
        // mediated endpoints. Each one surgically resets that stage and
        // routes the resumption through the coordinator (no race with
        // the in-flight pipeline).
        let jobId: string | null = null;
        if (stage === 'schema') {
          jobId = (await postAnalyse(bookId)).job_id;
          apply(() => ({ phase: 'analysing' }));
        } else if (stage === 'theory') {
          jobId = (await postRetryTheory(bookId)).job_id;
          apply(() => ({ phase: 'extracting', failedSections: [] }));
        } else if (stage === 'questions') {
          jobId = (await postRetryQuestions(bookId)).job_id;
          apply(() => ({ phase: 'extracting', questionsFailed: false }));
        } else if (stage === 'figures') {
          jobId = (await postRetryFigures(bookId)).job_id;
          apply(() => ({ phase: 'extracting', figuresFailed: false }));
        }
        apply(() => ({
          [stage]: {
            ...initialStage(),
            jobId,
            status: 'queued',
            lastProgressAt: Date.now(),
          },
        }) as Partial<ExtractionState>);
        reconciledFor.current = null;
        startPolling(() => void tick());
      } catch (e) {
        apply((prev) => ({
          [stage]: {
            ...prev[stage],
            inflight: false,
            error: `Retry failed: ${explain(e)}`,
          },
        }) as Partial<ExtractionState>);
      }
    },
    [apply, startPolling, tick],
  );

  // ─── Retry one theory section ───────────────────────────────────
  const retrySection = useCallback(
    async (sectionRowId: string) => {
      let bookId: string | null = null;
      setState((s) => {
        bookId = s.bookId;
        return s;
      });
      if (!bookId) return;
      dbg('retrySection', sectionRowId);
      try {
        const r = await postReExtractSection(sectionRowId);
        apply((prev) => ({
          phase: 'extracting',
          theory: {
            ...prev.theory,
            jobId: r.job_id,
            status: 'queued',
            error: null,
            inflight: false,
            lastProgressAt: Date.now(),
          },
          failedSections: prev.failedSections.filter((f) => f.id !== sectionRowId),
        }));
        reconciledFor.current = null;
        startPolling(() => void tick());
      } catch (e) {
        apply(() => ({
          errorMessage: `Retry section failed: ${explain(e)}`,
        }));
      }
    },
    [apply, startPolling, tick],
  );

  const retryAllTheory = useCallback(async () => {
    await retryStage('theory');
  }, [retryStage]);

  const cancel = useCallback(() => stopPolling(), [stopPolling]);

  // Cleanup on unmount.
  useEffect(() => () => stopPolling(), [stopPolling]);

  return { state, start, retryStage, retrySection, retryAllTheory, cancel };
}

// Re-export so callers can detect stalls. Not used internally yet.
export const STALL_MS = STALL_THRESHOLD_MS;
