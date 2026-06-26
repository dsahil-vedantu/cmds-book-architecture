export const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

export type UUID = string;

export interface Book {
  id: UUID;
  title: string;
  subject: string | null;
  pdf_url: string | null;
  schema_: BookSchema | null;
  analyser: AnalyserResult | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface AnalyserResult {
  pdf_type: "digital" | "scanned" | "mixed";
  estimated_pages: number;
  estimated_words: number;
  document_title: string;
  subject: string;
  has_equations: boolean;
  has_tables: boolean;
  has_diagrams: boolean;
}

export interface BookSchema {
  document_title: string;
  subject: string;
  sections: SchemaSection[];
  exclusion_summary: string[];
  excluded_sections?: ExcludedSection[];
}

export interface ExcludedSection {
  title: string;
  reason?: string | null;
  page_start?: number | null;
  page_end?: number | null;
  expected_question_count?: number;
  subsections?: ExcludedSection[];
}

export interface SchemaSection {
  id: string;
  level: number;
  title: string;
  type: "chapter" | "section" | "subsection" | "excluded";
  content_types: string[];
  expected_question_count?: number;
  subsections: SchemaSection[];
}

export type Block =
  | { t: "p"; c: string }
  | { t: "h3"; c: string }
  | { t: "eq"; c: string }
  | { t: "def"; term: string; c: string }
  | { t: "kp"; c: string }
  | { t: "fig"; c: string; label?: string }
  | { t: "list"; items: string[] }
  | { t: "table"; caption: string; headers: string[]; rows: string[][] }
  | { t: "example"; label: string; prob: string; eqs: string[] }
  | { t: "example_ref"; label: string; number?: string; section_id?: string; question_id?: string }
  | { t: "exercise_ref"; label: string; number?: string; section_id?: string; question_id?: string }
  | { t: "question_ref"; label: string; number?: string; section_id?: string; question_id?: string };

/** A figure that should be rendered inline within a section's theory body
 *  or beside a specific question, as computed by the deterministic
 *  figure_embedder service (Phase 1).
 */
export interface EmbeddedFigure {
  /** figure_references row id — used to hide/unhide this placement. */
  ref_id: UUID;
  figure_id: UUID;
  label: string;            // e.g. "Figure 4.7"
  caption: string;
  variant: "original" | "regen";
  image_url: string;        // GET endpoint returning PNG bytes
  placement_kind:
    | "inline"
    | "appended"
    | "unattached"
    | "needs_review"
    | "page_fallback"; // auto-placed at end of page-detected section
  /** For theory placement: render figure AFTER the block at this index.
   *  Null when the placement is "appended" / "unattached". */
  placement_block_idx?: number | null;
  /** For question placement: character offset inside raw_text where the
   *  inline marker would render. Null when "appended". */
  placement_char_offset?: number | null;
}

/** Returned by `/api/books/{id}/unattached-figures`. Same shape as
 *  EmbeddedFigure with extra context fields so the user can decide
 *  where to put them (or leave unattached). */
export interface UnattachedFigure extends EmbeddedFigure {
  context: "theory" | "question";
  section_ref: string;
  page_number: number | null;
}

/* ─── Phase 3 — Final Draft (composer) ─────────────────────────────────── */

export type FinalDraftItem =
  | {
      id: string;
      type: "section_heading";
      parent_section_id: string | null;
      section_id: string;
      title: string;
      level: number;
      regen: boolean;
    }
  | {
      id: string;
      type: "block";
      parent_section_id: string | null;
      block: Block;
    }
  | {
      id: string;
      type: "figure";
      parent_section_id: string | null;
      figure: EmbeddedFigure;
    }
  | {
      id: string;
      type: "question";
      parent_section_id: string | null;
      question: FinalMergeQuestion;
    }
  | {
      id: string;
      type: "custom_text";
      parent_section_id: string | null;
      content: string;
    };

export interface FinalDraft {
  id: UUID;
  book_id: UUID;
  status: "draft" | "exporting" | "exported" | "failed";
  prefer_regen: boolean;
  items: FinalDraftItem[];
  item_count: number;
  last_seeded_at: string | null;
  updated_at: string | null;
}

export type FinalDraftOperation =
  | { op: "reorder"; id: string; after_id: string | "start" }
  | { op: "remove"; id: string }
  | { op: "edit_item"; id: string; patch: Record<string, unknown> }
  | { op: "insert_custom_text"; after_id: string | "start"; content: string }
  | {
      op: "insert_existing";
      after_id: string | "start";
      item: Omit<FinalDraftItem, "id">;
    };

/** Phase 2 — Final Merge document. Stitched theory + questions + figures
 *  in schema order, ready for read-only rendering and export. */
export interface FinalMergeSection {
  section_id: string;
  section_title: string;
  level: number;
  blocks: Block[];
  block_source: "original" | "regen";
  regen_meta: { regen_id: UUID; regen_created_at: string } | null;
  embedded_figures: EmbeddedFigure[];
  questions: FinalMergeQuestion[];
  /** Phase 3 — questions inlined at chip positions within `blocks`.
   *  Key is the block index after which to render (`"-1"` = before any block).
   *  These questions are excluded from the standalone `questions` list. */
  inlined_questions_by_block_idx?: Record<string, FinalMergeQuestion[]>;
}
export interface FinalMergeQuestion {
  id: UUID;
  question_number: string | null;
  exercise_ref: string | null;
  page_start: number | null;
  question_type: string | null;
  raw_text: string;
  has_solution: boolean;
  solution_text: string;
  kind: string;
  embedded_figures: EmbeddedFigure[];
  /** Phase 4 — multimodal regen verdict. Present only on regen questions
   *  whose attached image was flagged for regeneration. */
  image_regen_hint?: { needed: boolean; reason: string } | null;
}
export interface FinalMergeDoc {
  book: { id: UUID; title: string; subject: string };
  sections: FinalMergeSection[];
  unattached_figures: UnattachedFigure[];
}

export interface Section {
  id: UUID;
  book_id: UUID;
  section_id: string;
  title: string;
  level: number | null;
  blocks: Block[];
  qc_local: Record<string, unknown> | null;
  qc_llm: Record<string, unknown> | null;
  status: string;
  attempts: number;
  /** Figures to render inline within this section's theory body. Empty
   *  when no figures are linked to this section (or theory not yet
   *  extracted). Populated by Phase 1 figure_embedder. */
  embedded_figures?: EmbeddedFigure[];
}

export interface Job {
  id: UUID;
  book_id: UUID | null;
  type: string;
  status: string;
  progress: number;
  message: string | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface RegenParams {
  intensity: "light" | "moderate" | "heavy";
  tone: "academic" | "conversational" | "simplified";
  equations_handling: "preserve" | "explain";
  diagrams_handling: "preserve" | "describe";
  analogies: "none" | "add_one" | "add_multiple";
  structure: "identical" | "reorganize";
  language: string;
  target_audience?: string | null;
  custom_instructions?: string | null;
}

export interface Regeneration {
  id: UUID;
  book_id: UUID;
  params: RegenParams;
  blocks_by_section: Record<string, Block[]>;
  qc_drift: Record<string, { pass: boolean; drifted: string[] }> | null;
  created_at: string;
}

export interface ExtractionBlockStats {
  excluded_block_index: number;
  title: string;
  page_start: number | null;
  page_end: number | null;
  section_ref: string | null;
  link_method: string;
  link_confidence: number;
  identified: number;
  extracted: number;
  attempts: number;
  missed: number;
  status: "ok" | "partial" | "empty" | "failed";
  failures: string[];
}

export interface ExtractionStats {
  total_identified: number;
  total_extracted: number;
  missed: number;
  blocks: ExtractionBlockStats[];
  // v3-only (optional — present when worker_version === "v3")
  worker_version?: "v2" | "v3";
  totals?: {
    expected_total: number;
    extracted_total: number;
    complete: number;
    partial: number;
    empty: number;
    failed: number;
  };
  sections?: ExtractionSectionStats[];
  dedup?: {
    checked: number;
    kept: number;
    dropped: number;
    groups: { fingerprint: string; kept_id: string; dropped_ids: string[] }[];
  };
}

export interface ExtractionRejectedItem {
  raw_text?: string;
  _reject_reason?: string;
  [k: string]: unknown;
}

export interface ExtractionSectionStats {
  section_ref: string;
  section_title: string;
  kind: "section" | "excluded";
  page_start: number | null;
  page_end: number | null;
  expected: number | null;
  identified: number;
  extracted: number;
  rejected: number;
  rejected_items: ExtractionRejectedItem[];
  status: "complete" | "partial" | "empty" | "failed" | "skipped";
  attempts: number;
  error: string | null;
}

export interface QuestionBank {
  id: UUID;
  book_id: UUID;
  title: string;
  subject: string | null;
  status: "pending" | "extracting" | "ready" | "failed";
  question_count: number;
  stats: ExtractionStats | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
  active_job_id?: UUID | null;
  active_job?: {
    id: UUID;
    status: string;
    progress: number | null;
    message: string | null;
  } | null;
}

export interface Question {
  id: UUID;
  regen_id?: UUID | null;
  source_question_id?: UUID | null;
  section_ref: string | null;
  section_title: string | null;
  page_start: number | null;
  page_end: number | null;
  raw_text: string;
  status: string;
  // Phase 1 linking context
  excluded_block_ref: string;
  excluded_block_index: number | null;
  link_method: string | null;
  link_confidence: number | null;
  // Stage 2 OCR metadata
  question_number: string | null;
  exercise_ref: string | null;
  chapter_ref: string | null;
  sub_part: string | null;
  question_type: string | null;
  has_options: boolean;
  solution_text: string | null;
  has_solution: boolean;
  kind: string;
  is_hidden: boolean;
  /** Figures associated with this question (Phase 1 figure_embedder).
   *  Empty when no figures linked. */
  embedded_figures?: EmbeddedFigure[];
}

export interface RejectedQuestion {
  id: UUID;
  section_ref: string | null;
  section_title: string | null;
  page_start: number | null;
  page_end: number | null;
  raw_text: string;
  reject_reason: string | null;
  payload: Record<string, unknown> | null;
  status: "pending" | "restored" | "discarded";
  created_at: string | null;
}

export type QuestionKind = "exercise" | "example" | "problem" | "try_it" | "review" | "mcq" | "other";

export interface QuestionBankSectionGroup {
  section_ref: string;
  section_title: string;
  questions: Question[];
  by_kind: Partial<Record<QuestionKind, Question[]>>;
  rejected: RejectedQuestion[];
  identified: number;
  extracted: number;
  missed: number;
}

export interface QuestionBankDetail {
  bank_id: UUID;
  book_id: UUID;
  title: string;
  status: QuestionBank["status"];
  total_questions: number;
  stats: ExtractionStats | null;
  sections: QuestionBankSectionGroup[];
}

export interface QuestionStructureExcludedBlock {
  title: string;
  page_start: number | null;
  page_end: number | null;
  reason: string;
  excluded_index: number;
  excluded_block_ref: string;
  link_method: string;
  link_confidence: number;
  section_ref: string | null;
}

export interface QuestionStructureNode {
  id: string;
  title: string;
  level: number;
  type: string;
  page_start: number | null;
  page_end: number | null;
  question_count: number;
  excluded_blocks: QuestionStructureExcludedBlock[];
  subsections: QuestionStructureNode[];
}

export interface QuestionStructureResponse {
  book_id: UUID;
  document_title: string;
  sections: QuestionStructureNode[];
  unlinked_excluded: QuestionStructureExcludedBlock[];
  summary: {
    total_sections: number;
    total_excluded: number;
    linked_excluded: number;
    unlinked_excluded: number;
  };
}

export interface QuestionRegeneration {
  id: UUID;
  bank_id: UUID;
  book_id: UUID;
  source_regen_id: UUID | null;
  label: string | null;
  scope: "bank" | "sections";
  section_refs: string[];
  custom_instructions: string | null;
  // "partial" is set by the v3 worker when any section failed but at least
  // one section succeeded — the run is usable but not fully complete.
  status: "pending" | "extracting" | "ready" | "partial" | "failed" | "saved";
  job_id: UUID | null;
  question_count: number;
  stats: ExtractionStats | null;
  last_error: string | null;
  created_at: string | null;
  updated_at: string | null;
  finished_at: string | null;
}

// 0014 — variants grouped by source_question_id for the new theory-style UI.
export interface QuestionRegenSourceGroup {
  source_id: UUID | null;
  source: Question | null;
  variants: Question[];
}

export interface QuestionRegenSectionGroup {
  section_ref: string | null;
  section_title: string | null;
  questions: Question[];   // flat list — backward-compat
  sources?: QuestionRegenSourceGroup[];  // grouped by source (new)
}

export interface QuestionRegenQuestionsResponse {
  regen: QuestionRegeneration;
  sections: QuestionRegenSectionGroup[];
}

export interface RegenerateQuestionsParams {
  scope: "bank" | "sections";
  section_refs?: string[] | null;
  custom_instructions?: string | null;
  source_regen_id?: UUID | null;
  label?: string | null;
  // R4 — v3 regen params. All optional with worker-side defaults.
  similarity_level?:
    | "numbers_only"
    | "numbers_and_rephrase"
    | "new_question_same_topic"
    | "same_topic_add_one_concept"
    | "same_chapter_any_topic"
    | null;
  count?: number | null;
  question_type?: string | null;
  priority_mode?: "override" | "layer_on_top" | "specific_aspects" | null;
}

// R10 — section-level retry params
export interface RetryRegenSectionParams {
  regen_id: UUID;
  section_ref: string;
}

export interface Provider {
  name: string;
  handles: string[];
  avg_time_per_page: number;
  configured: boolean;
  healthy: boolean;
  message: string | null;
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${API_BASE}${url}`, {
    ...init,
    headers: {
      ...(init?.headers ?? {}),
      ...(init?.body && !(init.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
    },
  });
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new Error(`${r.status} ${r.statusText}${text ? ` — ${text}` : ""}`);
  }
  if (r.status === 204) return undefined as T;
  return r.json() as Promise<T>;
}

export const api = {
  listBooks: () => req<Book[]>("/api/books"),
  getBook: (id: UUID) => req<Book>(`/api/books/${id}`),
  uploadBook: (file: File, title?: string) => {
    const fd = new FormData();
    fd.append("file", file);
    if (title) fd.append("title", title);
    return req<{ book_id: UUID; status: string }>("/api/books", {
      method: "POST",
      body: fd,
    });
  },
  deleteBook: (id: UUID) => req<void>(`/api/books/${id}`, { method: "DELETE" }),
  analyse: (id: UUID) =>
    req<{ book_id: UUID; job_id: UUID; status: string }>(`/api/books/${id}/analyse`, {
      method: "POST",
    }),
  patchSchema: (id: UUID, schema: BookSchema) =>
    req<Book>(`/api/books/${id}/schema`, {
      method: "PATCH",
      body: JSON.stringify(schema),
    }),
  approve: (id: UUID) =>
    req<{ book_id: UUID; job_id: UUID; status: string }>(`/api/books/${id}/approve`, {
      method: "POST",
    }),
  listSections: (bookId: UUID) => req<Section[]>(`/api/books/${bookId}/sections`),
  getSection: (id: UUID) => req<Section>(`/api/sections/${id}`),
  reExtractSection: (id: UUID) =>
    req<{ book_id: UUID; job_id: UUID; status: string }>(
      `/api/sections/${id}/re-extract`,
      { method: "POST" },
    ),
  reExtractBook: (bookId: UUID) =>
    req<{ book_id: UUID; job_id: UUID; status: string }>(
      `/api/books/${bookId}/re-extract`,
      { method: "POST" },
    ),
  regenerate: (bookId: UUID, params: RegenParams, sectionIds?: string[] | null) =>
    req<{ book_id: UUID; job_id: UUID; regen_id: UUID; status: string }>(
      `/api/books/${bookId}/regenerate`,
      {
        method: "POST",
        body: JSON.stringify(
          sectionIds && sectionIds.length > 0 ? { ...params, section_ids: sectionIds } : params,
        ),
      },
    ),
  listRegenerations: (bookId: UUID) => req<Regeneration[]>(`/api/books/${bookId}/regenerations`),
  getRegeneration: (id: UUID) => req<Regeneration>(`/api/regenerations/${id}`),
  rerunSection: (regenId: UUID, sectionId: string, customInstructions: string) =>
    req<{ section_id: string; blocks: Block[] }>(
      `/api/regenerations/${regenId}/sections/${sectionId}/rerun`,
      { method: "POST", body: JSON.stringify({ custom_instructions: customInstructions }) },
    ),
  saveRegeneration: (regenId: UUID, confirmedSectionIds: string[]) =>
    req<{ saved: boolean; sections_saved: number }>(
      `/api/regenerations/${regenId}/save`,
      { method: "POST", body: JSON.stringify({ confirmed_section_ids: confirmedSectionIds }) },
    ),
  exportMarkdown: (bookId: UUID, regenId?: UUID | null) => {
    const a = document.createElement("a");
    const qs = regenId ? `?regen_id=${regenId}` : "";
    a.href = `${API_BASE}/api/books/${bookId}/export/markdown${qs}`;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  },
  exportJson: (bookId: UUID, regenId?: UUID | null) => {
    const a = document.createElement("a");
    const qs = regenId ? `?regen_id=${regenId}` : "";
    a.href = `${API_BASE}/api/books/${bookId}/export/json${qs}`;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  },
  exportDocx: (bookId: UUID, regenId?: UUID | null) => {
    const a = document.createElement("a");
    const qs = regenId ? `?regen_id=${regenId}` : "";
    a.href = `${API_BASE}/api/books/${bookId}/export/docx${qs}`;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  },
  getJob: (id: UUID) => req<Job>(`/api/jobs/${id}`),
  getQuestionStructure: (bookId: UUID) =>
    req<QuestionStructureResponse>(`/api/books/${bookId}/question-structure`),
  createQuestionBank: (bookId: UUID) =>
    req<{ bank_id: UUID; job_id: UUID; status: string }>(
      `/api/books/${bookId}/question-banks`,
      { method: "POST" },
    ),
  listQuestionBanks: (bookId: UUID) =>
    req<QuestionBank[]>(`/api/books/${bookId}/question-banks`),
  getQuestionBank: (bankId: UUID) =>
    req<QuestionBank>(`/api/question-banks/${bankId}`),
  deleteQuestionBank: (bankId: UUID) =>
    req<void>(`/api/question-banks/${bankId}`, { method: "DELETE" }),
  retrySection: (bankId: UUID, sectionRef: string) =>
    req<{ bank_id: UUID; section_ref: string; job_id: UUID; status: string }>(
      `/api/question-banks/${bankId}/sections/${encodeURIComponent(sectionRef)}/retry`,
      { method: "POST" },
    ),
  reExtractBlock: (bankId: UUID, blockIdx: number) =>
    req<{ bank_id: UUID; block_idx: number; job_id: UUID; status: string }>(
      `/api/question-banks/${bankId}/blocks/${blockIdx}/re-extract`,
      { method: "POST" },
    ),
  listQuestions: (bankId: UUID) =>
    req<QuestionBankDetail>(`/api/question-banks/${bankId}/questions`),
  restoreRejected: (bankId: UUID, rejectedId: UUID) =>
    req<{ ok: boolean; question_id: UUID; rejected_id: UUID }>(
      `/api/question-banks/${bankId}/rejected/${rejectedId}/restore`,
      { method: "POST" },
    ),
  restoreAllRejected: (bankId: UUID) =>
    req<{ ok: boolean; restored: number; skipped: number }>(
      `/api/question-banks/${bankId}/rejected/restore-all`,
      { method: "POST" },
    ),
  discardRejected: (bankId: UUID, rejectedId: UUID) =>
    req<{ ok: boolean; rejected_id: UUID }>(
      `/api/question-banks/${bankId}/rejected/${rejectedId}/discard`,
      { method: "POST" },
    ),
  hideQuestion: (questionId: UUID) =>
    req<{ ok: boolean; question_id: UUID; is_hidden: boolean }>(
      `/api/question-banks/questions/${questionId}/hide`,
      { method: "PATCH" },
    ),
  unhideQuestion: (questionId: UUID) =>
    req<{ ok: boolean; question_id: UUID; is_hidden: boolean }>(
      `/api/question-banks/questions/${questionId}/unhide`,
      { method: "PATCH" },
    ),
  exportQuestionsJson: (bankId: UUID) => {
    const a = document.createElement("a");
    a.href = `${API_BASE}/api/question-banks/${bankId}/export/json`;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  },
  exportQuestionsMarkdown: (bankId: UUID) => {
    const a = document.createElement("a");
    a.href = `${API_BASE}/api/question-banks/${bankId}/export/markdown`;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  },
  exportQuestionsDocx: (bankId: UUID) => {
    const a = document.createElement("a");
    a.href = `${API_BASE}/api/question-banks/${bankId}/export/docx`;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  },
  startQuestionRegeneration: (bankId: UUID, params: RegenerateQuestionsParams) =>
    req<{ regen_id: UUID; job_id: UUID; status: string }>(
      `/api/question-banks/${bankId}/regenerate`,
      { method: "POST", body: JSON.stringify(params) },
    ),
  listQuestionRegenerations: (bookId: UUID) =>
    req<QuestionRegeneration[]>(`/api/books/${bookId}/question-regenerations`),
  getQuestionRegeneration: (regenId: UUID) =>
    req<QuestionRegeneration>(`/api/question-regenerations/${regenId}`),
  listRegenQuestions: (regenId: UUID) =>
    req<QuestionRegenQuestionsResponse>(`/api/question-regenerations/${regenId}/questions`),
  saveQuestionRegeneration: (regenId: UUID) =>
    req<QuestionRegeneration>(`/api/question-regenerations/${regenId}/save`, { method: "POST" }),
  deleteQuestionRegeneration: (regenId: UUID) =>
    req<void>(`/api/question-regenerations/${regenId}`, { method: "DELETE" }),
  bulkDeleteRegenQuestions: (regenId: UUID, questionIds: UUID[]) =>
    req<{ deleted: number }>(`/api/question-regenerations/${regenId}/questions`, {
      method: "DELETE",
      body: JSON.stringify({ question_ids: questionIds }),
    }),
  // R6 — section-level retry
  retryRegenSection: (regenId: UUID, sectionRef: string) =>
    req<{ regen_id: UUID; section_ref: string; job_id: UUID; status: string }>(
      `/api/question-regenerations/${regenId}/retry-section`,
      { method: "POST", body: JSON.stringify({ section_ref: sectionRef }) },
    ),
  // R10 — regen exports (overall, or per-section via section_ref query param)
  exportRegenJson: (regenId: UUID, sectionRef?: string) => {
    const qs = sectionRef ? `?section_ref=${encodeURIComponent(sectionRef)}` : "";
    const a = document.createElement("a");
    a.href = `${API_BASE}/api/question-regenerations/${regenId}/export/json${qs}`;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  },
  exportRegenMarkdown: (regenId: UUID, sectionRef?: string) => {
    const qs = sectionRef ? `?section_ref=${encodeURIComponent(sectionRef)}` : "";
    const a = document.createElement("a");
    a.href = `${API_BASE}/api/question-regenerations/${regenId}/export/markdown${qs}`;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  },
  exportRegenDocx: (regenId: UUID, sectionRef?: string) => {
    const qs = sectionRef ? `?section_ref=${encodeURIComponent(sectionRef)}` : "";
    const a = document.createElement("a");
    a.href = `${API_BASE}/api/question-regenerations/${regenId}/export/docx${qs}`;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  },
  listProviders: () => req<Provider[]>("/api/providers"),
  getProviderKeyStatus: (name: string) =>
    req<{ provider: string; configured: boolean }>(`/api/providers/${name}/keys`),
  saveProviderKeys: (name: string, keys: Record<string, unknown>) =>
    req<{ saved: boolean; valid: boolean; provider: string }>(
      `/api/providers/${name}/keys`,
      { method: "POST", body: JSON.stringify(keys) },
    ),

  // ===========================================================
  // Figures pipeline v2 (NEW — additive)
  // ===========================================================
  extractFiguresV2: (bookId: UUID) =>
    req<FigureExtractStartResponse>(
      `/api/books/${bookId}/extract-figures-v2`,
      { method: "POST" },
    ),
  listFigures: (bookId: UUID) =>
    req<FigureListResponse>(`/api/books/${bookId}/figures`),
  getFigure: (figureId: UUID) =>
    req<FigureDetail>(`/api/figures/${figureId}`),
  // Image URLs — used directly in <img src> (no JSON parse needed)
  figureImageUrl: (figureId: UUID, variant: FigureVariant = "auto") =>
    `${API_BASE}/api/figures/${figureId}/image?variant=${variant}`,
  regenerateFiguresSection: (
    bookId: UUID,
    sectionRef: string,
    params: FigureRegenParams,
  ) =>
    req<FigureRegenStartResponse>(
      `/api/books/${bookId}/sections/${encodeURIComponent(sectionRef)}/regenerate-figures`,
      { method: "POST", body: JSON.stringify(params) },
    ),
  discardFigureRegen: (figureId: UUID) =>
    req<{ figure_id: UUID; status: string }>(
      `/api/figures/${figureId}/discard-regen`,
      { method: "POST" },
    ),
  approveSectionFigures: (bookId: UUID, sectionRef: string) =>
    req<{ section_ref: string; approved: number; skipped_without_regen: number; approved_at: string }>(
      `/api/books/${bookId}/sections/${encodeURIComponent(sectionRef)}/figures/approve`,
      { method: "POST" },
    ),
  unapproveSectionFigures: (bookId: UUID, sectionRef: string) =>
    req<{ section_ref: string; unapproved: number }>(
      `/api/books/${bookId}/sections/${encodeURIComponent(sectionRef)}/figures/unapprove`,
      { method: "POST" },
    ),
  approveOneFigure: (figureId: UUID) =>
    req<{ figure_id: UUID; approved_at: string }>(
      `/api/figures/${figureId}/approve`,
      { method: "POST" },
    ),
  unapproveOneFigure: (figureId: UUID) =>
    req<{ figure_id: UUID; status: string }>(
      `/api/figures/${figureId}/unapprove`,
      { method: "POST" },
    ),
  // Phase 1: per-placement hide / unhide. Suppresses an inline or
  // appended figure at THIS spot without deleting the underlying image.
  hideFigureReference: (refId: UUID) =>
    req<{ ref_id: UUID; is_hidden: boolean }>(
      `/api/books/figure-references/${refId}/hide`,
      { method: "POST" },
    ),
  unhideFigureReference: (refId: UUID) =>
    req<{ ref_id: UUID; is_hidden: boolean }>(
      `/api/books/figure-references/${refId}/unhide`,
      { method: "POST" },
    ),
  deleteFigureReference: (refId: UUID) =>
    req<{ ref_id: UUID; deleted: boolean }>(
      `/api/books/figure-references/${refId}`,
      { method: "DELETE" },
    ),
  reembedFigures: (bookId: UUID) =>
    req<{ book_id: UUID; counters: Record<string, number> }>(
      `/api/books/${bookId}/reembed-figures`,
      { method: "POST" },
    ),
  getFinalDraft: (bookId: UUID, preferRegen: boolean = true) =>
    req<FinalDraft>(
      `/api/books/${bookId}/final-draft?prefer_regen=${preferRegen}`,
    ),
  reseedFinalDraft: (bookId: UUID, preferRegen: boolean = true) =>
    req<FinalDraft>(
      `/api/books/${bookId}/final-draft/reseed?prefer_regen=${preferRegen}`,
      { method: "POST" },
    ),
  patchFinalDraft: (bookId: UUID, operations: FinalDraftOperation[]) =>
    req<FinalDraft>(`/api/books/${bookId}/final-draft`, {
      method: "PATCH",
      body: JSON.stringify({ operations }),
    }),
  deleteFinalDraft: (bookId: UUID) =>
    req<{ deleted: boolean }>(`/api/books/${bookId}/final-draft`, {
      method: "DELETE",
    }),
  finalDraftExportUrl: (
    bookId: UUID,
    fmt: "json" | "markdown" | "docx",
  ) => `${API_BASE}/api/books/${bookId}/final-draft/export/${fmt}`,

  getFinalMerge: (bookId: UUID, preferRegen: boolean = true) =>
    req<FinalMergeDoc>(
      `/api/books/${bookId}/final-merge?prefer_regen=${preferRegen}`,
    ),
  finalMergeExportUrl: (
    bookId: UUID,
    fmt: "json" | "markdown" | "docx",
    preferRegen: boolean = true,
  ) =>
    `${API_BASE}/api/books/${bookId}/final-merge/export/${fmt}?prefer_regen=${preferRegen}`,
  listUnattachedFigures: (bookId: UUID) =>
    req<{ book_id: UUID; figures: UnattachedFigure[] }>(
      `/api/books/${bookId}/unattached-figures`,
    ),
  listFigureReferences: (
    bookId: UUID,
    opts?: { sectionRef?: string; context?: "theory" | "question" },
  ) => {
    const qs = new URLSearchParams();
    if (opts?.sectionRef) qs.set("section_ref", opts.sectionRef);
    if (opts?.context) qs.set("context", opts.context);
    const tail = qs.toString() ? `?${qs.toString()}` : "";
    return req<FigureReferencesResponse>(
      `/api/books/${bookId}/figure-references${tail}`,
    );
  },
  listFigureRegenerations: (
    bookId: UUID,
    opts?: { sectionRef?: string },
  ) => {
    const qs = new URLSearchParams();
    if (opts?.sectionRef) qs.set("section_ref", opts.sectionRef);
    const tail = qs.toString() ? `?${qs.toString()}` : "";
    return req<FigureRegenerationsResponse>(
      `/api/books/${bookId}/figure-regenerations${tail}`,
    );
  },
};

// ============================================================
// Figures pipeline v2 — types (NEW — additive)
// ============================================================

export type FigureVariant = "original" | "regenerated" | "auto";

export interface FigureReference {
  id: UUID;
  figure_id: UUID;
  section_ref: string;
  context: "theory" | "question";
  question_id: UUID | null;
  placeholder_text: string | null;
  link_method: string;
}

export interface Figure {
  id: UUID;
  book_id: UUID;
  section_id: string;
  figure_id_text: string | null;
  figure_number: string | null;
  normalized_label: string | null;
  caption: string | null;
  description: string | null;
  page_number: number | null;
  bounding_box: number[] | null;
  semantic_type: string;
  tags: string[];
  status: string;
  regen_status: "none" | "extracting" | "ready" | "failed";
  regen_version: number;
  has_original: boolean;
  has_regen: boolean;
  regen_meta: Record<string, unknown> | null;
  context_hint: string | null;
  // 0016 — approval workflow
  approved_at: string | null;
  is_approved: boolean;
  references?: FigureReference[] | null;
  created_at: string | null;
}

export interface FigureDetail extends Figure {
  references: FigureReference[];
}

export interface FigureSectionGroup {
  section_ref: string;
  figures: Figure[];
  contexts: ("theory" | "question")[];
  n_theory: number;
  n_question: number;
  // 0016 — regen + approval counts for sidebar badges
  n_regen: number;
  n_approved: number;
}

export interface FigureListResponse {
  book_id: UUID;
  sections: FigureSectionGroup[];
  total_figures: number;
}

export interface FigureReferencesResponse {
  book_id: UUID;
  section_ref: string | null;
  context: "theory" | "question" | null;
  references: FigureReference[];
}

export interface FigureExtractStartResponse {
  book_id: UUID;
  job_id: UUID;
  status: string;
}

export interface FigureRegenParams {
  style?: "enhanced" | "original";
  custom_instructions?: string | null;
  watermark_clean?: boolean;
  overlay?: boolean;
  image_model?: string | null;
  ocr_model?: string | null;
}

export interface FigureRegenStartResponse {
  book_id: UUID;
  section_ref: string;
  job_id: UUID;
  status: string;
  params: FigureRegenParams;
}

export interface FigureRegenerationRow {
  id: UUID;
  book_id: UUID;
  figure_id: UUID;
  section_id: string;
  image_url: string | null;
  style_params: Record<string, unknown> | null;
  model_used: string | null;
  status: string;
  created_at: string | null;
}

export interface FigureRegenerationRun {
  section_id: string;
  started_at: string | null;
  total: number;
  succeeded: number;
  failed: number;
  model_used: string | null;
  style_params: Record<string, unknown> | null;
  rows: FigureRegenerationRow[];
}

export interface FigureRegenerationsResponse {
  book_id: UUID;
  section_ref: string | null;
  total_attempts: number;
  runs: FigureRegenerationRun[];
}
