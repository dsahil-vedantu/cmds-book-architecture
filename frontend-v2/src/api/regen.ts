// Regeneration API client. Mirrors the existing backend endpoint contracts
// exactly — no new endpoints. V-Studio just wraps them.

import { ApiError, req } from './client';

// ─── Theory ──────────────────────────────────────────────────────────
//
// POST /api/books/:id/regenerate
//
// Body shape (matches backend's RegenParams + optional section_ids):
//
//   {
//     intensity:         'light' | 'moderate' | 'heavy',
//     tone:              'academic_rigorous' | 'academic_pedagogical' | 'academic_interactive',
//     equations_handling:'preserve' | 'explain',
//     diagrams_handling: 'preserve' | 'describe',
//     analogies:         'none' | 'add_one' | 'add_multiple',
//     structure:         'identical' | 'reorganize',
//     language:          'en' | 'hi',        // default 'en'
//     target_audience:   string | null,
//     custom_instructions: string | null,
//     section_ids:       string[] | null,   // null/omitted = all sections
//   }

export type TheoryTone =
  | 'academic_rigorous'
  | 'academic_pedagogical'
  | 'academic_interactive';

export type TheoryLanguage = 'en' | 'hi';

export type TheoryRegenParams = {
  intensity: 'light' | 'moderate' | 'heavy';
  tone: TheoryTone;
  equations_handling: 'preserve' | 'explain';
  diagrams_handling: 'preserve' | 'describe';
  analogies: 'none' | 'add_one' | 'add_multiple';
  structure: 'identical' | 'reorganize';
  language?: TheoryLanguage;
  target_audience?: string | null;
  custom_instructions?: string | null;
  // v3 recap rules (opt-in). Empty / omitted = no recap behavior.
  // Backend ignores when THEORY_REGEN_PROMPT_VERSION != "v3".
  recap_rule_ids?: string[];
};

// ─── Recap rules catalog (v3 only) ───────────────────────────────────
export type RecapRule = {
  id: string;
  label: string;
  kind: 'rename' | 'redistribute';
  source_labels: string[];
  source_section_patterns: string[];
  description: string;
};

export const getRecapRules = () => req<RecapRule[]>(`/api/recap-rules`);

// Defaults tuned so regen produces VISIBLY different output while
// keeping block order stable. Earlier defaults (intensity=moderate,
// structure=identical, analogies=none) told the LLM to keep content
// nearly unchanged — making reviewers think regen was broken.
//
// New defaults:
//   intensity: heavy     → sentences are significantly rewritten
//   structure: identical → block order preserved (no equation reordering)
//   analogies: add_one   → one helpful analogy per section adds new value
//   equations: preserve  → math is character-for-character identical
//   diagrams:  preserve  → figures untouched
//
// Net effect: theory prose visibly different, but structure/math/figures
// are stable. Reviewer immediately sees that regen IS doing something.
export const defaultTheoryParams: TheoryRegenParams = {
  intensity: 'heavy',
  tone: 'academic_pedagogical',
  equations_handling: 'preserve',
  diagrams_handling: 'preserve',
  analogies: 'add_one',
  structure: 'identical',
  language: 'en',
  target_audience: null,
  custom_instructions: null,
  recap_rule_ids: [],
};

export type TheoryRegenResponse = {
  book_id: string;
  job_id: string;
  regen_id?: string;
  status: string;
};

export const postRegenTheory = (
  bookId: string,
  params: TheoryRegenParams,
  sectionIds?: string[] | null,
) =>
  req<TheoryRegenResponse>(`/api/books/${bookId}/regenerate`, {
    method: 'POST',
    body: JSON.stringify({
      ...params,
      section_ids: sectionIds ?? null,
    }),
  });

// ─── Questions ───────────────────────────────────────────────────────
//
// POST /api/question-banks/:bank_id/regenerate
//
// Backend's RegenerateRequest:
//   scope:           'bank' | 'sections'
//   section_refs:    string[] | null
//   custom_instructions: string | null
//   similarity_level: enum | null
//   question_type:   string | null  (max 64 chars)
//   priority_mode:   'override' (locked)
//   label:           string | null

export type QuestionsSimilarity =
  | 'numbers_only'
  | 'numbers_and_rephrase'
  | 'numbers_rephrase_add_concept'
  | 'new_question_same_topic'
  | 'same_topic_add_one_concept'
  | 'same_chapter_any_topic';

export type QuestionsPriorityMode = 'override';

export type QuestionsRegenParams = {
  scope: 'bank' | 'sections';
  section_refs?: string[] | null;
  custom_instructions?: string | null;
  similarity_level?: QuestionsSimilarity | null;
  question_type?: string | null;
  priority_mode?: QuestionsPriorityMode | null;
  label?: string | null;
};

export const defaultQuestionsParams: QuestionsRegenParams = {
  scope: 'bank',
  section_refs: null,
  custom_instructions: null,
  similarity_level: 'numbers_and_rephrase',
  question_type: null,
  priority_mode: 'override',
  label: null,
};

export type QuestionsRegenResponse = {
  regen_id: string;
  job_id: string;
  status: string;
};

export const postRegenQuestions = (
  bankId: string,
  params: QuestionsRegenParams,
) =>
  req<QuestionsRegenResponse>(`/api/question-banks/${bankId}/regenerate`, {
    method: 'POST',
    body: JSON.stringify(params),
  });

// ─── Figures ─────────────────────────────────────────────────────────
//
// POST /api/books/:id/sections/:section_ref/regenerate-figures
// (per-section only — to regen all figures in book, loop over sections)
//
// Body:
//   style:               'enhanced' | 'original'
//   custom_instructions: string | null
//   watermark_clean:     bool
//   overlay:             bool
//   image_model:         string | null
//   ocr_model:           string | null

export type FiguresRegenParams = {
  style: 'enhanced' | 'original';
  custom_instructions?: string | null;
  watermark_clean?: boolean;
  overlay?: boolean;
};

export const defaultFiguresParams: FiguresRegenParams = {
  style: 'enhanced',
  custom_instructions: null,
  watermark_clean: false,
  overlay: true,
};

export type FiguresRegenResponse = {
  book_id: string;
  section_ref: string;
  job_id: string;
  status: string;
};

export const postRegenFiguresSection = (
  bookId: string,
  sectionRef: string,
  params: FiguresRegenParams,
) =>
  req<FiguresRegenResponse>(
    `/api/books/${bookId}/sections/${sectionRef}/regenerate-figures`,
    {
      method: 'POST',
      body: JSON.stringify(params),
    },
  );

/**
 * Fire figure regen for every section that has figures. Backend has no
 * whole-book regen endpoint — we just loop on the client. Returns the
 * list of {section_ref, job_id} for whatever succeeded, plus errors.
 */
export async function postRegenFiguresAllSections(
  bookId: string,
  sectionRefs: string[],
  params: FiguresRegenParams,
): Promise<{
  jobs: Array<{ section_ref: string; job_id: string }>;
  failures: Array<{ section_ref: string; error: string }>;
}> {
  const results = await Promise.allSettled(
    sectionRefs.map((ref) => postRegenFiguresSection(bookId, ref, params)),
  );
  const jobs: Array<{ section_ref: string; job_id: string }> = [];
  const failures: Array<{ section_ref: string; error: string }> = [];
  results.forEach((r, i) => {
    const ref = sectionRefs[i];
    if (r.status === 'fulfilled') {
      jobs.push({ section_ref: ref, job_id: r.value.job_id });
    } else {
      const err =
        r.reason instanceof ApiError
          ? `Backend ${r.reason.status}: ${r.reason.message}`
          : r.reason instanceof Error
          ? r.reason.message
          : 'Unknown error';
      failures.push({ section_ref: ref, error: err });
    }
  });
  return { jobs, failures };
}
