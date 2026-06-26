// Real Figures view — shows extracted figures with thumbnails for the
// selected section.

import { useState } from 'react';

import { Icon } from '../Icon';
import {
  figureImageUrl,
  figureOriginalImageUrl,
  regenerateFigureDiagram,
  redrawFigure,
  approveFigure,
  unapproveFigure,
  type Figure,
  type SectionFigures,
} from '../../api/figures';

type Props = {
  sectionRef: string | null;
  sectionFigures: SectionFigures | null;
  loading?: boolean;
  emptyMessage?: string;
};

export function FiguresView({
  sectionRef,
  sectionFigures,
  loading,
  emptyMessage,
}: Props) {
  if (loading) {
    return (
      <div
        style={{
          flex: 1,
          padding: 48,
          color: 'var(--ink-500)',
          textAlign: 'center',
        }}
      >
        Loading figures…
      </div>
    );
  }

  if (!sectionRef) {
    return (
      <div
        style={{
          flex: 1,
          padding: 48,
          color: 'var(--ink-500)',
          textAlign: 'center',
        }}
      >
        Pick a section from the left to see its figures.
      </div>
    );
  }

  if (emptyMessage) {
    return (
      <div
        style={{
          flex: 1,
          padding: 48,
          color: 'var(--ink-500)',
          textAlign: 'center',
        }}
      >
        {emptyMessage}
      </div>
    );
  }

  if (!sectionFigures || sectionFigures.figures.length === 0) {
    return (
      <div
        style={{
          flex: 1,
          padding: 48,
          color: 'var(--ink-500)',
          textAlign: 'center',
        }}
      >
        No figures in this section.
      </div>
    );
  }

  return (
    <div
      style={{
        flex: 1,
        overflowY: 'auto',
        padding: '28px 40px 56px',
        background: 'var(--bg)',
      }}
    >
      <div style={{ maxWidth: 980, margin: '0 auto' }}>
        <div style={{ marginBottom: 18 }}>
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
            {sectionFigures.section_ref}
          </div>
          <h1
            style={{
              fontSize: 24,
              fontWeight: 800,
              letterSpacing: '-0.02em',
              color: 'var(--ink-900)',
              margin: '6px 0 0',
            }}
          >
            {sectionFigures.figures.length} figure
            {sectionFigures.figures.length === 1 ? '' : 's'}
          </h1>
        </div>
        <div
          style={{
            display: 'grid',
            // Bigger cards — 1 column up to ~720px, 2 columns above that.
            // Each card is full-width so the image is genuinely big.
            gridTemplateColumns: 'repeat(auto-fill, minmax(420px, 1fr))',
            gap: 20,
          }}
        >
          {sectionFigures.figures.map((f) => (
            <FigureCard
              key={f.id}
              figure={f}
              sectionRef={sectionFigures.section_ref}
              sectionTitle={sectionFigures.section_title ?? null}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function FigureCard({
  figure: f,
  sectionRef,
  sectionTitle,
}: {
  figure: Figure;
  sectionRef: string;
  sectionTitle: string | null;
}) {
  const [imgErr, setImgErr] = useState(false);
  const [compareOpen, setCompareOpen] = useState(false);
  // Manual per-figure regen — two methods: 'latex' (vector, theory-aligned) and
  // 'clean' (image-model clean redraw). On-demand only.
  const [regenMethod, setRegenMethod] = useState<'latex' | 'clean' | null>(null);
  const [regenInstr, setRegenInstr] = useState('');
  const [regenBusy, setRegenBusy] = useState(false);
  const [regenMsg, setRegenMsg] = useState<string | null>(null);
  const [forceRegen, setForceRegen] = useState(false);
  const [bust, setBust] = useState(0);
  // Original ⇄ Regenerated choice (reversible approve/unapprove). When approved,
  // the regenerated image is what Preview/Composer/Export use; when not, the
  // ORIGINAL is used. Mirrors the figure's approval state.
  const [approved, setApproved] = useState<boolean>(f.is_approved ?? false);
  const [approveBusy, setApproveBusy] = useState(false);

  const hasRegen = forceRegen || f.has_regen;
  // The card shows whatever is actually IN USE downstream: regen iff approved.
  const showingRegen = hasRegen && approved;
  const baseUrl = showingRegen
    ? figureImageUrl(f.id, true)
    : f.has_original
      ? figureImageUrl(f.id)
      : hasRegen
        ? figureImageUrl(f.id, true)
        : null;
  // Cache-bust after a regen / variant switch so the browser re-fetches.
  const url = baseUrl
    ? bust
      ? baseUrl + (baseUrl.includes('?') ? '&' : '?') + '_t=' + bust
      : baseUrl
    : null;

  const canCompare = f.has_original && hasRegen;

  const toggleVariant = async () => {
    if (!hasRegen) return;
    setApproveBusy(true);
    try {
      if (approved) {
        await unapproveFigure(f.id);
        setApproved(false);
      } else {
        await approveFigure(f.id);
        setApproved(true);
      }
      setBust(Date.now());
      setImgErr(false);
    } catch (_e) {
      /* leave state unchanged on failure */
    } finally {
      setApproveBusy(false);
    }
  };

  const runRegen = async () => {
    if (!regenMethod) return;
    setRegenBusy(true);
    setRegenMsg(null);
    try {
      const instr = regenInstr.trim() || null;
      if (regenMethod === 'latex') {
        const r = await regenerateFigureDiagram(f.id, instr);
        if (r.ok) {
          setForceRegen(true);
          setApproved(true); // regen buttons auto-approve on the backend
          setBust(Date.now());
          setImgErr(false);
          setRegenMethod(null);
          setRegenInstr('');
        } else if (r.fallback) {
          setRegenMsg(r.message || 'Diagram too complex to vectorize — original kept.');
        } else {
          setRegenMsg('Regeneration failed.');
        }
      } else {
        const r = await redrawFigure(f.id, { custom_instructions: instr });
        if (r.ok) {
          setForceRegen(true);
          setApproved(true); // regen buttons auto-approve on the backend
          setBust(Date.now());
          setImgErr(false);
          setRegenMethod(null);
          setRegenInstr('');
        } else {
          setRegenMsg('Redraw failed.');
        }
      }
    } catch (e) {
      setRegenMsg(e instanceof Error ? e.message : 'Request failed');
    } finally {
      setRegenBusy(false);
    }
  };

  return (
    <div
      className="card"
      style={{
        padding: 0,
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <div
        style={{
          minHeight: 420,
          background: 'var(--surface-2)',
          display: 'grid',
          placeItems: 'center',
          borderBottom: '1px solid var(--line)',
          position: 'relative',
          overflow: 'hidden',
          padding: 12,
        }}
      >
        {url && !imgErr ? (
          <img
            key={`${f.id}-${showingRegen ? 'regen' : 'orig'}`}
            src={url}
            alt={f.caption ?? f.figure_number ?? 'Figure'}
            onError={() => setImgErr(true)}
            style={{
              maxWidth: '100%',
              maxHeight: '100%',
              objectFit: 'contain',
            }}
          />
        ) : (
          <div
            style={{
              color: 'var(--ink-400)',
              fontSize: 12,
              textAlign: 'center',
              padding: 16,
            }}
          >
            <Icon name="image" size={28} />
            <div style={{ marginTop: 6 }}>
              {imgErr ? 'Image unavailable' : 'No image yet'}
            </div>
          </div>
        )}
        {/* Variant indicator — small chip top-right so reviewers know
            whether they're looking at original or regen by default. */}
        <span
          className={showingRegen ? 'badge regen' : 'badge'}
          style={{
            position: 'absolute',
            top: 8,
            right: 8,
            fontSize: 10,
            background: showingRegen ? undefined : 'var(--surface)',
          }}
        >
          {showingRegen ? (
            <>
              <Icon name="sparkles" size={10} /> regenerated
            </>
          ) : (
            'original'
          )}
        </span>
        {/* Failed-regen badge — reviewer can see at a glance which figures
            need a retry. Section-level "Reseed figures" button picks them
            up on next run. */}
        {f.regen_status === 'failed' && (
          <span
            style={{
              position: 'absolute',
              bottom: 8,
              left: 8,
              fontSize: 10,
              padding: '3px 8px',
              border: '1px solid var(--red-200, #fca5a5)',
              borderRadius: 6,
              background: 'var(--red-50, #fee2e2)',
              color: 'var(--red-700, #b91c1c)',
              fontWeight: 700,
              letterSpacing: '0.04em',
            }}
            title="Last regen attempt failed — use Reseed Figures for this section to retry."
          >
            ⚠ regen failed
          </span>
        )}
        {/* Engine badge — which engine produced the current regen variant
            (table_embed = crisp vector grid + embedded graphic; vector =
            LaTeX/SVG; image = image-model redraw). */}
        {f.has_regen && f.regen_meta?.engine && (
          <span
            style={{
              position: 'absolute',
              bottom: 8,
              right: 8,
              fontSize: 10,
              padding: '3px 8px',
              border: '1px solid var(--line)',
              borderRadius: 6,
              background: 'var(--surface)',
              color: 'var(--ink-600, #475569)',
              fontWeight: 600,
              letterSpacing: '0.03em',
            }}
            title={`Regen engine: ${f.regen_meta.engine}${
              f.regen_meta.engine === 'table_embed' &&
              typeof f.regen_meta.graphics_embedded === 'number'
                ? ` · ${f.regen_meta.graphics_embedded} graphic(s) embedded`
                : ''
            }`}
          >
            {f.regen_meta.engine === 'table_embed'
              ? 'table'
              : f.regen_meta.engine === 'vector'
              ? 'vector'
              : 'image'}
          </span>
        )}
        {/* ↔ Compare button — only useful when both variants exist */}
        {canCompare && (
          <button
            onClick={() => setCompareOpen(true)}
            title="Compare original vs regenerated"
            style={{
              position: 'absolute',
              top: 8,
              left: 8,
              fontSize: 11,
              padding: '4px 10px',
              border: '1px solid var(--line)',
              borderRadius: 6,
              background: 'var(--surface)',
              color: 'var(--ink-900)',
              cursor: 'pointer',
              fontWeight: 700,
            }}
          >
            ↔ Compare
          </button>
        )}
      </div>
      <div style={{ padding: '14px 16px' }}>
        {/* Figure label — bold heading row */}
        <div
          style={{
            display: 'flex',
            alignItems: 'baseline',
            gap: 8,
            marginBottom: 6,
            flexWrap: 'wrap',
          }}
        >
          <div
            style={{
              fontSize: 15,
              fontWeight: 800,
              color: 'var(--ink-900)',
              letterSpacing: '-0.01em',
            }}
          >
            {f.figure_number ?? (f.normalized_label ? `Figure ${f.normalized_label}` : 'Figure')}
          </div>
          {f.context_hint && (
            <span
              className="kbd"
              style={{
                fontSize: 10,
                padding: '2px 8px',
                background:
                  f.context_hint === 'question'
                    ? 'var(--red-50)'
                    : 'var(--indigo-50)',
                color:
                  f.context_hint === 'question'
                    ? 'var(--red-700)'
                    : 'var(--indigo-700)',
                fontWeight: 700,
                letterSpacing: '0.05em',
                textTransform: 'uppercase',
              }}
            >
              {f.context_hint}
            </span>
          )}
        </div>
        {/* Section anchor — always visible so reviewers can confirm the
            figure is filed under the right section without scrolling. */}
        <div
          style={{
            fontSize: 11,
            color: 'var(--ink-500)',
            fontFamily: 'var(--font-mono)',
            letterSpacing: '0.04em',
            marginBottom: 6,
          }}
        >
          {sectionRef}
          {sectionTitle ? ` · ${sectionTitle}` : ''}
        </div>
        {f.caption && (
          <div
            style={{
              fontSize: 13,
              color: 'var(--ink-700)',
              lineHeight: 1.5,
              marginBottom: 4,
            }}
          >
            {f.caption}
          </div>
        )}
        <div
          style={{
            marginTop: 6,
            fontSize: 11,
            color: 'var(--ink-500)',
            display: 'flex',
            gap: 10,
          }}
        >
          {f.page_number && <span>p.{f.page_number}</span>}
          {f.semantic_type && <span>{f.semantic_type}</span>}
        </div>

        {/* Original ⇄ Regenerated toggle — only when a regen variant exists.
            Controls which image Preview/Composer/Export use (reversible). */}
        {hasRegen && (
          <div
            style={{
              marginTop: 10,
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              flexWrap: 'wrap',
            }}
          >
            <span style={{ fontSize: 11, color: 'var(--ink-500)' }}>
              In use:{' '}
              <strong style={{ color: showingRegen ? 'var(--teal-700, #0f766e)' : 'var(--ink-800)' }}>
                {showingRegen ? 'Regenerated' : 'Original'}
              </strong>
            </span>
            <button
              onClick={toggleVariant}
              disabled={approveBusy}
              title={
                showingRegen
                  ? 'Use the ORIGINAL figure in Preview / Composer / Export'
                  : 'Use the REGENERATED figure in Preview / Composer / Export'
              }
              style={{
                fontSize: 11,
                fontWeight: 600,
                color: 'var(--ink-800)',
                background: 'var(--surface)',
                border: '1px solid var(--line)',
                borderRadius: 6,
                padding: '3px 10px',
                cursor: approveBusy ? 'default' : 'pointer',
              }}
            >
              {approveBusy
                ? 'Switching…'
                : showingRegen
                  ? '↩ Use original'
                  : '✨ Use regenerated'}
            </button>
          </div>
        )}

        {/* Manual per-figure regen — two methods. On-demand only; never
            auto-updates. LaTeX = vector, aligned to regenerated theory/question.
            Redraw cleanly = image-model clean raster redraw. */}
        <div style={{ marginTop: 10, borderTop: '1px dashed var(--line)', paddingTop: 10 }}>
          {!regenMethod ? (
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <button
                onClick={() => { setRegenMethod('latex'); setRegenMsg(null); setRegenInstr(''); }}
                title="Regenerate as LaTeX/SVG vector, aligned to the regenerated theory/question"
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  color: 'var(--teal-700, #0f766e)',
                  background: 'transparent',
                  border: '1px dashed var(--teal-200, #99f6e4)',
                  borderRadius: 6,
                  padding: '4px 10px',
                  cursor: 'pointer',
                }}
              >
                🔁 Regenerate (LaTeX)
              </button>
              <button
                onClick={() => { setRegenMethod('clean'); setRegenMsg(null); setRegenInstr(''); }}
                title="Redraw this figure cleanly with AI (image model) — cosmetic clean-up of the existing figure"
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  color: 'var(--indigo-700)',
                  background: 'transparent',
                  border: '1px dashed var(--indigo-200, #c7d2fe)',
                  borderRadius: 6,
                  padding: '4px 10px',
                  cursor: 'pointer',
                }}
              >
                🎨 Redraw cleanly
              </button>
            </div>
          ) : (
            <div>
              <div style={{ fontSize: 11, fontWeight: 700, marginBottom: 6, color: 'var(--ink-600)' }}>
                {regenMethod === 'latex'
                  ? 'Regenerate as LaTeX (vector, theory/question-aligned)'
                  : 'Redraw cleanly (AI image redraw)'}
              </div>
              <textarea
                value={regenInstr}
                onChange={(e) => setRegenInstr(e.target.value)}
                placeholder={
                  regenMethod === 'latex'
                    ? "Optional: how should the figure change to match the new content? e.g. 'relabel axes', 'use the new values', 'solid black lines'"
                    : "Optional: redraw guidance, e.g. 'flat clean style', 'remove watermark', 'sharper labels'"
                }
                rows={2}
                disabled={regenBusy}
                style={{
                  width: '100%',
                  boxSizing: 'border-box',
                  fontSize: 12,
                  padding: 8,
                  borderRadius: 4,
                  border: '1px solid var(--line)',
                  resize: 'vertical',
                  fontFamily: 'inherit',
                }}
              />
              <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                <button
                  onClick={runRegen}
                  disabled={regenBusy}
                  style={{
                    fontSize: 11,
                    fontWeight: 600,
                    color: '#fff',
                    background: regenBusy
                      ? 'var(--ink-300, #cbd5e1)'
                      : regenMethod === 'latex'
                        ? 'var(--teal-700, #0f766e)'
                        : 'var(--indigo-700)',
                    border: 'none',
                    borderRadius: 6,
                    padding: '5px 12px',
                    cursor: regenBusy ? 'default' : 'pointer',
                  }}
                >
                  {regenBusy
                    ? regenMethod === 'latex' ? 'Regenerating…' : 'Redrawing…'
                    : regenMethod === 'latex' ? 'Regenerate diagram' : 'Redraw figure'}
                </button>
                <button
                  onClick={() => { setRegenMethod(null); setRegenMsg(null); }}
                  disabled={regenBusy}
                  style={{
                    fontSize: 11,
                    color: 'var(--ink-500)',
                    background: 'transparent',
                    border: '1px solid var(--line)',
                    borderRadius: 6,
                    padding: '5px 12px',
                    cursor: regenBusy ? 'default' : 'pointer',
                  }}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
          {regenMsg && (
            <div style={{ marginTop: 6, fontSize: 11, color: 'var(--amber-800, #92400e)' }}>
              {regenMsg}
            </div>
          )}
        </div>
      </div>

      {/* Compare modal — side-by-side Original vs Regenerated */}
      {compareOpen && canCompare && (
        <div
          onClick={() => setCompareOpen(false)}
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0,0,0,0.6)',
            zIndex: 1000,
            display: 'grid',
            placeItems: 'center',
            padding: 24,
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              background: 'var(--bg)',
              borderRadius: 12,
              padding: 20,
              maxWidth: 1200,
              width: '100%',
              maxHeight: '90vh',
              overflow: 'auto',
              boxShadow: '0 20px 60px rgba(0,0,0,0.4)',
            }}
          >
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginBottom: 12,
              }}
            >
              <div style={{ fontWeight: 800, fontSize: 16 }}>
                {f.figure_number ?? 'Figure'} — Original vs Regenerated
              </div>
              <button
                onClick={() => setCompareOpen(false)}
                style={{
                  border: 'none',
                  background: 'transparent',
                  fontSize: 20,
                  cursor: 'pointer',
                  color: 'var(--ink-500)',
                }}
              >
                ✕
              </button>
            </div>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: '1fr 1fr',
                gap: 12,
              }}
            >
              <div>
                <div
                  style={{
                    fontSize: 11,
                    fontWeight: 700,
                    letterSpacing: '0.1em',
                    textTransform: 'uppercase',
                    color: 'var(--ink-500)',
                    marginBottom: 6,
                  }}
                >
                  Original
                </div>
                <img
                  src={figureOriginalImageUrl(f.id)}
                  alt="original"
                  style={{
                    width: '100%',
                    maxHeight: '70vh',
                    objectFit: 'contain',
                    background: 'var(--surface-2)',
                    border: '1px solid var(--line)',
                    borderRadius: 8,
                  }}
                />
              </div>
              <div>
                <div
                  style={{
                    fontSize: 11,
                    fontWeight: 700,
                    letterSpacing: '0.1em',
                    textTransform: 'uppercase',
                    color: 'var(--indigo-700)',
                    marginBottom: 6,
                  }}
                >
                  ✨ Regenerated
                </div>
                <img
                  src={figureImageUrl(f.id, true)}
                  alt="regenerated"
                  style={{
                    width: '100%',
                    maxHeight: '70vh',
                    objectFit: 'contain',
                    background: 'var(--surface-2)',
                    border: '1px solid var(--line)',
                    borderRadius: 8,
                  }}
                />
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
