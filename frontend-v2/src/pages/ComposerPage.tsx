// Composer — full port of OLD prod FinalComposerPage logic, restyled
// for V-Studio. Uses the SAME backend endpoints and SAME data model so
// the entire merge flow is preserved exactly:
//   GET   /api/books/:id/final-draft?regen=true
//   PATCH /api/books/:id/final-draft        body: { ops: [...] }
//   POST  /api/books/:id/final-draft/reseed
//   GET   /api/books/:id/final-draft/export/docx
//
// Item types (5):
//   - section_heading : {title, level, section_id, regen}
//   - block           : {block: {t, c, ...}}   ← theory paragraph/heading/etc
//   - figure          : {figure: {label, caption, image_url, ...}}
//   - question        : {question: {raw_text, ...}}
//   - custom_text     : {content: string}      ← user-inserted free text
//
// Operations:
//   - reorder        {op, id, after_id: string | "start"}
//   - remove         {op, id}
//   - edit_item      {op, id, patch: {...}}
//   - insert_custom_text {op, after_id, content}
//
// v1: up/down reorder buttons (drag-drop deferred). Edit/remove/insert
// all wired. Preview reflects changes immediately (via final-merge endpoint).

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  DndContext,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core';
import {
  SortableContext,
  arrayMove,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';

import { API_BASE, ApiError, req } from '../api/client';
import { useBook } from '../api/books';
import { Icon } from '../components/Icon';
import { MathMarkdown } from '../components/MathMarkdown';
import { DiagramPreview } from '../components/DiagramPreview';
import { sortQuestionRuns } from '../lib/question-sort';
import type { RegeneratedDiagram } from '../api/questions';
import { stripFigPlaceholders } from '../lib/questionText';

type Block = { t: string; [k: string]: unknown };

type SectionHeadingItem = {
  id: string;
  type: 'section_heading';
  parent_section_id: string | null;
  section_id: string;
  title: string;
  level: number;
  regen: boolean;
};
type BlockItem = {
  id: string;
  type: 'block';
  parent_section_id: string | null;
  block: Block;
};
type FigureItem = {
  id: string;
  type: 'figure';
  parent_section_id: string | null;
  figure: {
    ref_id: string;
    figure_id: string;
    label: string;
    caption: string;
    variant: 'original' | 'regen';
    image_url: string;
  };
};
type QuestionItem = {
  id: string;
  type: 'question';
  parent_section_id: string | null;
  question: {
    id: string;
    raw_text?: string;
    solution_text?: string | null;
    [k: string]: unknown;
  };
};
type CustomTextItem = {
  id: string;
  type: 'custom_text';
  parent_section_id: string | null;
  content: string;
};
type FinalDraftItem =
  | SectionHeadingItem
  | BlockItem
  | FigureItem
  | QuestionItem
  | CustomTextItem;

type FinalDraftOperation =
  | { op: 'reorder'; id: string; after_id: string | 'start' }
  | { op: 'remove'; id: string }
  | { op: 'edit_item'; id: string; patch: Record<string, unknown> }
  | { op: 'insert_custom_text'; after_id: string | 'start'; content: string };

type FinalDraftResponse = {
  id: string;
  book_id: string;
  status: string;
  items: FinalDraftItem[];
  item_count: number;
  last_seeded_at: string | null;
  updated_at: string | null;
};

export default function ComposerPage() {
  const { bookId } = useParams<{ bookId: string }>();
  const navigate = useNavigate();
  const bookState = useBook(bookId);

  const [items, setItems] = useState<FinalDraftItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  // Sort questions into textbook-original numeric order ONLY on the FIRST
  // API load for this book. After that we trust whatever order the backend
  // returns (which reflects any manual drag-reorder the user did + saved).
  // Without this guard, every save+reload would wipe the user's manual order
  // back to numeric.
  const hasInitiallyLoaded = useRef(false);

  const load = useCallback(async () => {
    if (!bookId) return;
    setLoading(true);
    setError(null);
    try {
      const d = await req<FinalDraftResponse>(
        `/api/books/${bookId}/final-draft?regen=true`,
      );
      const fetched = d.items ?? [];
      if (!hasInitiallyLoaded.current) {
        hasInitiallyLoaded.current = true;
        setItems(sortQuestionRuns(fetched));
      } else {
        setItems(fetched);
      }
    } catch (e) {
      setError(
        e instanceof ApiError ? `Backend ${e.status}: ${e.message}` :
        e instanceof Error ? e.message : 'Load failed',
      );
    } finally {
      setLoading(false);
    }
  }, [bookId]);

  useEffect(() => { void load(); }, [load]);

  // Apply ops optimistically + PATCH backend.
  // IMPORTANT: backend body shape is {"operations": [...]} not {"ops": [...]}.
  // Matches OLD prod's api.patchFinalDraft() exactly.
  const applyOps = useCallback(
    async (ops: FinalDraftOperation[], nextItems: FinalDraftItem[]) => {
      if (!bookId || saving) return;
      setItems(nextItems);
      setSaving(true);
      setError(null);
      try {
        const updated = await req<FinalDraftResponse>(
          `/api/books/${bookId}/final-draft`,
          {
            method: 'PATCH',
            body: JSON.stringify({ operations: ops }),
          },
        );
        // Backend returns canonical state — use it (handles server-assigned ids)
        setItems(updated.items ?? []);
      } catch (e) {
        setError(
          e instanceof ApiError ? `Backend ${e.status}: ${e.message}` :
          e instanceof Error ? e.message : 'Edit failed',
        );
        // Rollback by reloading
        void load();
      } finally {
        setSaving(false);
      }
    },
    [bookId, saving, load],
  );

  const moveUp = useCallback((idx: number) => {
    if (idx === 0) return;
    const next = [...items];
    [next[idx - 1], next[idx]] = [next[idx], next[idx - 1]];
    const afterId = idx - 1 === 0 ? 'start' : (next[idx - 2]?.id ?? 'start');
    void applyOps([{ op: 'reorder', id: items[idx].id, after_id: afterId }], next);
  }, [items, applyOps]);

  const moveDown = useCallback((idx: number) => {
    if (idx === items.length - 1) return;
    const next = [...items];
    [next[idx], next[idx + 1]] = [next[idx + 1], next[idx]];
    // After move, our item is at idx+1; the item now at idx is its predecessor
    const afterId = next[idx]?.id ?? 'start';
    void applyOps([{ op: 'reorder', id: items[idx].id, after_id: afterId }], next);
  }, [items, applyOps]);

  // ── Drag/drop reorder (faithful port from OLD prod FinalComposerPage) ──
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
  );

  const onDragEnd = useCallback(
    (e: DragEndEvent) => {
      const { active, over } = e;
      if (!over || active.id === over.id) return;
      const oldIdx = items.findIndex((it) => it.id === active.id);
      const newIdx = items.findIndex((it) => it.id === over.id);
      if (oldIdx < 0 || newIdx < 0) return;
      const next = arrayMove(items, oldIdx, newIdx);
      const afterId =
        newIdx === 0
          ? ('start' as const)
          : (next[newIdx - 1].id as string);
      void applyOps(
        [{ op: 'reorder', id: String(active.id), after_id: afterId }],
        next,
      );
    },
    [items, applyOps],
  );

  const removeItem = useCallback((id: string) => {
    if (!window.confirm('Remove this item from the draft?')) return;
    const next = items.filter((it) => it.id !== id);
    void applyOps([{ op: 'remove', id }], next);
  }, [items, applyOps]);

  const editItemPatch = useCallback(
    (id: string, patch: Record<string, unknown>) => {
      const next = items.map((it) =>
        it.id === id ? ({ ...it, ...patch } as FinalDraftItem) : it,
      );
      void applyOps([{ op: 'edit_item', id, patch }], next);
    },
    [items, applyOps],
  );

  const insertCustomTextAfter = useCallback(
    (afterId: string | 'start', content = 'New custom paragraph') => {
      // Server will assign an id; optimistic add omitted to keep simple.
      void applyOps(
        [{ op: 'insert_custom_text', after_id: afterId, content }],
        items,
      );
    },
    [items, applyOps],
  );

  // Generic export — matches OLD prod's api.finalDraftExportUrl().
  // 3 formats supported by backend: json, markdown, docx.
  const exportAs = useCallback(
    (fmt: 'json' | 'markdown' | 'docx') => {
      if (!bookId) return;
      const a = document.createElement('a');
      a.href = `${API_BASE}/api/books/${bookId}/final-draft/export/${fmt}`;
      a.download = '';
      document.body.appendChild(a);
      a.click();
      a.remove();
    },
    [bookId],
  );

  // Re-seed: discard composer edits + rebuild from current final-merge.
  // Confirmation matches OLD prod wording.
  const reseed = useCallback(async () => {
    if (!bookId) return;
    if (!window.confirm('Re-seed the draft from the current Final Merge state? Your edits will be discarded.')) return;
    try {
      await req(`/api/books/${bookId}/final-draft/reseed?prefer_regen=true`, { method: 'POST' });
      await load();
    } catch (e) {
      setError(
        e instanceof ApiError ? `Backend ${e.status}: ${e.message}` :
        e instanceof Error ? e.message : 'Re-seed failed',
      );
    }
  }, [bookId, load]);

  // Merge regen — same backend call as Re-seed (prefer_regen=true rebuilds
  // the draft pulling in every saved regen at its exact schema position).
  // Different UX path: clearer confirmation about replacing originals.
  // Matches OLD prod's "🔀 Merge regen" button exactly.
  const mergeRegen = useCallback(async () => {
    if (!bookId) return;
    if (!window.confirm(
      'Merge regenerated content into the draft? Every section that has a saved theory/question regeneration will be pulled in at its exact schema position, replacing originals. Your manual edits to the draft will be lost.',
    )) return;
    try {
      await req(`/api/books/${bookId}/final-draft/reseed?prefer_regen=true`, { method: 'POST' });
      await load();
    } catch (e) {
      setError(
        e instanceof ApiError ? `Backend ${e.status}: ${e.message}` :
        e instanceof Error ? e.message : 'Merge failed',
      );
    }
  }, [bookId, load]);

  const title = useMemo(
    () => bookState.kind === 'ready' ? bookState.data.book.title : 'Loading…',
    [bookState],
  );

  return (
    <div
      className="fade-up"
      style={{
        flex: 1,
        minHeight: 0,
        display: 'flex',
        flexDirection: 'column',
        background: '#faf8f4',
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: '14px 28px',
          borderBottom: '1px solid var(--line)',
          background: 'var(--surface)',
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          flexShrink: 0,
        }}
      >
        <button
          className="btn btn-ghost btn-sm"
          onClick={() => navigate(`/books/${bookId}/regen-review`)}
          title="Back to Regen Review"
        >
          <Icon name="arrow-l" size={14} /> Back
        </button>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--indigo-700)', marginBottom: 2 }}>
            Composer · Final Draft
          </div>
          <h1 style={{ fontSize: 20, fontWeight: 800, color: 'var(--ink-900)', margin: 0 }}>
            {title}
          </h1>
        </div>
        {/* Full toolbar — faithfully matches OLD prod's 6 CTAs */}
        <button
          className="btn btn-ghost btn-sm"
          onClick={() => navigate(`/books/${bookId}/preview`)}
          title="Open Preview (clean read-only view of the current draft)"
        >
          <Icon name="eye" size={14} /> Preview
        </button>
        <button
          className="btn btn-primary btn-sm"
          onClick={() => void mergeRegen()}
          disabled={saving}
          title="Pull all saved regen content into the draft at each section's exact position (same place the original sits)."
        >
          <Icon name="regen" size={14} /> Merge regen
        </button>
        <button
          className="btn btn-ghost btn-sm"
          onClick={() => void reseed()}
          disabled={saving}
          title="Discard edits and rebuild from current Final Merge"
        >
          <Icon name="regen" size={14} /> Re-seed
        </button>
        <div style={{ display: 'flex', gap: 4 }}>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => exportAs('json')}
            title="Download draft as JSON"
            style={{ padding: '4px 10px' }}
          >
            <Icon name="download" size={12} /> .json
          </button>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => exportAs('markdown')}
            title="Download draft as Markdown"
            style={{ padding: '4px 10px' }}
          >
            <Icon name="md" size={12} /> .md
          </button>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => exportAs('docx')}
            title="Download draft as Word DOCX"
            style={{ padding: '4px 10px' }}
          >
            <Icon name="docx" size={12} /> .docx
          </button>
        </div>
      </div>

      {error && (
        <div style={{ padding: '8px 28px', background: 'var(--red-50)', borderBottom: '1px solid var(--red-100)', color: 'var(--red-700)', fontSize: 13 }}>
          {error}
        </div>
      )}

      <div style={{ flex: 1, overflowY: 'auto', padding: '24px 32px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 18 }}>
          <div style={{ fontSize: 12, color: 'var(--ink-500)' }}>
            {loading ? 'Loading items…' : `${items.length} items · seeded from regen`}
          </div>
          {!loading && items.length > 0 && (
            <button
              className="btn btn-ghost btn-xs"
              onClick={() => insertCustomTextAfter('start')}
            >
              + Insert at top
            </button>
          )}
        </div>

        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
          <SortableContext items={items.map((i) => i.id)} strategy={verticalListSortingStrategy}>
            {items.map((item, idx) => (
              <ComposerItem
                key={item.id}
                item={item}
                index={idx}
                total={items.length}
                disabled={saving}
                onMoveUp={() => moveUp(idx)}
                onMoveDown={() => moveDown(idx)}
                onRemove={() => removeItem(item.id)}
                onEdit={(patch) => editItemPatch(item.id, patch)}
                onInsertAfter={() => insertCustomTextAfter(item.id)}
              />
            ))}
          </SortableContext>
        </DndContext>

        {!loading && items.length === 0 && (
          <div style={{ padding: 48, color: 'var(--ink-500)', textAlign: 'center' }}>
            No final draft items yet.
            <br />
            <span style={{ fontSize: 12 }}>
              Go to Regen Review and click <strong>Approve &amp; Save</strong> to seed the composer.
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// ComposerItem — renders one item per its type, with edit/move/remove.
// ─────────────────────────────────────────────────────────────────

function ComposerItem({
  item,
  index,
  total,
  disabled,
  onMoveUp,
  onMoveDown,
  onRemove,
  onEdit,
  onInsertAfter,
}: {
  item: FinalDraftItem;
  index: number;
  total: number;
  disabled: boolean;
  onMoveUp: () => void;
  onMoveDown: () => void;
  onRemove: () => void;
  onEdit: (patch: Record<string, unknown>) => void;
  onInsertAfter: () => void;
}) {
  const [editing, setEditing] = useState(false);
  // @dnd-kit sortable wiring
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: item.id });
  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
    cursor: isDragging ? 'grabbing' : 'default',
  };

  return (
    <>
      <div
        ref={setNodeRef}
        style={{
          ...style,
          background: 'var(--surface)',
          borderLeft: `3px solid ${typeAccent(item)}`,
          borderRadius: 4,
          padding: '6px 10px',
          marginBottom: 4,
          display: 'flex',
          gap: 8,
          alignItems: 'flex-start',
        }}
      >
        {/* Grip handle for drag + type chip */}
        <div
          {...attributes}
          {...listeners}
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 2,
            alignItems: 'center',
            cursor: 'grab',
            color: 'var(--ink-400)',
            userSelect: 'none',
            paddingTop: 2,
          }}
          title="Drag to reorder"
        >
          <span style={{ fontSize: 11, lineHeight: 1 }}>⋮⋮</span>
          <div
            style={{
              fontSize: 8,
              fontWeight: 700,
              letterSpacing: '0.04em',
              textTransform: 'uppercase',
              color: 'var(--ink-500)',
              padding: '1px 4px',
              border: '1px solid var(--line-2)',
              borderRadius: 3,
              background: 'var(--bg-tint)',
            }}
          >
            {chipLabel(item)}
          </div>
        </div>

        {/* Content */}
        <div style={{ flex: 1, minWidth: 0 }}>
          {editing ? (
            <EditForm item={item} onCancel={() => setEditing(false)} onSubmit={(patch) => { onEdit(patch); setEditing(false); }} />
          ) : (
            <ItemContent item={item} />
          )}
        </div>

        {/* Actions — appear inline on the right, small */}
        <div style={{ display: 'flex', gap: 2, opacity: 0.7 }}>
          <button
            className="btn btn-ghost btn-xs"
            onClick={onMoveUp}
            disabled={disabled || index === 0}
            title="Move up"
            style={{ padding: '2px 5px', fontSize: 12 }}
          >
            ↑
          </button>
          <button
            className="btn btn-ghost btn-xs"
            onClick={onMoveDown}
            disabled={disabled || index === total - 1}
            title="Move down"
            style={{ padding: '2px 5px', fontSize: 12 }}
          >
            ↓
          </button>
          {!editing && canEdit(item) && (
            <button className="btn btn-ghost btn-xs" onClick={() => setEditing(true)} disabled={disabled} title="Edit" style={{ padding: '2px 5px' }}>
              ✏
            </button>
          )}
          <button className="btn btn-ghost btn-xs" onClick={onRemove} disabled={disabled} title="Remove" style={{ padding: '2px 5px' }}>
            🗑
          </button>
        </div>
      </div>

      {/* Slim "+ add" between items — only visible on hover for low noise */}
      <div
        className="composer-add-row"
        style={{
          textAlign: 'center',
          height: 14,
          marginBottom: 2,
          position: 'relative',
        }}
      >
        <button
          className="btn btn-ghost btn-xs"
          onClick={onInsertAfter}
          disabled={disabled}
          style={{
            position: 'absolute',
            left: '50%',
            top: -3,
            transform: 'translateX(-50%)',
            fontSize: 10,
            color: 'var(--ink-400)',
            padding: '1px 8px',
            background: 'var(--surface)',
            border: '1px dashed var(--line)',
            borderRadius: 10,
            opacity: 0,
            transition: 'opacity 120ms',
          }}
          onMouseEnter={(e) => { (e.currentTarget.style.opacity = '1'); }}
          onMouseLeave={(e) => { (e.currentTarget.style.opacity = '0'); }}
        >
          + add
        </button>
      </div>
    </>
  );
}

function typeAccent(item: FinalDraftItem): string {
  if (item.type === 'section_heading') return 'var(--indigo-700)';
  if (item.type === 'block') return 'var(--ink-300)';
  if (item.type === 'figure') return '#9F7BFF';
  if (item.type === 'question') return 'var(--red-600)';
  if (item.type === 'custom_text') return 'var(--success)';
  return 'var(--ink-300)';
}

function chipLabel(item: FinalDraftItem): string {
  if (item.type === 'section_heading') return `H${item.level}`;
  if (item.type === 'block') return String(item.block.t ?? 'block').toUpperCase();
  if (item.type === 'figure') return 'FIG';
  if (item.type === 'question') return 'Q';
  if (item.type === 'custom_text') return 'TEXT';
  return 'ITEM';
}

function canEdit(item: FinalDraftItem): boolean {
  return item.type === 'block' || item.type === 'custom_text' || item.type === 'section_heading';
}

// ─────────────────────────────────────────────────────────────────
// ItemContent — read-only render based on type.
// ─────────────────────────────────────────────────────────────────

function ItemContent({ item }: { item: FinalDraftItem }) {
  if (item.type === 'section_heading') {
    const fs = item.level === 1 ? 18 : item.level === 2 ? 16 : 14;
    return (
      <div>
        <div style={{ fontSize: fs, fontWeight: 800, color: 'var(--ink-900)' }}>
          {item.title || '(untitled section)'}
        </div>
        <div style={{ fontSize: 10, color: 'var(--ink-500)', fontFamily: 'var(--font-mono)', marginTop: 2 }}>
          {item.section_id} · level {item.level} {item.regen ? '· ✨ regen' : ''}
        </div>
      </div>
    );
  }
  if (item.type === 'block') {
    return <BlockRender block={item.block} />;
  }
  if (item.type === 'figure') {
    return (
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
        <img
          src={`${API_BASE}${item.figure.image_url}`}
          alt={item.figure.label}
          style={{ width: 160, height: 'auto', borderRadius: 6, border: '1px solid var(--line-2)' }}
          onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
        />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--ink-900)' }}>
            {item.figure.label}
          </div>
          <div style={{ fontSize: 12, color: 'var(--ink-700)', marginTop: 4, lineHeight: 1.5 }}>
            {item.figure.caption || <em style={{ color: 'var(--ink-400)' }}>(no caption)</em>}
          </div>
        </div>
      </div>
    );
  }
  if (item.type === 'question') {
    const q = item.question;
    const diagram = (q as { regenerated_diagram?: RegeneratedDiagram | null })
      .regenerated_diagram ?? null;
    const showDiagram = !!(
      diagram && !diagram.fallback_to_original && diagram.svg_preview
    );
    // Embedded figures, routed by body_target exactly like the extraction /
    // regen review view: question-stem figures render under the question text,
    // solution figures inside the Solution block. null = question-side. A
    // regenerated diagram replaces the originals (→ no embedded figs shown).
    type _EF = {
      ref_id?: string; figure_id?: string; label?: string;
      caption?: string; image_url: string;
      body_target?: 'question' | 'solution' | null;
    };
    const _allEf = ((q as { embedded_figures?: _EF[] }).embedded_figures) ?? [];
    const figsQ = showDiagram ? [] : _allEf.filter((ef) => (ef.body_target ?? 'question') !== 'solution');
    const figsS = showDiagram ? [] : _allEf.filter((ef) => ef.body_target === 'solution');
    const renderFig = (ef: _EF) => {
      const src = ef.image_url?.startsWith('http') ? ef.image_url : `${API_BASE}${ef.image_url}`;
      return (
        <figure key={ef.ref_id ?? ef.figure_id} style={{ margin: '6px 0 0', textAlign: 'center' }}>
          <img
            src={src}
            alt={ef.caption || ef.label || 'Figure'}
            style={{ maxWidth: '100%', maxHeight: 220, borderRadius: 6, border: '1px solid var(--line-2)' }}
            onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
          />
          {(ef.label || ef.caption) && (
            <figcaption style={{ fontSize: 11, color: 'var(--ink-500)', marginTop: 3, fontStyle: 'italic' }}>
              {ef.label && <strong>{ef.label}</strong>}
              {ef.label && ef.caption && ' — '}
              {ef.caption}
            </figcaption>
          )}
        </figure>
      );
    };
    return (
      <div>
        <div style={{ fontSize: 13, color: 'var(--ink-900)', lineHeight: 1.55 }}>
          {stripFigPlaceholders(q.raw_text) || '(no question text)'}
        </div>
        {/* Step 2 — regenerated vector diagram (replaces the original figure) */}
        <DiagramPreview diagram={diagram} compact />
        {/* Question-body figures under the question text */}
        {figsQ.map(renderFig)}
        {q.solution_text && (
          <div style={{ marginTop: 6, padding: '8px 10px', background: 'var(--bg-tint)', borderRadius: 6, fontSize: 12, color: 'var(--ink-700)', lineHeight: 1.55 }}>
            <strong>Solution:</strong> {String(q.solution_text)}
            {/* Solution-body figures inside the Solution block */}
            {figsS.map(renderFig)}
          </div>
        )}
      </div>
    );
  }
  if (item.type === 'custom_text') {
    return (
      <div style={{ fontSize: 14, color: 'var(--ink-900)', lineHeight: 1.55, whiteSpace: 'pre-wrap' }}>
        {item.content || <em style={{ color: 'var(--ink-400)' }}>(empty custom text — click ✏ to edit)</em>}
      </div>
    );
  }
  return <div style={{ color: 'var(--ink-400)' }}>(unknown item type)</div>;
}

function BlockRender({ block }: { block: Block }) {
  const t = String(block.t ?? '');
  const c = String((block as { c?: string }).c ?? '');
  if (t === 'h3') return <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--ink-900)' }}>{c}</div>;
  if (t === 'p') return <div style={{ fontSize: 13, color: 'var(--ink-900)', lineHeight: 1.55, whiteSpace: 'pre-wrap' }}>{c}</div>;
  if (t === 'kp') return (
    <div style={{ background: '#FFF9E5', padding: '8px 10px', borderRadius: 6, borderLeft: '3px solid #C28000' }}>
      <div style={{ fontSize: 10, fontWeight: 800, color: '#8A5300', letterSpacing: '0.1em', marginBottom: 2 }}>KEY POINT</div>
      <div style={{ fontSize: 13 }}>{c}</div>
    </div>
  );
  if (t === 'eq') {
    // RAW OCR rendering — see PreviewPage comments.
    return (
      <div style={{ background: 'var(--bg-tint)', padding: '8px 10px', borderRadius: 6, fontSize: 13, color: 'var(--indigo-700)', fontFamily: 'var(--font-mono)', whiteSpace: 'pre-wrap' }}>
        {c}
      </div>
    );
  }
  if (t === 'def') {
    const term = String((block as { term?: string }).term ?? '');
    return <div><strong>{term}: </strong>{c}</div>;
  }
  if (t === 'list') {
    const items = ((block as { items?: string[] }).items ?? []);
    // Split-list parts (backend resolver, content_stream) carry `_split_start`
    // so the <ol> numbering continues across the figure cards interleaved
    // between parts — each part is its own draggable card but reads as one
    // list. Non-split lists have no `_split_start` → unchanged.
    const splitStart = (block as { _split_start?: number })._split_start;
    return <ol start={typeof splitStart === 'number' ? splitStart : undefined} style={{ paddingLeft: 18, fontSize: 13, lineHeight: 1.55, margin: 0 }}>
      {items.map((it, i) => <li key={i} style={{ whiteSpace: 'pre-wrap' }}>{it}</li>)}
    </ol>;
  }
  if (t === 'example_ref' || t === 'exercise_ref' || t === 'question_ref') {
    const label = String((block as { label?: string }).label ?? t.toUpperCase());
    return <span style={{ display: 'inline-block', padding: '2px 8px', background: 'var(--indigo-50)', color: 'var(--indigo-700)', borderRadius: 12, fontSize: 11, fontWeight: 700 }}>{label}</span>;
  }
  if (t === 'fig') {
    // The seeder drops fig BLOCKS when a matching figure ITEM exists at
    // the same position. A fig block that reaches this renderer means
    // the embedder couldn't link a figure here — show a muted callout
    // so the user sees a figure was expected, rather than a silent gap.
    const label = String((block as { label?: string }).label ?? '');
    return <div style={{ fontSize: 12, color: 'var(--ink-500)' }}>📷 Figure placeholder — {c || label}</div>;
  }
  return <div style={{ fontSize: 12, color: 'var(--ink-400)' }}>[{t}] {c}</div>;
}

// ─────────────────────────────────────────────────────────────────
// EditForm — inline editor based on item type.
// ─────────────────────────────────────────────────────────────────

function EditForm({
  item,
  onCancel,
  onSubmit,
}: {
  item: FinalDraftItem;
  onCancel: () => void;
  onSubmit: (patch: Record<string, unknown>) => void;
}) {
  const [text, setText] = useState(() => {
    if (item.type === 'block') return String((item.block as { c?: string }).c ?? '');
    if (item.type === 'custom_text') return item.content;
    if (item.type === 'section_heading') return item.title;
    return '';
  });

  const submit = () => {
    if (item.type === 'block') {
      onSubmit({ block: { ...item.block, c: text } });
    } else if (item.type === 'custom_text') {
      onSubmit({ content: text });
    } else if (item.type === 'section_heading') {
      onSubmit({ title: text });
    }
  };

  return (
    <div>
      <textarea
        autoFocus
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={Math.max(2, text.split('\n').length)}
        style={{ width: '100%', padding: 8, border: '1px solid var(--line)', borderRadius: 6, fontSize: 13, font: 'inherit' }}
      />
      <div style={{ marginTop: 8, display: 'flex', gap: 6 }}>
        <button className="btn btn-primary btn-xs" onClick={submit}>
          <Icon name="check" size={11} /> Save
        </button>
        <button className="btn btn-ghost btn-xs" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}
