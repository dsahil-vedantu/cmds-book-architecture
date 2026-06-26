// Figures client — fetches all figures for a book grouped by section.

import { useCallback, useEffect, useState } from 'react';

import { API_BASE, ApiError, req } from './client';

export type Figure = {
  id: string;
  book_id: string;
  section_id: string | null;
  figure_id_text: string | null;
  figure_number: string | null;
  normalized_label: string | null;
  caption: string | null;
  description: string | null;
  page_number: number | null;
  bounding_box: number[] | null;
  semantic_type: string | null;
  status: string;
  regen_status: string;
  has_original: boolean;
  has_regen: boolean;
  context_hint: string | null;
  is_approved: boolean;
  // Regen bookkeeping. `engine` ("table_embed" | "vector" | "image") records
  // which engine produced the current regen variant; `source` is its provenance.
  regen_meta: {
    engine?: string;
    source?: string;
    graphics_embedded?: number;
    [k: string]: unknown;
  } | null;
};

export type SectionFigures = {
  section_ref: string;
  // Human-readable section title (from book schema). Falls back to null
  // when the section couldn't be matched (e.g. orphan figures).
  section_title?: string | null;
  figures: Figure[];
  n_theory: number;
  n_question: number;
};

export type BookFigures = {
  book_id: string;
  total_figures: number;
  sections: SectionFigures[];
};

// ─── HTTP ─────────────────────────────────────────────────────────
export const getBookFigures = (bookId: string) =>
  req<BookFigures>(`/api/books/${bookId}/figures`);

// Image bytes endpoint — returns the figure image bytes. Use in <img src>.
// Backend variant strings are "regenerated" | "original" | "auto"; we send
// the explicit one when caller asked for regen so we never depend on the
// auto-fallback. Omitting the param lets the backend choose (auto).
export const figureImageUrl = (figureId: string, regen = false) =>
  `${API_BASE}/api/figures/${figureId}/image${regen ? '?variant=regenerated' : ''}`;

// Explicit ORIGINAL-variant URL — never auto-upgraded to the regen variant.
// The compare modal's "Original" pane must use this: figureImageUrl(id, false)
// omits the variant param, which the backend resolves as `auto` and serves the
// regen bytes when an approved regen variant exists — making both panes show
// the regenerated image.
export const figureOriginalImageUrl = (figureId: string) =>
  `${API_BASE}/api/figures/${figureId}/image?variant=original`;

// Per-section figure regen — POSTs to backend with optional custom instructions.
// Backend dispatches an async worker. Caller should refetch figures after.
export const regenerateSectionFigures = (
  bookId: string,
  sectionRef: string,
  body: { style?: string; custom_instructions?: string | null } = {},
) =>
  req(
    `/api/books/${bookId}/sections/${encodeURIComponent(sectionRef)}/regenerate-figures`,
    { method: 'POST', body: JSON.stringify(body) },
  );

// Manual per-figure LaTeX/SVG diagram regen aligned to the regenerated theory.
// On-demand only (the figure is never auto-updated). On success the backend
// stores the rasterized PNG as the approved regen variant — refetch + cache-bust
// the image afterward to show it.
export const regenerateFigureDiagram = (
  figureId: string,
  customInstructions?: string | null,
  // Engine override. "auto" (default) routes by semantic_type, matching the
  // automatic pipeline; pass an explicit engine to force one.
  engine: 'auto' | 'vector' | 'table_embed' | 'image' = 'auto',
) =>
  req<{
    ok: boolean;
    fallback?: boolean;
    figure_id: string;
    engine?: string;
    subject?: string;
    description?: string;
    message?: string;
  }>(`/api/figures/${figureId}/regenerate-diagram`, {
    method: 'POST',
    body: JSON.stringify({
      custom_instructions: customInstructions ?? null,
      engine,
    }),
  });

// On-demand "Redraw cleanly" for ONE figure — the image-model raster redraw
// (same engine as the section batch), auto-approved so it shows immediately.
// Distinct from regenerateFigureDiagram (LaTeX/SVG vector). Refetch + cache-bust
// the image afterward.
export const redrawFigure = (
  figureId: string,
  opts: {
    style?: 'enhanced' | 'original';
    custom_instructions?: string | null;
    watermark_clean?: boolean;
    overlay?: boolean;
  } = {},
) =>
  req<{ ok: boolean; figure_id: string; style?: string }>(
    `/api/figures/${figureId}/redraw`,
    {
      method: 'POST',
      body: JSON.stringify({
        style: opts.style ?? 'enhanced',
        custom_instructions: opts.custom_instructions ?? null,
        watermark_clean: opts.watermark_clean ?? false,
        overlay: opts.overlay ?? false,
      }),
    },
  );

// Approve / unapprove a single figure's regen variant. Approving makes the
// REGENERATED image the one used in Preview/Composer/Export; unapproving falls
// back to the ORIGINAL. Reversible and non-destructive (the regen is kept).
export const approveFigure = (figureId: string) =>
  req<{ figure_id: string; approved_at: string }>(
    `/api/figures/${figureId}/approve`,
    { method: 'POST' },
  );

export const unapproveFigure = (figureId: string) =>
  req<{ figure_id: string }>(`/api/figures/${figureId}/unapprove`, {
    method: 'POST',
  });

// ─── Hook ─────────────────────────────────────────────────────────
type State =
  | { kind: 'loading' }
  | { kind: 'ready'; data: BookFigures }
  | { kind: 'error'; error: string };

export function useBookFigures(bookId: string | undefined) {
  const [state, setState] = useState<State>({ kind: 'loading' });

  const load = useCallback(async () => {
    if (!bookId) {
      setState({ kind: 'error', error: 'No book id' });
      return;
    }
    setState({ kind: 'loading' });
    try {
      const data = await getBookFigures(bookId);
      setState({ kind: 'ready', data });
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
