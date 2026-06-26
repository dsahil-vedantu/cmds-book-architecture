// Schema viewer / editor modal. Mirrors what the existing /frontend
// SchemaPage does at the data layer: read the book's schema_, allow
// edits (title rename, mark excluded, change content_types), persist
// via PATCH /api/books/:id/schema. Same endpoint, same payload shape,
// same backend logic — V-Studio just wraps it in a focused modal so
// ops users can peek + tweak without leaving the Review page.

import { useEffect, useMemo, useState } from 'react';

import { ApiError, req } from '../../api/client';
import type { components } from '../../api/generated';
import { Icon } from '../Icon';

type BookOut = components['schemas']['BookOut'];

export type SchemaNode = {
  id?: string;
  title?: string;
  type?: 'chapter' | 'section' | 'subsection' | 'excluded';
  content_types?: string[];
  expected_question_count?: number;
  page_start?: number;
  page_end?: number;
  subsections?: SchemaNode[];
};

type Schema = {
  document_title?: string;
  subject?: string;
  sections?: SchemaNode[];
  excluded_sections?: SchemaNode[];
  [k: string]: unknown;
};

type Props = {
  bookId: string;
  open: boolean;
  onClose: () => void;
  onSaved?: () => void;
};

export function SchemaViewerModal({ bookId, open, onClose, onSaved }: Props) {
  const [schema, setSchema] = useState<Schema | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);

  // Load schema when modal opens.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    void (async () => {
      try {
        const book = await req<BookOut>(`/api/books/${bookId}`);
        if (cancelled) return;
        const sc = (book.schema_ ?? null) as Schema | null;
        setSchema(sc);
        setDirty(false);
      } catch (e) {
        if (cancelled) return;
        setError(
          e instanceof ApiError
            ? `Backend ${e.status}: ${e.message}`
            : e instanceof Error
            ? e.message
            : 'Unknown error',
        );
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, bookId]);

  // Aggregate stats over the schema tree + excluded_sections array.
  const stats = useMemo(() => {
    if (!schema) return { chapters: 0, theory: 0, questions: 0, excluded: 0 };
    let chapters = 0, theory = 0, questions = 0, excluded = 0;
    const walk = (nodes: SchemaNode[]) => {
      for (const n of nodes) {
        if (n.type === 'chapter') chapters++;
        const ct = (n.content_types ?? []).map((c) => String(c).toLowerCase());
        if (n.type === 'excluded') excluded++;
        else if (ct.includes('questions')) questions++;
        else if (ct.includes('theory')) theory++;
        if (n.subsections?.length) walk(n.subsections);
      }
    };
    const walkExcluded = (nodes: SchemaNode[]) => {
      for (const n of nodes) {
        excluded++;
        if (n.subsections?.length) walkExcluded(n.subsections);
      }
    };
    walk(schema.sections ?? []);
    walkExcluded(schema.excluded_sections ?? []);
    return { chapters, theory, questions, excluded };
  }, [schema]);

  // Mutators — keep schema immutable per change.
  const updateNode = (path: number[], patch: Partial<SchemaNode>) => {
    if (!schema?.sections) return;
    const next = structuredClone(schema) as Schema;
    let nodes = next.sections!;
    let target: SchemaNode | undefined;
    for (let i = 0; i < path.length; i++) {
      target = nodes[path[i]];
      if (i < path.length - 1) {
        nodes = target.subsections ?? [];
      }
    }
    if (!target) return;
    Object.assign(target, patch);
    setSchema(next);
    setDirty(true);
  };

  const save = async () => {
    if (!schema || saving) return;
    setSaving(true);
    setError(null);
    try {
      await req<BookOut>(`/api/books/${bookId}/schema`, {
        method: 'PATCH',
        body: JSON.stringify(schema),
      });
      setDirty(false);
      setEditing(false);
      onSaved?.();
    } catch (e) {
      setError(
        e instanceof ApiError
          ? `Backend ${e.status}: ${e.message}`
          : e instanceof Error
          ? e.message
          : 'Save failed',
      );
    } finally {
      setSaving(false);
    }
  };

  if (!open) return null;

  return (
    <>
      <div
        onClick={() => !saving && onClose()}
        style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(15,23,42,0.45)',
          zIndex: 200,
        }}
      />
      <div
        className="card fade-up"
        style={{
          position: 'fixed',
          top: '5vh',
          bottom: '5vh',
          left: '50%',
          transform: 'translateX(-50%)',
          width: 'min(880px, 92vw)',
          padding: 0,
          zIndex: 201,
          boxShadow: 'var(--sh-pop)',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        {/* Header */}
        <div
          style={{
            padding: '18px 24px',
            borderBottom: '1px solid var(--line)',
            display: 'flex',
            alignItems: 'center',
            gap: 14,
          }}
        >
          <div
            style={{
              width: 36,
              height: 36,
              borderRadius: 10,
              background: 'var(--indigo-50)',
              color: 'var(--indigo-700)',
              display: 'grid',
              placeItems: 'center',
            }}
          >
            <Icon name="layers" size={18} />
          </div>
          <div style={{ flex: 1 }}>
            <h3
              style={{
                fontSize: 17,
                fontWeight: 800,
                color: 'var(--ink-900)',
                margin: 0,
                letterSpacing: '-0.01em',
              }}
            >
              Chapter schema
            </h3>
            <div
              style={{
                fontSize: 12,
                color: 'var(--ink-500)',
                marginTop: 2,
                display: 'flex',
                gap: 10,
                flexWrap: 'wrap',
                alignItems: 'center',
              }}
            >
              <span>{schema?.document_title ?? '—'}</span>
              <span style={{ opacity: 0.4 }}>·</span>
              <span>{stats.chapters} ch</span>
              <span style={{ opacity: 0.4 }}>·</span>
              <span style={{ color: 'var(--success)', fontWeight: 600 }}>
                {stats.theory} Cat B · Theory
              </span>
              <span style={{ opacity: 0.4 }}>·</span>
              <span style={{ color: 'var(--red-600)', fontWeight: 600 }}>
                {stats.questions} Cat A · Q
              </span>
              {stats.excluded > 0 && (
                <>
                  <span style={{ opacity: 0.4 }}>·</span>
                  <span style={{ color: 'var(--ink-400)' }}>
                    {stats.excluded} excluded
                  </span>
                </>
              )}
            </div>
          </div>
          <button
            className={`btn ${editing ? 'btn-soft' : 'btn-ghost'} btn-sm`}
            onClick={() => setEditing((v) => !v)}
            disabled={saving || loading}
          >
            {editing ? 'Editing' : 'Edit'}
          </button>
          <button className="btn btn-ghost btn-sm" onClick={onClose} disabled={saving}>
            Close
          </button>
        </div>

        {/* Body */}
        <div
          style={{
            flex: 1,
            overflowY: 'auto',
            padding: '16px 22px',
            background: 'var(--bg)',
          }}
        >
          {loading && (
            <div style={{ color: 'var(--ink-500)', padding: 18 }}>
              Loading schema…
            </div>
          )}
          {error && (
            <div
              style={{
                padding: 12,
                background: 'var(--red-50)',
                border: '1px solid var(--red-100)',
                borderRadius: 8,
                color: 'var(--red-700)',
                fontSize: 13,
                marginBottom: 14,
              }}
            >
              {error}
            </div>
          )}
          {schema?.sections && schema.sections.length > 0 && (
            <SchemaTree
              nodes={schema.sections}
              editing={editing}
              onChange={updateNode}
            />
          )}

          {schema?.excluded_sections && schema.excluded_sections.length > 0 && (
            <div style={{ marginTop: 22 }}>
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  padding: '8px 4px',
                  borderTop: '1px solid var(--line)',
                  marginTop: 10,
                  marginBottom: 12,
                }}
              >
                <Icon name="layers" size={14} />
                <h4
                  style={{
                    margin: 0,
                    fontSize: 13,
                    fontWeight: 700,
                    color: 'var(--ink-700)',
                    letterSpacing: '-0.005em',
                  }}
                >
                  Excluded sections
                </h4>
                <span style={{ fontSize: 11, color: 'var(--ink-500)' }}>
                  ({stats.excluded} item{stats.excluded === 1 ? '' : 's'} — end-of-chapter question banks,
                  hints, answer keys)
                </span>
              </div>
              <SchemaTree
                nodes={schema.excluded_sections}
                editing={editing}
                onChange={() => {/* excluded edits read-only for now */}}
                forceExcluded
              />
            </div>
          )}
        </div>

        {/* Footer */}
        <div
          style={{
            padding: '14px 24px',
            borderTop: '1px solid var(--line)',
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            background: 'var(--surface)',
          }}
        >
          <div style={{ fontSize: 12, color: 'var(--ink-500)' }}>
            {editing
              ? dirty
                ? 'Unsaved changes — click Save to persist.'
                : 'Edit mode — change titles, toggle exclude, save when done.'
              : 'Read-only view. Click Edit to make changes.'}
          </div>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
            {editing && (
              <button
                className="btn btn-primary"
                onClick={() => void save()}
                disabled={!dirty || saving}
              >
                {saving ? <span className="spinner" /> : <Icon name="check" size={14} />}
                Save schema
              </button>
            )}
          </div>
        </div>
      </div>
    </>
  );
}

// ─── Tree ───────────────────────────────────────────────────────────

function SchemaTree({
  nodes,
  editing,
  onChange,
  path = [],
  depth = 0,
  forceExcluded = false,
}: {
  nodes: SchemaNode[];
  editing: boolean;
  onChange: (path: number[], patch: Partial<SchemaNode>) => void;
  path?: number[];
  depth?: number;
  /** True when rendering the excluded_sections branch — show the
   *  Excluded badge regardless of the node's own type/content_types. */
  forceExcluded?: boolean;
}) {
  return (
    <div>
      {nodes.map((n, i) => (
        <SchemaRow
          key={`${depth}-${i}`}
          node={n}
          editing={editing}
          onChange={(patch) => onChange([...path, i], patch)}
          path={[...path, i]}
          depth={depth}
          forceExcluded={forceExcluded}
        >
          {n.subsections && n.subsections.length > 0 && (
            <SchemaTree
              nodes={n.subsections}
              editing={editing}
              onChange={onChange}
              path={[...path, i]}
              depth={depth + 1}
              forceExcluded={forceExcluded}
            />
          )}
        </SchemaRow>
      ))}
    </div>
  );
}

function SchemaRow({
  node,
  editing,
  onChange,
  depth,
  forceExcluded = false,
  children,
}: {
  node: SchemaNode;
  editing: boolean;
  onChange: (patch: Partial<SchemaNode>) => void;
  path: number[];
  depth: number;
  forceExcluded?: boolean;
  children?: React.ReactNode;
}) {
  const indent = depth * 18;
  const ct = (node.content_types ?? []).map((c) => String(c).toLowerCase());
  const isExcluded = forceExcluded || node.type === 'excluded';
  const hasTheory = ct.includes('theory');
  const hasQuestions = ct.includes('questions');
  const isMixed = hasTheory && hasQuestions;

  // Distinguish mixed (theory + questions) from pure Cat A (questions only).
  // Theory worker still extracts theory blocks from mixed sections —
  // they belong in BOTH tabs.
  const badge = isExcluded
    ? { label: 'Excluded', cls: 'idle' as const }
    : isMixed
    ? { label: 'Mixed · Theory + Q', cls: 'info' as const }
    : hasQuestions
    ? { label: 'Cat A · Q', cls: 'regen' as const }
    : hasTheory
    ? { label: 'Cat B · Theory', cls: 'ok' as const }
    : { label: node.type ?? 'section', cls: 'info' as const };

  return (
    <div style={{ marginLeft: indent }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          padding: '8px 10px',
          background: 'var(--surface)',
          border: '1px solid var(--line)',
          borderRadius: 8,
          marginBottom: 6,
          opacity: isExcluded ? 0.55 : 1,
        }}
      >
        <span className={`badge ${badge.cls}`} style={{ fontSize: 10 }}>
          <span className="dot" />
          {badge.label}
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          {editing ? (
            <input
              type="text"
              value={node.title ?? ''}
              onChange={(e) => onChange({ title: e.target.value })}
              style={{
                width: '100%',
                height: 30,
                padding: '0 8px',
                border: '1px solid var(--line)',
                borderRadius: 6,
                font: 'inherit',
                fontSize: 13,
                color: 'var(--ink-900)',
              }}
            />
          ) : (
            <div
              style={{
                fontSize: 13.5,
                fontWeight: 600,
                color: 'var(--ink-900)',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
            >
              {node.title || node.id}
            </div>
          )}
          <div
            className="mono"
            style={{
              fontSize: 10.5,
              color: 'var(--ink-500)',
              marginTop: 2,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {node.id}
            {node.expected_question_count != null && (
              <>· {node.expected_question_count} Q expected</>
            )}
          </div>
        </div>
        {editing && (
          <button
            className="btn btn-ghost btn-sm"
            onClick={() =>
              onChange({
                type: isExcluded ? 'section' : 'excluded',
              })
            }
            title={isExcluded ? 'Include in extraction' : 'Exclude from extraction'}
          >
            {isExcluded ? 'Include' : 'Exclude'}
          </button>
        )}
      </div>
      {children}
    </div>
  );
}
