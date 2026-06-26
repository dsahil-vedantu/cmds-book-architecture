// Questions client — fetch the latest question bank for a book + walk
// the section-grouped question list returned by the backend.

import { useCallback, useEffect, useState } from 'react';

import { ApiError, req } from './client';

export type QuestionBank = {
  id: string;
  book_id: string;
  title: string | null;
  subject: string | null;
  status: string;
  question_count?: number;
  last_error?: string | null;
};

// Embedded figure on a question — backend joins figure_references → figures
// for this question and returns the data the UI needs to render the image
// inline at `placement_char_offset` inside `raw_text`.
export type QuestionEmbeddedFigure = {
  ref_id: string;
  figure_id: string;
  label: string;
  caption: string;
  // Gemini-extracted 2-3 sentence description of what the figure shows.
  // Used as the PLACEHOLDER info text for UNLABELLED figures (where label
  // and caption are both empty strings). The user always sees SOMETHING
  // describing the image, even when the source PDF didn't print a label.
  description?: string;
  variant: 'original' | 'regen';
  image_url: string;
  placement_kind?: string;
  placement_char_offset?: number | null;
  // Explicit body target — embedder-computed. Tells the UI whether to
  // render this figure under the question stem or inside the solution
  // block. NULL on legacy refs (rendered as question body by default).
  body_target?: 'question' | 'solution' | null;
};

export type ExtractedQuestion = {
  id: string;
  section_ref: string;
  // Canonical UUID FK to the Section row this question belongs to.
  // Frontend joins on this (not section_ref slug) so schema/db slug
  // divergence — see the Class-9th-Maths blank-tab bug — can never
  // hide questions again. Null on legacy rows; slug is the fallback.
  section_uuid?: string | null;
  section_title: string | null;
  page_start: number | null;
  page_end: number | null;
  raw_text: string;
  status: string;
  question_number?: string | null;
  exercise_ref?: string | null;
  question_type?: string | null;
  has_options: boolean;
  solution_text?: string | null;
  has_solution: boolean;
  kind?: string | null; // 'example' | 'question' | etc.
  is_hidden?: boolean;
  // Figures referenced inline within this question's text. Empty if the
  // figure_embedder didn't find any "Fig. X.Y" reference in raw_text.
  embedded_figures?: QuestionEmbeddedFigure[];
  // Step 2 — chained LaTeX/SVG diagram regen. Present only on regenerated
  // variants whose source question carried a diagram. svg_preview renders
  // live in the browser; latex_code is the compilable standalone source.
  image_regen_hint?: { needed: boolean; reason: string } | null;
  regenerated_diagram?: RegeneratedDiagram | null;
};

export type RegeneratedDiagram = {
  fallback_to_original: boolean;
  subject: string;
  latex_code: string;
  svg_preview: string;
  description: string;
};

export type RejectedItem = {
  id: string;
  section_ref: string;
  page_start: number | null;
  raw_text: string;
  reject_reason: string | null;
};

export type SectionQuestions = {
  section_ref: string;
  // Canonical UUID FK to the Section row. See ExtractedQuestion.section_uuid
  // — this is the same identity, surfaced at the group level so the UI
  // can join `bs.section_uuid === selectedSection.id` instead of slug-
  // matching. Null when the schema slug doesn't map to any DB Section row
  // (legacy / pre-race-fix books); selector falls back to section_ref.
  section_uuid?: string | null;
  section_title: string | null;
  questions: ExtractedQuestion[];
  extracted: number;
  identified: number;
  missed: number;
  by_kind?: Record<string, number>;
  // Pending rejected items for this section (backend already returns
  // this; field was missing from the type so TypeScript users couldn't
  // see it). Used to surface "Mark all reviewed" bulk action.
  rejected?: RejectedItem[];
};

export type QuestionBankDetail = {
  bank_id: string;
  book_id: string;
  title: string | null;
  status: string;
  total_questions: number;
  sections: SectionQuestions[];
};

// ─── HTTP ─────────────────────────────────────────────────────────
export const listBanks = (bookId: string) =>
  req<QuestionBank[]>(`/api/books/${bookId}/question-banks`);

export const getBankQuestions = (bankId: string) =>
  req<QuestionBankDetail>(`/api/question-banks/${bankId}/questions`);

// ─── Question REGEN HTTP (for RegenReviewPage Questions tab) ──────
export type QuestionRegeneration = {
  id: string;
  book_id: string;
  bank_id: string;
  status: string;
  custom_instructions: string | null;
  created_at: string;
};

export type RegenQuestionsResponse = {
  regen: QuestionRegeneration;
  sections: SectionQuestions[];
};

export const listQuestionRegenerations = (bookId: string) =>
  req<QuestionRegeneration[]>(`/api/books/${bookId}/question-regenerations`);

export const getRegenQuestions = (regenId: string) =>
  req<RegenQuestionsResponse>(
    `/api/question-regenerations/${regenId}/questions`,
  );

export const retryRegenSection = (
  regenId: string,
  body: { section_ref: string; custom_instructions?: string | null },
) =>
  req(`/api/question-regenerations/${regenId}/retry-section`, {
    method: 'POST',
    body: JSON.stringify(body),
  });

export const saveQuestionRegeneration = (regenId: string) =>
  req(`/api/question-regenerations/${regenId}/save`, { method: 'POST' });

// Reseed ONE regenerated question's LaTeX/SVG diagram with an optional
// customization instruction (mirrors "Reseed this section" but for the figure).
// Refines the current diagram via the LLM, persists it, and returns the new one.
export const regenerateQuestionDiagram = (
  questionId: string,
  customInstructions?: string | null,
) =>
  req<{
    ok: boolean;
    question_id: string;
    regenerated_diagram: RegeneratedDiagram;
  }>(`/api/question-banks/questions/${questionId}/regenerate-diagram`, {
    method: 'POST',
    body: JSON.stringify({ custom_instructions: customInstructions ?? null }),
  });

// Hide / unhide a single question (used in the reviewer UI to drop a
// generated question the user doesn't want without re-running regen).
export const hideQuestion = (questionId: string) =>
  req(`/api/question-banks/questions/${questionId}/hide`, { method: 'PATCH' });

export const unhideQuestion = (questionId: string) =>
  req(`/api/question-banks/questions/${questionId}/unhide`, { method: 'PATCH' });

// Bulk-restore every pending rejected_question for a bank. Also fires
// the Q-2 solution-completeness retry server-side so newly-restored
// rows with empty solution_text get rescued in the same call.
export const restoreAllRejected = (bankId: string) =>
  req<{
    ok: boolean;
    restored: number;
    skipped: number;
    solutions_rescued?: number;
    figures_attached?: number;
  }>(
    `/api/question-banks/${bankId}/rejected/restore-all`,
    { method: 'POST' },
  );

// ─── Hook ─────────────────────────────────────────────────────────
type State =
  | { kind: 'loading' }
  | { kind: 'empty' } // no bank yet
  | { kind: 'ready'; bank: QuestionBank; detail: QuestionBankDetail }
  | { kind: 'error'; error: string };

/**
 * Loads the LATEST question bank for the book and pulls its full
 * section-grouped question list. Returns 'empty' when no bank exists.
 */
export function useBookQuestions(bookId: string | undefined) {
  const [state, setState] = useState<State>({ kind: 'loading' });

  const load = useCallback(async () => {
    if (!bookId) {
      setState({ kind: 'error', error: 'No book id' });
      return;
    }
    setState({ kind: 'loading' });
    try {
      const banks = await listBanks(bookId);
      if (banks.length === 0) {
        setState({ kind: 'empty' });
        return;
      }
      // Pick the latest ready bank, or fall back to the newest row.
      const ready = banks.filter((b) => b.status === 'ready');
      const bank = ready[ready.length - 1] ?? banks[banks.length - 1];
      const detail = await getBankQuestions(bank.id);
      setState({ kind: 'ready', bank, detail });
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `Backend ${err.status}: ${err.message}`
          : err instanceof Error
          ? err.message
          : 'Unknown error';
      setState({ kind: 'error', error: msg });
    }
  }, [bookId]);

  useEffect(() => {
    void load();
  }, [load]);

  return { ...state, refetch: load };
}
