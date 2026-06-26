// Real-backend books client + adapter to the V-Studio Book shape.
//
// The CMDS backend returns books with a rich nested schema; V-Studio's
// cards want a flatter shape with derived counts + a cover color. The
// adapter computes those once at fetch time so the page code stays clean.

import { useQuery } from '@tanstack/react-query';

import { API_BASE, req, ApiError } from './client';
import { COVER_GRADIENTS, type Book, type BookStatus } from '../mocks/books';

// ---------- Upload (multipart) ----------

export type CreateBookResult = {
  book_id: string;
  job_id?: string | null;
  status: string;
};

/**
 * Upload a chapter file. Uses raw fetch (not the JSON helper) because the
 * backend's `POST /api/books` endpoint expects multipart/form-data with the
 * file part — we cannot send JSON.
 */
export async function uploadChapter(params: {
  file: File;
  folderId: string;
  title: string;
  subject?: string;
  isMultiColumn?: boolean;
}): Promise<CreateBookResult> {
  const fd = new FormData();
  fd.append('file', params.file, params.file.name);
  fd.append('folder_id', params.folderId);
  fd.append('title', params.title);
  if (params.subject) fd.append('subject', params.subject);
  if (params.isMultiColumn) fd.append('is_multi_column', 'true');

  const res = await fetch(`${API_BASE}/api/books`, {
    method: 'POST',
    credentials: 'include',
    body: fd,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new ApiError(res.status, text);
  }
  return (await res.json()) as CreateBookResult;
}

// ---------- Raw backend shape (subset we need) ----------

export type BackendBook = {
  id: string;
  title: string;
  subject: string | null;
  folder_id: string | null;
  status: string;
  created_at: string;
  updated_at: string;
  schema_?: BackendSchema | null;
  analyser?: BackendAnalyser | null;
};

type BackendSchema = {
  sections?: BackendSchemaSection[];
} | null;

type BackendSchemaSection = {
  id?: string;
  title?: string;
  type: 'chapter' | 'section' | 'subsection' | 'excluded';
  expected_question_count?: number;
  subsections?: BackendSchemaSection[];
};

type BackendAnalyser = {
  estimated_pages?: number;
} | null;

// ---------- Status mapping ----------

const PROCESSING_STATUSES = new Set([
  'analysing',
  'extracting',
  're_extracting',
  'regenerating',
]);
const READY_STATUSES = new Set(['extracted', 'approved', 'done', 'ready']);

function mapStatus(raw: string): BookStatus {
  if (PROCESSING_STATUSES.has(raw)) return 'processing';
  if (READY_STATUSES.has(raw)) return 'done';
  return 'queued';
}

// ---------- Cover gradient picker ----------

const COVER_KEYS = Object.keys(COVER_GRADIENTS) as Array<keyof typeof COVER_GRADIENTS>;

/** Deterministic pick: same subject → same cover, so the library stays stable. */
function pickCover(seed: string): keyof typeof COVER_GRADIENTS {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) {
    hash = (hash * 31 + seed.charCodeAt(i)) | 0;
  }
  return COVER_KEYS[Math.abs(hash) % COVER_KEYS.length];
}

// ---------- Schema → counts ----------

function flatten(sections: BackendSchemaSection[] | undefined): BackendSchemaSection[] {
  if (!sections) return [];
  const out: BackendSchemaSection[] = [];
  const stack = [...sections];
  while (stack.length) {
    const s = stack.shift()!;
    out.push(s);
    if (s.subsections?.length) stack.push(...s.subsections);
  }
  return out;
}

function countChapters(schema: BackendSchema): number {
  return flatten(schema?.sections).filter((s) => s.type === 'chapter').length;
}

function countQuestions(schema: BackendSchema): number {
  return flatten(schema?.sections).reduce(
    (sum, s) => sum + (s.expected_question_count ?? 0),
    0
  );
}

// ---------- Date humanizer ----------

function humanize(iso: string): string {
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return iso;
  const diff = Date.now() - then;
  const m = Math.floor(diff / 60_000);
  const h = Math.floor(diff / 3_600_000);
  const d = Math.floor(diff / 86_400_000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m} min ago`;
  if (h < 24) return `${h} hour${h === 1 ? '' : 's'} ago`;
  if (d < 7) return `${d} day${d === 1 ? '' : 's'} ago`;
  return new Date(iso).toLocaleDateString();
}

// ---------- Adapter ----------

export function adaptBook(b: BackendBook): Book {
  const subject = b.subject ?? 'General';
  const schema = b.schema_ ?? null;
  return {
    id: b.id,
    title: b.title,
    subject,
    grade: '—',
    template: 'CBSE',
    chapters: countChapters(schema),
    questions: countQuestions(schema),
    figures: 0, // figure count comes from a separate endpoint; surface later
    status: mapStatus(b.status),
    progress: mapStatus(b.status) === 'done' ? 100 : 0,
    updated: humanize(b.updated_at || b.created_at),
    cover: pickCover(subject),
    pages: b.analyser?.estimated_pages ?? 0,
    folder_id: b.folder_id,
  };
}

// ---------- Hook ----------

type State =
  | { kind: 'loading' }
  | { kind: 'ready'; books: Book[] }
  | { kind: 'error'; error: string };

export function useBooks() {
  const q = useQuery({
    queryKey: ['books'],
    queryFn: async () => {
      const raw = await req<BackendBook[]>('/api/books');
      return raw.map(adaptBook);
    },
    // Continuous reconciliation of the library with the server: new books
    // (including ones uploaded by OTHER users) and status changes appear
    // without a manual refresh. The /api/books list is cheap; 5s is a good
    // balance for a multi-team service.
    refetchInterval: 5000,
    refetchOnWindowFocus: true,
  });

  const state: State = q.isPending
    ? { kind: 'loading' }
    : q.error
      ? { kind: 'error', error: explainError(q.error) }
      : { kind: 'ready', books: q.data! };

  return { ...state, refetch: () => q.refetch() };
}

// ---------- Single-book detail ----------

export type BackendChapter = {
  id: string;
  n: number;
  title: string;
  sections: number;
  questions: number;
  figures: number;
  status: 'done' | 'processing' | 'queued';
  progress?: number;
};

export type BookDetail = {
  book: Book;
  chapters: BackendChapter[];
  /** Raw schema so callers can drill deeper if needed. */
  raw: BackendBook;
};

type BookState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: BookDetail }
  | { kind: 'error'; error: string };

export function useBook(id: string | undefined) {
  const q = useQuery({
    queryKey: ['book', id],
    queryFn: async () => {
      const raw = await req<BackendBook>(`/api/books/${id}`);
      const book = adaptBook(raw);
      const chapters = extractChapters(raw, book.status);
      return { book, chapters, raw } as BookDetail;
    },
    enabled: Boolean(id),
    // Live status: poll fast while the book is in-flight (schema/theory/
    // questions/figures running) so the extract progress screen updates on
    // its own. Stop once the book reaches a terminal state (ready/failed)
    // to avoid pointless polling. Reconciliation, not one-shot.
    refetchInterval: (query) => {
      const raw = query.state.data?.raw?.status;
      if (!raw) return 2500; // still loading → keep polling
      const terminal = READY_STATUSES.has(raw) || raw === 'failed';
      return terminal ? false : 2500;
    },
    refetchOnWindowFocus: true,
  });

  const state: BookState = !id
    ? { kind: 'error', error: 'No book id in URL' }
    : q.isPending
      ? { kind: 'loading' }
      : q.error
        ? { kind: 'error', error: explainError(q.error) }
        : { kind: 'ready', data: q.data! };

  return { ...state, refetch: () => q.refetch() };
}

function extractChapters(raw: BackendBook, bookStatus: BookStatus): BackendChapter[] {
  const top = (raw.schema_?.sections ?? []).filter((s) => s.type === 'chapter');
  // Default per-chapter status mirrors the book until we wire the
  // per-section endpoint in a later phase.
  const chapterStatus: BackendChapter['status'] = bookStatus;
  return top.map((c, i) => {
    const subs = flatten(c.subsections);
    const questions =
      (c.expected_question_count ?? 0) +
      subs.reduce((s, x) => s + (x.expected_question_count ?? 0), 0);
    return {
      id: c.id ?? `ch-${i + 1}`,
      n: i + 1,
      title: c.title ?? `Chapter ${i + 1}`,
      sections: subs.length,
      questions,
      figures: 0,
      status: chapterStatus,
    };
  });
}

function explainError(err: unknown): string {
  if (err instanceof ApiError) return `Backend ${err.status}: ${err.message}`;
  if (err instanceof Error) return err.message;
  return 'Unknown error';
}
