// Fetch regenerated content for a book.
//
// Backend endpoints:
//   GET /api/books/:id/regenerations          → list (newest first)
//   GET /api/regenerations/:regen_id           → single regen with blocks_by_section

import { useCallback, useEffect, useState } from 'react';

import { ApiError, req } from './client';

export type Regeneration = {
  id: string;
  book_id: string;
  status: string;
  blocks_by_section: Record<string, Array<{ t: string; [k: string]: unknown }>>;
  qc_drift?: Record<string, unknown> | null;
  params?: Record<string, unknown> | null;
  created_at: string;
  finished_at?: string | null;
};

export const listRegenerations = (bookId: string) =>
  req<Regeneration[]>(`/api/books/${bookId}/regenerations`);

export const getRegeneration = (regenId: string) =>
  req<Regeneration>(`/api/regenerations/${regenId}`);

// ─── Hook ─────────────────────────────────────────────────────────
//
// Returns the LATEST regeneration for the book, or 'empty' when none.

type State =
  | { kind: 'loading' }
  | { kind: 'empty' }
  | { kind: 'ready'; latest: Regeneration }
  | { kind: 'error'; error: string };

export function useLatestRegeneration(bookId: string | undefined) {
  const [state, setState] = useState<State>({ kind: 'loading' });

  const load = useCallback(async () => {
    if (!bookId) {
      setState({ kind: 'error', error: 'No book id' });
      return;
    }
    setState({ kind: 'loading' });
    try {
      const list = await listRegenerations(bookId);
      if (list.length === 0) {
        setState({ kind: 'empty' });
        return;
      }
      // Backend orders newest-first.
      const latest = list[0];
      // Re-fetch the full row in case the list response was a slim shape.
      const full = await getRegeneration(latest.id);
      setState({ kind: 'ready', latest: full });
    } catch (e) {
      setState({
        kind: 'error',
        error:
          e instanceof ApiError
            ? `Backend ${e.status}: ${e.message}`
            : e instanceof Error
            ? e.message
            : 'Unknown error',
      });
    }
  }, [bookId]);

  useEffect(() => {
    void load();
  }, [load]);

  return { ...state, refetch: load };
}
