// Renders a section's extracted theory blocks.
//
// Block schema (from Section.blocks):
//   { t: 'p',   c: string }              → paragraph
//   { t: 'h3',  c: string }              → heading
//   { t: 'eq',  c: string }              → equation
//   { t: 'def', term: string, c: string} → definition
//   { t: 'kp',  c: string }              → key point
//   { t: 'fig', c: string, label?: str } → figure caption / reference
//   ... other types passed through as raw JSON for now

import React from 'react';
import type { Section } from '../../api/sections';
import type { Figure } from '../../api/figures';
import { figureImageUrl } from '../../api/figures';
import { API_BASE } from '../../api/client';
import { Icon } from '../Icon';
import { MathMarkdown } from '../MathMarkdown';

type Block =
  | { t: 'p'; c: string }
  | { t: 'h3'; c: string }
  | { t: 'eq'; c: string }
  | { t: 'def'; term?: string; c: string }
  | { t: 'kp'; c: string }
  | { t: 'fig'; c?: string; label?: string }
  | { t: 'list'; items: string[]; ordered?: boolean }
  | { t: 'table'; headers?: string[]; rows?: string[][]; caption?: string }
  | { t: 'example_ref' | 'exercise_ref' | 'question_ref'; label?: string; ref?: string }
  | { t: 'example'; label?: string; prob?: string; sol?: string; eqs?: string[] }
  | { t: string; [k: string]: unknown };

const CHIP_TYPES = new Set([
  'example_ref',
  'exercise_ref',
  'question_ref',
]);

const STRUCTURAL_TYPES = new Set([
  // "Structural" blocks reset chip-suppression state — they signal
  // a return to genuine theory content after an example body.
  'h3',
  'eq',
  'def',
  'kp',
  'fig',
  'table',
  'example',
]);

/**
 * Theory-tab view cleanup for mixed (theory+questions) sections.
 *
 * The backend's theory worker extracts the FULL page range of a section
 * — including any worked-example problem statements + solutions that
 * are visually inline in the textbook. Those belong to the questions
 * pipeline (and appear in the Questions tab), so showing them inside
 * the Theory tab would duplicate the same content twice and make the
 * theory read like a question paper.
 *
 * The cleanup:
 *   1. Dedupe consecutive chips referencing the same example
 *      (the linker emits both example_ref + question_ref for one example).
 *   2. After a chip, suppress paragraph / list blocks (they're the
 *      worked-example body — "By what percent…" + "SOLUTION" + steps).
 *   3. Any structural block (h3 / eq / def / kp / fig / table / example)
 *      resets the suppression — that signals a return to theory content.
 *
 * Pure theory sections (no chips) flow through unchanged.
 */
function filterTheoryBlocks(blocks: Block[]): Block[] {
  const out: Block[] = [];
  let inExampleBody = false;
  let lastChipLabel: string | null = null;
  for (const b of blocks) {
    if (CHIP_TYPES.has(b.t)) {
      const label = (b as { label?: string }).label ?? null;
      // Dedupe — same chip reference emitted twice in a row.
      if (label && label === lastChipLabel) continue;
      lastChipLabel = label;
      inExampleBody = true;
      out.push(b);
      continue;
    }
    if (STRUCTURAL_TYPES.has(b.t)) {
      // Back to real theory — keep rendering.
      inExampleBody = false;
      lastChipLabel = null;
      out.push(b);
      continue;
    }
    // Paragraph / list — only keep when NOT inside an example body.
    if (!inExampleBody) out.push(b);
  }
  return out;
}

type TheoryViewProps = {
  section: Section | null;
  /** When provided, overrides section.blocks (e.g. regenerated blocks). */
  blocksOverride?: Block[] | null;
  /** Banner shown above the content (e.g. "Regenerated content" or compare). */
  banner?: { label: string; tone: 'regen' | 'original' } | null;
  /** All figures for the book — used to inline images on `fig` blocks. */
  figures?: Figure[];
  /** Hide the internal section header (id + title + status badge). Used by
   *  RegenReviewPage where the parent already renders a section header —
   *  avoids duplicate title/slug stacks. */
  hideHeader?: boolean;
  /** Hide the outer container padding (parent provides its own spacing). */
  flat?: boolean;
};

/** Normalize a figure label / number for matching against block labels.
 *
 * Backend stores labels in many shapes:
 *   - figure_number:    "Figure 8.10" / "Fig. 8.10"
 *   - normalized_label: "8.10"
 *   - block.label:      "Figure 8.10"   (clean)
 *   - block.c:          "Figure 8.10 Diagram showing X"   (full caption)
 *
 * For block.c we need to extract just the "Figure X.Y" prefix; otherwise the
 * normalized form picks up the whole caption text and never matches the
 * figure_number that the Figure row carries. Without this prefix-extract,
 * theory body figures all fall through to the "Figure not available inline"
 * branch even when the Figure row + image_bytes + figure_references all exist.
 */
/** Parse a LaTeX `\begin{tabular}...\end{tabular}` string into headers + rows.
 *
 * Backend Unit 2 emits tables as LaTeX (e.g. "\begin{tabular}{|l|l|}\hline
 * Element & Symbol \\ \hline Hydrogen & H \\ Oxygen & O \\ \hline
 * \end{tabular}"). The browser KaTeX renderer doesn't render `tabular` as a
 * real HTML table — it falls through as text. So we parse it ourselves and
 * emit a native <table>.
 *
 * Recognizes: `\\` row separators, `&` cell separators, `\hline` boundaries.
 * The first row (between the first two \hline markers) is treated as headers
 * when present; otherwise all rows are body.
 *
 * Returns null if the string doesn't look like a tabular block — caller falls
 * back to the legacy structured headers/rows path.
 */
function parseLatexTabular(s: string): { headers: string[]; rows: string[][] } | null {
  const trimmed = s.trim();
  if (!/\\begin\{tabular\}/.test(trimmed)) return null;
  const bodyMatch = trimmed.match(/\\begin\{tabular\}\{[^}]*\}([\s\S]*?)\\end\{tabular\}/);
  if (!bodyMatch) return null;
  const body = bodyMatch[1];
  // Split row-by-row on `\\` while preserving any inline LaTeX. Then drop
  // \hline tokens (they're separators, not content).
  const rawRows = body
    .split(/\\\\/)
    .map((r) => r.replace(/\\hline/g, '').trim())
    .filter((r) => r.length > 0);
  if (rawRows.length === 0) return null;
  const rows = rawRows.map((r) =>
    r.split('&').map((c) => c.replace(/\\textbackslash\{\}/g, '\\').trim())
  );
  // Heuristic: if the original body has \hline both before and after the
  // first row (i.e. "\hline X & Y \\ \hline"), treat the first row as headers.
  const firstHlineRe = /\\begin\{tabular\}\{[^}]*\}\s*\\hline/;
  const hasHeaderHline = firstHlineRe.test(trimmed);
  if (hasHeaderHline && rows.length > 1) {
    return { headers: rows[0], rows: rows.slice(1) };
  }
  return { headers: [], rows };
}

function normLabel(s: string | null | undefined): string {
  if (!s) return '';
  // Match "Figure X.Y", "Fig. X.Y", or "Fig X.Y" prefix and capture the
  // numeric part (allows trailing letters like "8.3a", "8.3b").
  const m = s.match(/^(?:figure|fig\.?)\s*([\d]+(?:\.[\d]+)*[a-z]?)/i);
  const head = m ? m[1] : s;
  return head
    .toLowerCase()
    .replace(/figure|fig\.?/g, '')
    .replace(/[\s.]/g, '')
    .trim();
}

export function TheoryView({
  section,
  blocksOverride,
  banner,
  figures = [],
  hideHeader = false,
  flat = false,
}: TheoryViewProps) {
  if (!section) {
    return (
      <div
        style={{
          flex: 1,
          padding: 48,
          color: 'var(--ink-500)',
          textAlign: 'center',
          fontSize: 14,
        }}
      >
        Pick a section from the left to see its extracted content.
      </div>
    );
  }

  // Render raw backend output — same blocks the existing frontend's
  // BlockRenderer sees.
  // If a regen variant is being viewed, blocksOverride supplies the
  // regenerated blocks instead of the original Section.blocks.
  const rawBlocks = (blocksOverride ?? (section.blocks ?? [])) as Block[];

  // Dedupe linker duplicates: when the example linker emits BOTH an
  // `example_ref` (from the theory OCR pass) AND a `question_ref` (from
  // the downstream linker that connects to the actual extracted question)
  // for the same label, we want to render only the `question_ref` — it's
  // the linked, clickable chip. Drop any `example_ref` whose label also
  // appears as a `question_ref` (or as a sibling `exercise_ref`).
  const linkedLabels = new Set<string>();
  for (const b of rawBlocks) {
    if (b.t === 'question_ref' || b.t === 'exercise_ref') {
      const label = (b as { label?: string }).label?.trim();
      if (label) linkedLabels.add(label);
    }
  }
  // Compute the set of RAW block indices to HIDE from rendering — WITHOUT
  // re-indexing the array. Embedded (unlabelled) figures are keyed by their
  // original `placement_block_idx`, so the render MUST look them up by the
  // ORIGINAL index. Filtering into a new array (and .slice(1)) silently
  // shifted every figure down by the number of removed blocks before it —
  // the root cause of unlabelled-figure misplacement. We render over
  // rawBlocks with the original index intact and just skip the dropped ones.
  const droppedIdx = new Set<number>();
  rawBlocks.forEach((b, i) => {
    if (b.t !== 'example_ref') return;
    const label = (b as { label?: string }).label?.trim();
    // Drop an example_ref whose label also appears as a linked
    // question_ref/exercise_ref (duplicate chip).
    if (label && linkedLabels.has(label)) droppedIdx.add(i);
  });

  // Dedupe duplicate heading: when the FIRST VISIBLE block is a heading whose
  // text matches the section title (case/punctuation-insensitive), hide it —
  // the section title is already shown above (SectionHeader / page header).
  const normHeading = (s: string) =>
    s
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, ' ')
      .trim();
  const firstVisibleIdx = rawBlocks.findIndex((_, i) => !droppedIdx.has(i));
  if (firstVisibleIdx >= 0) {
    const first = rawBlocks[firstVisibleIdx] as { t: string; c?: string };
    if (
      (first.t === 'heading' || first.t === 'h3' || first.t === 'h2') &&
      typeof first.c === 'string' &&
      section.title &&
      normHeading(first.c) === normHeading(section.title)
    ) {
      droppedIdx.add(firstVisibleIdx);
    }
  }
  const visibleBlockCount = rawBlocks.length - droppedIdx.size;

  // Build a {normalized label → Figure} map for inline image rendering.
  const figureByLabel = new Map<string, Figure>();
  for (const f of figures) {
    for (const key of [
      f.normalized_label,
      f.figure_number,
      f.figure_id_text,
    ]) {
      const k = normLabel(key);
      if (k) figureByLabel.set(k, f);
    }
  }
  // Also embedded_figures on the Section (set by figure_embedder, surfaced
  // by the canonical figure serializer). This is the SELF-SUFFICIENT path:
  // every section the API returns carries its own embedded_figures, so a
  // labelled `{t:'fig'}` block resolves to an image from the section ALONE —
  // no dependency on the optional `figures` prop. That guarantees figures
  // render automatically in EVERY caller (extract review, regen review,
  // future pages) without each one remembering to pass `figures`.
  //
  // Key on `label` (the field the serializer emits) FIRST, plus the legacy
  // figure-row field names so both shapes resolve. The stored object always
  // exposes `image_url`, which the fig-block renderer uses directly.
  for (const ef of section.embedded_figures ?? []) {
    const candidates = [
      (ef as unknown as { label?: string }).label,
      (ef as unknown as { normalized_label?: string }).normalized_label,
      (ef as unknown as { figure_number?: string }).figure_number,
      (ef as unknown as { figure_id_text?: string }).figure_id_text,
    ];
    for (const c of candidates) {
      const k = normLabel(c);
      if (k && !figureByLabel.has(k)) {
        figureByLabel.set(k, ef as unknown as Figure);
      }
    }
  }

  // Unit 10 completion: build a {block_idx → embedded figures} map so the
  // figure_embedder's placement decisions are surfaced in the UI for figures
  // that have no label number (and therefore can't be matched via figureByLabel).
  //
  // The embedder writes `placement_block_idx` on every FigureReference. The
  // sections API exposes those rows as `section.embedded_figures`. We render
  // each figure right AFTER its placement block, regardless of whether the
  // figure has a label.
  type EmbeddedFigure = {
    placement_block_idx?: number | null;
    // Sub-unit position INSIDE the block (char offset into the block's
    // "\n"-joined sub-units). Set by the embedder for figures that belong
    // to a specific list item, so they render between items, not after the
    // whole list. null → block-level (render after the block, as before).
    placement_char_offset?: number | null;
    image_url?: string;
    label?: string;
    figure_number?: string;
    caption?: string;
    // Gemini-extracted 2-3 sentence description. Rendered as placeholder
    // info for UNLABELLED figures (label + caption both empty). Ensures
    // every figure has SOMETHING readable describing it, even when the
    // source PDF didn't print a label or caption.
    description?: string;
    figure_id?: string;
    ref_id?: string;
  };
  const figuresByBlockIdx = new Map<number, EmbeddedFigure[]>();
  // Build a set of {t:'fig'} blocks already in the theory body, keyed by
  // normalized label, so we can suppress an embedded_figure that would
  // produce a visible duplicate card next to a theory `fig` block already
  // showing the same caption. Symptom observed today: section
  // "symmetry-point-symmetry" had a {t:'fig', label:'Figure 6.8'} block
  // AND embedded ref at the same block_idx → UI rendered the figure
  // twice (once via the fig block, once via the embedded card).
  // Reuses module-level normLabel() helper (line 168).
  const figBlockLabels = new Set<string>();
  for (const b of (section.blocks ?? []) as Array<{ t?: string; label?: string; c?: string }>) {
    if (b.t !== 'fig') continue;
    const lbl = normLabel(b.label) || normLabel(b.c);
    if (lbl) figBlockLabels.add(lbl);
  }
  // Figures the embedder could NOT anchor to a specific block
  // (placement_block_idx = null — "page_fallback": it knows the figure is
  // on the section's page but not which block). These have no inline
  // position, so they render in a trailing group at the end of the section
  // — nothing the embedder attached during extraction is ever dropped.
  const trailingFigs: EmbeddedFigure[] = [];
  for (const ef of (section.embedded_figures ?? []) as EmbeddedFigure[]) {
    const idx = ef.placement_block_idx;
    // Dedup: if this embedded figure's label matches a {t:'fig'} block
    // already in the theory body, suppress it — the fig block already
    // renders the same caption + image via the EmbeddedFigureRender path.
    const efLabel = normLabel(ef.label) || normLabel(ef.figure_number);
    if (efLabel && figBlockLabels.has(efLabel)) continue;
    if (typeof idx !== 'number') {
      // No block anchor → trailing.
      trailingFigs.push(ef);
      continue;
    }
    const list = figuresByBlockIdx.get(idx) ?? [];
    list.push(ef);
    figuresByBlockIdx.set(idx, list);
  }

  return (
    <div
      style={{
        flex: flat ? 'unset' : 1,
        overflowY: flat ? 'visible' : 'auto',
        padding: flat ? 0 : '28px 40px 56px',
        background: flat ? 'transparent' : 'var(--bg)',
      }}
    >
      <div style={{ maxWidth: flat ? 'unset' : 760, margin: '0 auto' }}>
        {banner && (
          <div
            style={{
              padding: '8px 12px',
              borderRadius: 8,
              marginBottom: 12,
              background:
                banner.tone === 'regen' ? 'var(--red-50)' : 'var(--bg-tint)',
              color:
                banner.tone === 'regen'
                  ? 'var(--red-700)'
                  : 'var(--ink-700)',
              fontSize: 11,
              fontWeight: 700,
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
            }}
          >
            {banner.label}
          </div>
        )}
        {!hideHeader && <SectionHeader section={section} />}

        {visibleBlockCount === 0 ? (
          <div
            style={{
              padding: '40px 24px',
              textAlign: 'center',
              color: 'var(--ink-500)',
              background: 'var(--surface)',
              border: '1px dashed var(--line)',
              borderRadius: 12,
              marginTop: 24,
            }}
          >
            No content extracted for this section.
            {section.status === 'failed' && (
              <div style={{ marginTop: 8, color: 'var(--red-700)', fontSize: 13 }}>
                Extraction failed after {section.attempts} attempts.
              </div>
            )}
          </div>
        ) : (
          <div style={{ marginTop: 20 }}>
            {rawBlocks.map((b, i) => {
              // Iterate over rawBlocks so the index `i` matches each figure's
              // placement_block_idx. Hidden blocks (dropped chips / duplicate
              // title heading) are skipped from display but their index slot
              // is preserved, so figures never shift.
              const figs = figuresByBlockIdx.get(i) ?? [];
              const isList = (b as { t?: string }).t === 'list';
              // Figures carrying a sub-unit offset belong INSIDE a list at a
              // specific item; render those interleaved. The rest render
              // after the block (unchanged behavior).
              const subFigs = isList
                ? figs.filter((f) => typeof f.placement_char_offset === 'number')
                : [];
              const afterFigs = subFigs.length
                ? figs.filter((f) => typeof f.placement_char_offset !== 'number')
                : figs;
              return (
                <React.Fragment key={i}>
                  {!droppedIdx.has(i) &&
                    (subFigs.length > 0 ? (
                      <ListWithInlineFigures
                        block={b as { items?: string[]; ordered?: boolean }}
                        figs={subFigs}
                      />
                    ) : (
                      <BlockRender block={b} figureByLabel={figureByLabel} />
                    ))}
                  {afterFigs.map((ef, j) => (
                    <EmbeddedFigureRender key={`embed-${i}-${j}`} ef={ef} />
                  ))}
                </React.Fragment>
              );
            })}
            {/* Trailing figures: embedder attached them to this section but
                couldn't anchor to a specific block (page_fallback). Render
                at section end so no extracted figure is dropped. Same
                EmbeddedFigureRender (image_url) → labelled or unlabelled both
                show. Identical in extract + regen (one component). */}
            {trailingFigs.map((ef, j) => (
              <EmbeddedFigureRender key={`embed-trail-${j}`} ef={ef} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function SectionHeader({ section }: { section: Section }) {
  return (
    <div style={{ marginBottom: 8 }}>
      <div
        style={{
          fontSize: 11,
          fontWeight: 700,
          letterSpacing: '0.12em',
          textTransform: 'uppercase',
          color: 'var(--ink-500)',
          fontFamily: 'var(--font-mono)',
        }}
      >
        {section.section_id}
      </div>
      <h1
        style={{
          fontSize: 24,
          fontWeight: 800,
          letterSpacing: '-0.02em',
          color: 'var(--ink-900)',
          margin: '6px 0 0',
          lineHeight: 1.2,
        }}
      >
        {section.title || section.section_id}
      </h1>
      <div
        style={{
          fontSize: 12,
          color: 'var(--ink-500)',
          marginTop: 6,
          display: 'inline-flex',
          alignItems: 'center',
          gap: 8,
        }}
      >
        {section.status === 'passed' && (
          <span className="badge ok">
            <span className="dot" /> Extracted
          </span>
        )}
        {section.status === 'failed' && (
          <span className="badge regen">
            <span className="dot" /> Failed
          </span>
        )}
        {section.status !== 'passed' && section.status !== 'failed' && (
          <span className="badge">{section.status}</span>
        )}
        <span>{(section.blocks?.length ?? 0)} blocks</span>
      </div>
    </div>
  );
}

function BlockRender({
  block,
  figureByLabel,
}: {
  block: Block;
  figureByLabel: Map<string, Figure>;
}) {
  const t = block.t;
  if (t === 'h3') {
    return (
      <h3
        style={{
          fontSize: 18,
          fontWeight: 700,
          color: 'var(--ink-900)',
          letterSpacing: '-0.01em',
          margin: '24px 0 10px',
        }}
      >
        <MathMarkdown inline>{(block as { c?: string }).c ?? ''}</MathMarkdown>
      </h3>
    );
  }
  if (t === 'p') {
    return (
      <div
        style={{
          fontSize: 15,
          lineHeight: 1.7,
          color: 'var(--ink-800)',
          margin: '0 0 14px',
        }}
      >
        <MathMarkdown>{(block as { c?: string }).c ?? ''}</MathMarkdown>
      </div>
    );
  }
  if (t === 'eq') {
    const c = (block as { c?: string }).c ?? '';
    // Pipe through MathMarkdown → KaTeX renders $$...$$ display math.
    // The backend wraps eq blocks in $$...$$ via Unit 4 latex_normalizer,
    // so this just renders. Fallback styling kept for the rare case of
    // raw text (no $ delimiters) — KaTeX simply prints as text.
    return (
      <div
        style={{
          padding: '12px 18px',
          background: 'var(--indigo-50)',
          border: '1px solid var(--indigo-100)',
          borderRadius: 10,
          fontSize: 14,
          color: 'var(--indigo-700)',
          margin: '10px 0 14px',
          overflowX: 'auto',
        }}
      >
        <MathMarkdown>{c}</MathMarkdown>
      </div>
    );
  }
  if (t === 'def') {
    const b = block as { term?: string; c?: string };
    return (
      <div
        style={{
          padding: '14px 18px',
          background: 'var(--surface)',
          border: '1px solid var(--line)',
          borderRadius: 10,
          margin: '10px 0 14px',
        }}
      >
        <div
          style={{
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
            color: 'var(--ink-500)',
            marginBottom: 4,
          }}
        >
          Definition
        </div>
        {b.term && (
          <div
            style={{
              fontSize: 15,
              fontWeight: 700,
              color: 'var(--ink-900)',
              marginBottom: 4,
            }}
          >
            <MathMarkdown inline>{b.term}</MathMarkdown>
          </div>
        )}
        <div style={{ fontSize: 14, lineHeight: 1.65, color: 'var(--ink-800)' }}>
          <MathMarkdown>{b.c ?? ''}</MathMarkdown>
        </div>
      </div>
    );
  }
  if (t === 'kp') {
    return (
      <div
        style={{
          padding: '12px 14px 12px 16px',
          background: 'var(--warning-bg)',
          borderLeft: '3px solid var(--warning)',
          borderRadius: 8,
          margin: '10px 0 14px',
          fontSize: 14,
          lineHeight: 1.65,
          color: 'var(--ink-800)',
        }}
      >
        <div
          style={{
            fontSize: 10.5,
            fontWeight: 700,
            letterSpacing: '0.1em',
            textTransform: 'uppercase',
            color: '#8A5300',
            marginBottom: 4,
          }}
        >
          Key Point
        </div>
        <MathMarkdown>{(block as { c?: string }).c ?? ''}</MathMarkdown>
      </div>
    );
  }
  if (t === 'fig') {
    const b = block as { c?: string; caption?: string; label?: string };
    // Look up the resolved figure (full Figure row OR embedded_figure dict).
    const key = normLabel(b.label) || normLabel(b.c);
    const fig = key
      ? (figureByLabel.get(key) as unknown as {
          id?: string;
          image_url?: string;
          has_original?: boolean;
          figure_number?: string;
          caption?: string;
        } | undefined)
      : undefined;
    // Resolve the image URL from EITHER shape, so a labelled fig block
    // renders its image whether the caller passed the `figures` prop
    // (full rows → figureImageUrl(id)) OR only the section's
    // embedded_figures (serializer dicts → image_url). This is what makes
    // figure rendering self-sufficient + automatic in every caller.
    const figSrc = fig
      ? (fig.image_url
          ? (fig.image_url.startsWith('http')
              ? fig.image_url
              : `${API_BASE}${fig.image_url}`)
          : fig.id
          ? figureImageUrl(fig.id)
          : null)
      : null;
    return (
      <figure
        style={{
          margin: '14px 0 18px',
          padding: 0,
          border: '1px solid var(--line)',
          borderRadius: 10,
          overflow: 'hidden',
          background: 'var(--surface)',
        }}
      >
        {figSrc ? (
          <div
            style={{
              background: 'var(--surface-2)',
              padding: 12,
              display: 'grid',
              placeItems: 'center',
              borderBottom: '1px solid var(--line)',
            }}
          >
            <img
              src={figSrc}
              alt={b.label ?? fig?.figure_number ?? b.c ?? 'Figure'}
              style={{
                maxWidth: '100%',
                maxHeight: 360,
                objectFit: 'contain',
                display: 'block',
              }}
            />
          </div>
        ) : (
          <div
            style={{
              padding: 14,
              background: 'var(--surface-2)',
              borderBottom: '1px dashed var(--line)',
              display: 'flex',
              gap: 12,
              alignItems: 'center',
              fontSize: 13,
              color: 'var(--ink-500)',
            }}
          >
            <Icon name="image" size={18} className="muted" />
            <span>Figure not available inline</span>
          </div>
        )}
        <figcaption style={{ padding: '10px 14px' }}>
          {/* Heading — labeled fig shows its real label ("Figure 6.5"),
              unlabeled fig falls back to generic "Figure" so the
              placeholder is always visually identifiable as a figure
              slot. Never blank, even for bare fig blocks without
              caption. */}
          <div
            style={{
              fontWeight: 700,
              color: 'var(--ink-900)',
              marginBottom: 4,
              fontSize: 13.5,
            }}
          >
            {b.label ?? fig?.figure_number ?? 'Figure'}
          </div>
          {/* Description: read from block's c, then block's caption,
              then the matched Figure row's caption. block_normalizer
              outputs `caption`, so unlabeled figs (caption-only blocks)
              were rendering empty before this fallback was added. */}
          {(b.c || b.caption || fig?.caption) && (
            <div style={{ fontSize: 12.5, color: 'var(--ink-700)', lineHeight: 1.5 }}>
              {b.c ?? b.caption ?? fig?.caption}
            </div>
          )}
        </figcaption>
      </figure>
    );
  }
  if (t === 'list') {
    const b = block as { items?: string[]; ordered?: boolean };
    const items = b.items ?? [];
    if (items.length === 0) return null;
    // Backend often gives items with baked-in "1. " / "(2) " prefixes —
    // strip them so the <ol> numbers don't double up.
    const strip = (s: string) =>
      s.replace(/^\s*(?:\(\s*\d+\s*\)|\d+[.)])\s+/, '').trim();
    const Tag = b.ordered === false ? 'ul' : 'ol';
    return (
      <Tag
        style={{
          margin: '4px 0 14px',
          paddingLeft: 24,
          fontSize: 15,
          lineHeight: 1.7,
          color: 'var(--ink-800)',
        }}
      >
        {items.map((it, i) => (
          <li key={i} style={{ marginBottom: 6 }}>
            <MathMarkdown inline>{strip(it)}</MathMarkdown>
          </li>
        ))}
      </Tag>
    );
  }
  if (t === 'table') {
    const b = block as {
      c?: string;            // Unit 2: LaTeX \begin{tabular}...\end{tabular}
      headers?: string[];    // legacy structured form
      rows?: string[][];     // legacy structured form
      caption?: string;
    };
    // LaTeX tabular path — backend now ships `c` field. Convert
    // \begin{tabular}{|l|l|}\hline H1 & H2 \\ ... \end{tabular} into an
    // HTML <table> we can style. KaTeX doesn't render tabular as a real
    // HTML table; parsing it ourselves gives us native <table> output.
    if (b.c && b.c.trim()) {
      const parsed = parseLatexTabular(b.c);
      if (parsed) {
        const { headers, rows } = parsed;
        return (
          <div style={{ margin: '10px 0 16px' }}>
            {b.caption && (
              <div
                style={{
                  fontSize: 12,
                  color: 'var(--ink-500)',
                  fontStyle: 'italic',
                  marginBottom: 6,
                }}
              >
                <MathMarkdown inline>{b.caption}</MathMarkdown>
              </div>
            )}
            <div
              style={{
                overflowX: 'auto',
                border: '1px solid var(--line)',
                borderRadius: 8,
              }}
            >
              <table
                style={{
                  width: '100%',
                  borderCollapse: 'collapse',
                  fontSize: 13,
                }}
              >
                {headers.length > 0 && (
                  <thead>
                    <tr>
                      {headers.map((h, i) => (
                        <th
                          key={i}
                          style={{
                            padding: '8px 12px',
                            background: 'var(--surface-2)',
                            textAlign: 'left',
                            fontWeight: 700,
                            color: 'var(--ink-900)',
                            borderBottom: '1px solid var(--line)',
                          }}
                        >
                          <MathMarkdown inline>{h}</MathMarkdown>
                        </th>
                      ))}
                    </tr>
                  </thead>
                )}
                <tbody>
                  {rows.map((row, ri) => (
                    <tr key={ri}>
                      {row.map((cell, ci) => (
                        <td
                          key={ci}
                          style={{
                            padding: '8px 12px',
                            borderTop: '1px solid var(--line-2)',
                            verticalAlign: 'top',
                            color: 'var(--ink-800)',
                          }}
                        >
                          <MathMarkdown inline>{cell}</MathMarkdown>
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      }
    }
    // Legacy structured form (existing prod data uses this)
    return (
      <div style={{ margin: '10px 0 16px' }}>
        {b.caption && (
          <div
            style={{
              fontSize: 12,
              color: 'var(--ink-500)',
              fontStyle: 'italic',
              marginBottom: 6,
            }}
          >
            {b.caption}
          </div>
        )}
        <div
          style={{
            overflowX: 'auto',
            border: '1px solid var(--line)',
            borderRadius: 8,
          }}
        >
          <table
            style={{
              width: '100%',
              borderCollapse: 'collapse',
              fontSize: 13,
            }}
          >
            {b.headers && b.headers.length > 0 && (
              <thead>
                <tr>
                  {b.headers.map((h, i) => (
                    <th
                      key={i}
                      style={{
                        padding: '8px 12px',
                        background: 'var(--surface-2)',
                        textAlign: 'left',
                        fontWeight: 700,
                        color: 'var(--ink-900)',
                        borderBottom: '1px solid var(--line)',
                      }}
                    >
                      <MathMarkdown inline>{h}</MathMarkdown>
                    </th>
                  ))}
                </tr>
              </thead>
            )}
            <tbody>
              {(b.rows ?? []).map((row, ri) => (
                <tr key={ri}>
                  {row.map((cell, ci) => (
                    <td
                      key={ci}
                      style={{
                        padding: '8px 12px',
                        borderTop: '1px solid var(--line-2)',
                        verticalAlign: 'top',
                        color: 'var(--ink-800)',
                      }}
                    >
                      <MathMarkdown inline>{cell}</MathMarkdown>
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }
  if (t === 'example_ref' || t === 'exercise_ref' || t === 'question_ref') {
    const b = block as { label?: string; ref?: string };
    return (
      <div
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 8,
          padding: '8px 12px',
          background: 'var(--indigo-50)',
          border: '1px solid var(--indigo-100)',
          borderRadius: 999,
          margin: '8px 8px 8px 0',
          fontSize: 12.5,
          color: 'var(--indigo-700)',
          fontWeight: 600,
        }}
      >
        <Icon name="layers" size={12} />
        <span>{b.label ?? b.ref ?? t.replace('_', ' ')}</span>
      </div>
    );
  }
  if (t === 'example') {
    const b = block as {
      label?: string;
      prob?: string;
      sol?: string;
      eqs?: string[];
    };
    return (
      <div
        style={{
          padding: '16px 18px',
          border: '1px solid var(--line)',
          borderLeft: '3px solid var(--indigo-700)',
          borderRadius: 10,
          background: 'var(--surface)',
          margin: '12px 0 16px',
        }}
      >
        <div
          style={{
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
            color: 'var(--indigo-700)',
            marginBottom: 6,
          }}
        >
          {b.label ?? 'Example'}
        </div>
        {b.prob && (
          <div
            style={{
              fontSize: 14,
              lineHeight: 1.65,
              color: 'var(--ink-900)',
              marginBottom: b.sol || (b.eqs?.length ?? 0) > 0 ? 12 : 0,
            }}
          >
            {b.prob}
          </div>
        )}
        {b.eqs?.map((eq, i) => (
          <div
            key={i}
            style={{
              padding: '8px 12px',
              background: 'var(--indigo-50)',
              borderRadius: 6,
              fontFamily: 'var(--font-mono)',
              fontSize: 13,
              color: 'var(--indigo-700)',
              margin: '6px 0',
            }}
          >
            {eq}
          </div>
        ))}
        {b.sol && (
          <div
            style={{
              fontSize: 13.5,
              lineHeight: 1.65,
              color: 'var(--ink-700)',
              marginTop: 8,
              paddingTop: 8,
              borderTop: '1px solid var(--line-2)',
            }}
          >
            <strong style={{ color: 'var(--ink-800)' }}>Solution: </strong>
            {b.sol}
          </div>
        )}
      </div>
    );
  }
  // Unknown — collapsible debug fallback (only seen if backend ships a
  // new block type we haven't added yet).
  return (
    <details
      style={{
        padding: '8px 12px',
        background: 'var(--bg-tint)',
        border: '1px dashed var(--line)',
        borderRadius: 8,
        margin: '8px 0',
        fontSize: 11,
        color: 'var(--ink-500)',
      }}
    >
      <summary style={{ cursor: 'pointer', userSelect: 'none' }}>
        Unknown block type: <code>{t}</code>
      </summary>
      <pre
        style={{
          marginTop: 8,
          fontFamily: 'var(--font-mono)',
          overflow: 'auto',
        }}
      >
        {JSON.stringify(block, null, 2)}
      </pre>
    </details>
  );
}


/** Render a figure attached at a specific block position by the figure
 * embedder. Used for figures without printed labels (anchor-only / position
 * fallback) that the label-keyed `figureByLabel` map cannot resolve.
 * The image_url comes from the API response (`/api/figures/<id>/image`).
 */
// Render a list block with figures placed INSIDE it, after the specific
// item each figure belongs to. The embedder records a char offset into the
// block's "\n"-joined items; we recompute the same item boundaries and slot
// each figure after the matching item. Falls back to nothing special when
// there are no sub-unit figures (the caller only uses this then).
function ListWithInlineFigures({
  block,
  figs,
}: {
  block: { items?: string[]; ordered?: boolean };
  figs: Array<{
    placement_char_offset?: number | null;
    image_url?: string;
    label?: string;
    figure_number?: string;
    caption?: string;
    description?: string;
    placement_kind?: string;
    figure_id?: string;
  }>;
}) {
  const items = block.items ?? [];
  if (items.length === 0) return null;
  const strip = (s: string) =>
    s.replace(/^\s*(?:\(\s*\d+\s*\)|\d+[.)])\s+/, '').trim();
  // boundary[k] = char length of items[0..k] joined by "\n" — identical to
  // the backend's offset math, so a figure's offset maps to the item it
  // should follow (smallest k with boundary[k] >= offset).
  const boundary: number[] = [];
  let acc = 0;
  items.forEach((it, k) => {
    acc += (k > 0 ? 1 : 0) + it.length;
    boundary.push(acc);
  });
  const figsByItem = new Map<number, typeof figs>();
  for (const ef of figs) {
    const off = ef.placement_char_offset ?? 0;
    let k = boundary.findIndex((bnd) => bnd >= off);
    if (k < 0) k = items.length - 1;
    const arr = figsByItem.get(k) ?? [];
    arr.push(ef);
    figsByItem.set(k, arr);
  }
  const Tag = block.ordered === false ? 'ul' : 'ol';
  return (
    <Tag
      style={{
        margin: '4px 0 14px',
        paddingLeft: 24,
        fontSize: 15,
        lineHeight: 1.7,
        color: 'var(--ink-800)',
      }}
    >
      {items.map((it, i) => (
        <li key={i} style={{ marginBottom: 6 }}>
          <MathMarkdown inline>{strip(it)}</MathMarkdown>
          {figsByItem.get(i)?.map((ef, j) => (
            <EmbeddedFigureRender key={`li-embed-${i}-${j}`} ef={ef} />
          ))}
        </li>
      ))}
    </Tag>
  );
}

function EmbeddedFigureRender({
  ef,
}: {
  ef: {
    image_url?: string;
    label?: string;
    figure_number?: string;
    caption?: string;
    // Gemini-extracted description — rendered as placeholder info for
    // UNLABELLED figures, as secondary italic text when caption exists.
    description?: string;
    placement_kind?: string;
    figure_id?: string;
  };
}) {
  const label = ef.figure_number || ef.label || '';
  // Normalize image URL the same way BlockRender does above — relative paths
  // like "/storage/figures/abc.png" must be prefixed with the BACKEND host,
  // otherwise the browser resolves them against the FRONTEND domain → 404 →
  // broken-img icon + "Figure not available inline" placeholder. This is the
  // "same book, some figs perfect, some broken" bug: labeled figs hit
  // BlockRender (which already prefixes API_BASE), label-less / placement-
  // only figs hit this renderer (which didn't), so they 404'd.
  const efSrc = ef.image_url
    ? (ef.image_url.startsWith('http') ? ef.image_url : `${API_BASE}${ef.image_url}`)
    : null;
  const hasImage = Boolean(efSrc);
  return (
    <figure
      style={{
        margin: '14px 0 18px',
        padding: 0,
        border: '1px solid var(--line)',
        borderRadius: 10,
        overflow: 'hidden',
        background: 'var(--surface)',
      }}
    >
      {hasImage ? (
        <div
          style={{
            background: 'var(--surface-2)',
            padding: 12,
            display: 'grid',
            placeItems: 'center',
            borderBottom: '1px solid var(--line)',
          }}
        >
          <img
            src={efSrc ?? undefined}
            alt={label || ef.caption || 'Figure'}
            style={{
              maxWidth: '100%',
              maxHeight: 360,
              objectFit: 'contain',
              display: 'block',
            }}
          />
        </div>
      ) : (
        <div
          style={{
            padding: 14,
            background: 'var(--surface-2)',
            borderBottom: '1px dashed var(--line)',
            display: 'flex',
            gap: 12,
            alignItems: 'center',
            fontSize: 13,
            color: 'var(--ink-500)',
          }}
        >
          <Icon name="image" size={18} className="muted" />
          <span>Figure not available inline</span>
        </div>
      )}
      {(label || ef.caption || ef.description) && (
        <figcaption style={{ padding: '10px 14px' }}>
          {/* Header: printed label like "Fig. 5.1" (labelled figs only).
              UNLABELLED figs have no label/caption — show a soft
              "Figure (unlabelled)" header so user knows what they're
              looking at, with the Gemini description below as the
              human-readable placeholder info. */}
          {label ? (
            <div
              style={{
                fontWeight: 700,
                color: 'var(--ink-900)',
                marginBottom: 4,
                fontSize: 13.5,
              }}
            >
              {label}
            </div>
          ) : (
            !ef.caption && ef.description && (
              <div
                style={{
                  fontWeight: 600,
                  color: 'var(--ink-500)',
                  marginBottom: 4,
                  fontSize: 11,
                  letterSpacing: '0.06em',
                  textTransform: 'uppercase',
                }}
              >
                Figure (unlabelled)
              </div>
            )
          )}
          {ef.caption && (
            <div style={{ fontSize: 12.5, color: 'var(--ink-700)', lineHeight: 1.5 }}>
              {ef.caption}
            </div>
          )}
          {/* Description: the Gemini-extracted 2-3 sentence summary. Always
              shown when present BUT styled as secondary info when caption
              already exists; styled as primary info text when there's no
              caption (the unlabelled placeholder case). */}
          {ef.description && (
            <div
              style={{
                fontSize: ef.caption ? 11.5 : 12.5,
                color: ef.caption ? 'var(--ink-500)' : 'var(--ink-700)',
                lineHeight: 1.5,
                marginTop: ef.caption ? 6 : 0,
                fontStyle: ef.caption ? 'italic' : 'normal',
              }}
            >
              {ef.description}
            </div>
          )}
        </figcaption>
      )}
    </figure>
  );
}
