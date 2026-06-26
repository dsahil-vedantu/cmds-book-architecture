// Regeneration orchestrator hook. Mirrors the extraction orchestrator's
// shape but polls the 3 regen pipelines (theory regen / question regen /
// per-section figure regens).
//
// Job IDs are passed in via the Regen Config page (stored in sessionStorage
// keyed by bookId). On mount the hook reads those IDs, polls /api/jobs/:id
// every 2s, and aggregates progress per pipeline + overall.
//
// On reconcile (all jobs settled) we fetch:
//   • /api/regenerations/:regen_id     → theory regen output (blocks_by_section)
//   • /api/question-regenerations/:id  → question regen output
//   • per-figure-section jobs done → figures regen complete
//
// No backend changes — same endpoints the existing pipeline uses.

import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiError, req } from './client';
import type { components } from './generated';

type BackendJobOut = components['schemas']['JobOut'];

// ─── Session-stored job IDs (set by RegenConfigPage) ──────────────
//
// Shape:
//   { bookId, startedAt, theory?: {job_id, regen_id}, questions?: {job_id, regen_id},
//     figures?: Array<{section_ref, job_id}> }

export type RegenSessionKick = {
  bookId: string;
  startedAt: number;
  theory: { job_id: string; regen_id?: string } | null;
  questions: { job_id: string; regen_id?: string } | null;
  figures: Array<{ section_ref: string; job_id: string }> | null;
};

const KICK_KEY = (bookId: string) => `vstudio.regenKick.${bookId}`;

export function saveRegenKick(kick: RegenSessionKick): void {
  try {
    sessionStorage.setItem(KICK_KEY(kick.bookId), JSON.stringify(kick));
  } catch {
    /* sessionStorage may be disabled — orchestrator will just show 'no run' */
  }
}

export function loadRegenKick(bookId: string): RegenSessionKick | null {
  try {
    const raw = sessionStorage.getItem(KICK_KEY(bookId));
    return raw ? (JSON.parse(raw) as RegenSessionKick) : null;
  } catch {
    return null;
  }
}

export function clearRegenKick(bookId: string): void {
  try {
    sessionStorage.removeItem(KICK_KEY(bookId));
  } catch {
    /* ignore */
  }
}

// ─── Hook state ────────────────────────────────────────────────────

export type RegenStageKey = 'theory' | 'questions' | 'figures';
export type JobStatus = 'queued' | 'running' | 'done' | 'failed' | 'unknown';

export type RegenStageState = {
  /** ON if user toggled this pipeline ON in config. */
  enabled: boolean;
  /** Single primary job (for theory + questions). */
  jobId: string | null;
  /** All figure job IDs (per section). */
  figureJobs: Array<{ section_ref: string; job_id: string }>;
  /** Aggregate status. */
  status: JobStatus;
  /** Progress 0..100. */
  progress: number;
  message: string | null;
  error: string | null;
  /** Backend's Regeneration row id (for fetching output). */
  regenId: string | null;
};

export type RegenPhase =
  | 'idle'
  | 'loading'
  | 'running'
  | 'reconciling'
  | 'done'
  | 'partial'
  | 'error';

export type RegenState = {
  phase: RegenPhase;
  bookId: string | null;
  overallPct: number;
  theory: RegenStageState;
  questions: RegenStageState;
  figures: RegenStageState;
  errorMessage: string | null;
  startedAt: number | null;
};

const POLL_MS = 2000;

// Theory / Questions / Figures relative weights — for overall %.
const WEIGHTS: Record<RegenStageKey, number> = {
  theory: 0.55,
  questions: 0.30,
  figures: 0.15,
};

// ─── Helpers ───────────────────────────────────────────────────────

function emptyStage(): RegenStageState {
  return {
    enabled: false,
    jobId: null,
    figureJobs: [],
    status: 'unknown',
    progress: 0,
    message: null,
    error: null,
    regenId: null,
  };
}

function initialState(bookId: string | null = null): RegenState {
  return {
    phase: 'idle',
    bookId,
    overallPct: 0,
    theory: emptyStage(),
    questions: emptyStage(),
    figures: emptyStage(),
    errorMessage: null,
    startedAt: null,
  };
}

function normalizeStatus(raw: string): JobStatus {
  if (
    raw === 'succeeded' ||
    raw === 'done' ||
    raw === 'completed' ||
    raw === 'success' ||
    raw === 'complete'
  )
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

const isTerminal = (s: JobStatus) => s === 'done' || s === 'failed';

function explain(err: unknown): string {
  if (err instanceof ApiError) return `Backend ${err.status}: ${err.message}`;
  if (err instanceof Error) return err.message;
  return 'Unknown error';
}

function stagePct(st: RegenStageState): number {
  if (!st.enabled) return 100; // skipped pipelines count as "done"
  if (st.status === 'done') return 100;
  return Math.max(0, Math.min(100, st.progress || 0));
}

function computeOverall(s: RegenState): number {
  return Math.round(
    stagePct(s.theory) * WEIGHTS.theory +
      stagePct(s.questions) * WEIGHTS.questions +
      stagePct(s.figures) * WEIGHTS.figures,
  );
}

const getJob = (jobId: string) => req<BackendJobOut>(`/api/jobs/${jobId}`);

// ─── The hook ──────────────────────────────────────────────────────

export type UseRegenPipeline = {
  state: RegenState;
  /** Attach to a previously-kicked regen run for this book. */
  attach: (bookId: string) => void;
  /** Stop polling. */
  cancel: () => void;
};

export function useRegenPipeline(): UseRegenPipeline {
  const [state, setState] = useState<RegenState>(initialState());
  const pollTimer = useRef<number | null>(null);
  const tickRunning = useRef(false);

  const stopPolling = useCallback(() => {
    if (pollTimer.current != null) {
      window.clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
  }, []);

  // ─── Tick: poll active jobs + aggregate ────────────────────────
  const tick = useCallback(async () => {
    if (tickRunning.current) return;
    tickRunning.current = true;
    try {
      let snapshot: RegenState | null = null;
      setState((s) => {
        snapshot = s;
        return s;
      });
      if (!snapshot) return;
      const s: RegenState = snapshot;
      if (!s.bookId) return;

      // ─── Theory job ───
      let theoryPatch: Partial<RegenStageState> | null = null;
      if (s.theory.enabled && s.theory.jobId && !isTerminal(s.theory.status)) {
        try {
          const j = await getJob(s.theory.jobId);
          theoryPatch = {
            status: normalizeStatus(j.status),
            progress: Number(j.progress) || 0,
            message: j.message ?? null,
            error: j.error ?? null,
          };
        } catch (e) {
          // tolerate transient — keep prior state
          void e;
        }
      }

      // ─── Questions job ───
      let questionsPatch: Partial<RegenStageState> | null = null;
      if (
        s.questions.enabled &&
        s.questions.jobId &&
        !isTerminal(s.questions.status)
      ) {
        try {
          const j = await getJob(s.questions.jobId);
          questionsPatch = {
            status: normalizeStatus(j.status),
            progress: Number(j.progress) || 0,
            message: j.message ?? null,
            error: j.error ?? null,
          };
        } catch {
          /* transient */
        }
      }

      // ─── Figures: aggregate over per-section jobs ───
      let figuresPatch: Partial<RegenStageState> | null = null;
      if (s.figures.enabled && s.figures.figureJobs.length > 0) {
        const results = await Promise.allSettled(
          s.figures.figureJobs.map((f) => getJob(f.job_id)),
        );
        const statuses: JobStatus[] = results.map((r) =>
          r.status === 'fulfilled'
            ? normalizeStatus(r.value.status)
            : 'unknown',
        );
        const done = statuses.filter((st) => st === 'done').length;
        const failed = statuses.filter((st) => st === 'failed').length;
        const terminal = done + failed;
        const total = statuses.length;
        const aggStatus: JobStatus =
          terminal === total
            ? failed > 0 && done === 0
              ? 'failed'
              : 'done'
            : statuses.some((st) => st === 'running' || st === 'queued')
            ? 'running'
            : 'unknown';
        figuresPatch = {
          status: aggStatus,
          progress: total > 0 ? Math.round((terminal / total) * 100) : 0,
          message:
            failed > 0
              ? `${done} of ${total} figures sections done (${failed} failed)`
              : `${done} of ${total} figures sections done`,
        };
      }

      // ─── Apply patches ───
      if (theoryPatch || questionsPatch || figuresPatch) {
        setState((prev) => {
          const next: RegenState = {
            ...prev,
            theory: theoryPatch ? { ...prev.theory, ...theoryPatch } : prev.theory,
            questions: questionsPatch
              ? { ...prev.questions, ...questionsPatch }
              : prev.questions,
            figures: figuresPatch
              ? { ...prev.figures, ...figuresPatch }
              : prev.figures,
          };
          next.overallPct = computeOverall(next);
          return next;
        });
      }

      // ─── Phase transition ───
      setState((prev) => {
        if (prev.phase !== 'running') return prev;
        const stages = (['theory', 'questions', 'figures'] as RegenStageKey[]).filter(
          (k) => prev[k].enabled,
        );
        const allTerminal = stages.every((k) =>
          isTerminal(prev[k].status),
        );
        if (!allTerminal) return prev;
        // All settled → flip to done|partial
        const anyFailure = stages.some(
          (k) => prev[k].status === 'failed',
        );
        return { ...prev, phase: anyFailure ? 'partial' : 'done' };
      });

      // Stop polling when terminal.
      const after = stateRef(setState);
      if (after.phase === 'done' || after.phase === 'partial') {
        stopPolling();
      }
    } finally {
      tickRunning.current = false;
    }
  }, [stopPolling]);

  // ─── Attach: read sessionStorage + seed state + start polling ──
  const attach = useCallback(
    (bookId: string) => {
      const kick = loadRegenKick(bookId);
      if (!kick) {
        setState(() => ({
          ...initialState(bookId),
          phase: 'error',
          errorMessage:
            'No regeneration in flight for this book. Start one from the Regen Config page.',
        }));
        return;
      }

      const fresh = initialState(bookId);
      fresh.phase = 'running';
      fresh.startedAt = kick.startedAt;

      if (kick.theory) {
        fresh.theory.enabled = true;
        fresh.theory.jobId = kick.theory.job_id;
        fresh.theory.regenId = kick.theory.regen_id ?? null;
        fresh.theory.status = 'queued';
      }
      if (kick.questions) {
        fresh.questions.enabled = true;
        fresh.questions.jobId = kick.questions.job_id;
        fresh.questions.regenId = kick.questions.regen_id ?? null;
        fresh.questions.status = 'queued';
      }
      if (kick.figures && kick.figures.length > 0) {
        fresh.figures.enabled = true;
        fresh.figures.figureJobs = kick.figures;
        fresh.figures.status = 'queued';
      }

      // If nothing was actually kicked (all three null), error.
      if (
        !fresh.theory.enabled &&
        !fresh.questions.enabled &&
        !fresh.figures.enabled
      ) {
        fresh.phase = 'error';
        fresh.errorMessage = 'No regeneration jobs found in the saved kick.';
      }

      fresh.overallPct = computeOverall(fresh);
      setState(() => fresh);

      // Start polling.
      stopPolling();
      pollTimer.current = window.setInterval(() => void tick(), POLL_MS);
      void tick();
    },
    [stopPolling, tick],
  );

  const cancel = useCallback(() => stopPolling(), [stopPolling]);

  // Cleanup on unmount.
  useEffect(() => () => stopPolling(), [stopPolling]);

  return { state, attach, cancel };
}

// ─── Tiny helper to read latest state synchronously inside tick ────
function stateRef(setStateFn: (fn: (s: RegenState) => RegenState) => void): RegenState {
  let captured!: RegenState;
  setStateFn((s) => {
    captured = s;
    return s;
  });
  return captured;
}
