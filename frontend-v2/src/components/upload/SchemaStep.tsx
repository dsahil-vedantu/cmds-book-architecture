import { useEffect, useState } from 'react';

import { ExtractionBanner } from './ExtractionBanner';
import { RegenToggle } from './RegenToggle';
import { Icon, type IconName } from '../Icon';
import {
  EXTRACTED_SCHEMA,
  EXTRACT_STAGES,
  type BookFolder,
  type PickedFile,
} from '../../mocks/upload';

type Props = {
  file: PickedFile;
  name: string;
  folder?: BookFolder;
  onBack: () => void;
  onStart: () => void;
};

type Depth = 'concise' | 'balanced' | 'thorough';

export function SchemaStep({ file, name, folder, onBack, onStart }: Props) {
  const [stageIdx, setStageIdx] = useState(0);
  const [done, setDone] = useState(false);
  const [regen, setRegen] = useState({ theory: true, questions: true, figures: false });
  const [variants, setVariants] = useState<1 | 2 | 3>(2);
  const [depth, setDepth] = useState<Depth>('balanced');

  // Auto-advance through extraction stages — one every 850ms — until done.
  useEffect(() => {
    if (done) return;
    if (stageIdx >= EXTRACT_STAGES.length) {
      setDone(true);
      return;
    }
    const t = window.setTimeout(() => setStageIdx((i) => i + 1), 850);
    return () => window.clearTimeout(t);
  }, [stageIdx, done]);

  const sch = EXTRACTED_SCHEMA;
  const totalSec = sch.chapters.reduce((s, c) => s + c.sections, 0);
  const totalQ = sch.chapters.reduce((s, c) => s + c.questions, 0);
  const totalF = sch.chapters.reduce((s, c) => s + c.figures, 0);

  const estimatedMinutes =
    (regen.theory ? 6 : 0) + (regen.questions ? 5 : 0) + (regen.figures ? 8 : 0);
  const startDisabled =
    !done || (!regen.theory && !regen.questions && !regen.figures);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      {!done && <ExtractionBanner stageIdx={stageIdx} stages={EXTRACT_STAGES} file={file} />}

      {/* Schema preview — greyed while still extracting */}
      <div
        className="card"
        style={{
          padding: 0,
          opacity: done ? 1 : 0.55,
          transition: 'opacity 360ms',
          pointerEvents: done ? 'auto' : 'none',
        }}
      >
        <div
          style={{
            padding: '20px 24px',
            borderBottom: '1px solid var(--line)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <div>
            <div
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 8,
                fontSize: 11,
                fontWeight: 700,
                letterSpacing: '0.08em',
                textTransform: 'uppercase',
                color: done ? 'var(--success)' : 'var(--ink-500)',
              }}
            >
              {done ? (
                <>
                  <Icon name="check" size={12} /> Extraction complete
                </>
              ) : (
                'Schema (loading)'
              )}
            </div>
            <h3
              style={{
                fontSize: 18,
                fontWeight: 700,
                color: 'var(--ink-900)',
                margin: '6px 0 0',
                letterSpacing: '-0.01em',
              }}
            >
              {name}
            </h3>
            {folder && (
              <div style={{ fontSize: 12.5, color: 'var(--ink-500)', marginTop: 4 }}>
                Folder:{' '}
                <span
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 4,
                  }}
                >
                  <span
                    style={{
                      width: 6,
                      height: 6,
                      borderRadius: 2,
                      background: folder.color,
                    }}
                  />
                  <strong style={{ color: 'var(--ink-800)' }}>{folder.name}</strong>
                </span>
              </div>
            )}
          </div>
          <div style={{ display: 'flex', gap: 22 }}>
            <SchemaStat label="Chapters" value={sch.chapters.length} />
            <SchemaStat label="Sections" value={totalSec} />
            <SchemaStat label="Questions" value={totalQ} />
            <SchemaStat label="Figures" value={totalF} />
            <SchemaStat label="Pages" value={sch.pages} />
          </div>
        </div>

        <div style={{ padding: '6px 0' }}>
          {sch.chapters.map((c, i) => (
            <div
              key={c.n}
              style={{
                display: 'grid',
                gridTemplateColumns: '36px 1fr 80px 90px 80px',
                alignItems: 'center',
                gap: 14,
                padding: '11px 24px',
                borderTop: i === 0 ? 'none' : '1px solid var(--line-2)',
              }}
            >
              <div
                className="mono"
                style={{ fontSize: 12, fontWeight: 700, color: 'var(--ink-400)' }}
              >
                {String(c.n).padStart(2, '0')}
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <Icon name="book" size={14} className="muted" />
                <div
                  style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink-900)' }}
                >
                  {c.title}
                </div>
              </div>
              <ColCount icon="layers" v={c.sections} lbl="sec" />
              <ColCount icon="question" v={c.questions} lbl="Q" />
              <ColCount icon="image" v={c.figures} lbl="fig" />
            </div>
          ))}
        </div>
      </div>

      {/* Regen options */}
      <div
        className="card"
        style={{
          padding: 24,
          opacity: done ? 1 : 0.4,
          transition: 'opacity 360ms',
          pointerEvents: done ? 'auto' : 'none',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            marginBottom: 6,
          }}
        >
          <Icon name="wand" size={15} style={{ color: 'var(--indigo-700)' }} />
          <h3
            style={{
              fontSize: 16,
              fontWeight: 700,
              color: 'var(--ink-900)',
              margin: 0,
              letterSpacing: '-0.01em',
            }}
          >
            What should we regenerate?
          </h3>
        </div>
        <div style={{ fontSize: 13, color: 'var(--ink-500)', marginBottom: 18 }}>
          Pick which extracted content gets the AI rewrite pass. You can re-run any of these
          later from the book view.
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
          <RegenToggle
            on={regen.theory}
            onToggle={() => setRegen((r) => ({ ...r, theory: !r.theory }))}
            icon="layers"
            label="Theory"
            sub={`${totalSec} sections`}
            meta={
              <>
                <div style={{ fontSize: 11, color: 'var(--ink-500)', marginBottom: 6 }}>
                  Verbosity
                </div>
                <div style={{ display: 'flex', gap: 4 }}>
                  {(['concise', 'balanced', 'thorough'] as Depth[]).map((v) => (
                    <SegmentButton
                      key={v}
                      label={v}
                      active={depth === v}
                      disabled={!regen.theory}
                      onClick={() => setDepth(v)}
                    />
                  ))}
                </div>
              </>
            }
          />
          <RegenToggle
            on={regen.questions}
            onToggle={() => setRegen((r) => ({ ...r, questions: !r.questions }))}
            icon="question"
            label="Questions"
            sub={`${totalQ} items`}
            meta={
              <>
                <div style={{ fontSize: 11, color: 'var(--ink-500)', marginBottom: 6 }}>
                  Variants per question
                </div>
                <div style={{ display: 'flex', gap: 4 }}>
                  {([1, 2, 3] as const).map((v) => (
                    <SegmentButton
                      key={v}
                      label={String(v)}
                      active={variants === v}
                      disabled={!regen.questions}
                      onClick={() => setVariants(v)}
                    />
                  ))}
                </div>
              </>
            }
          />
          <RegenToggle
            on={regen.figures}
            onToggle={() => setRegen((r) => ({ ...r, figures: !r.figures }))}
            icon="image"
            label="Figures"
            sub={`${totalF} images`}
            meta={
              <div
                style={{
                  fontSize: 11.5,
                  color: 'var(--ink-500)',
                  lineHeight: 1.5,
                  padding: '4px 0',
                }}
              >
                AI redraws low-res diagrams and adds clean labels. Adds ~8 min to processing.
              </div>
            }
          />
        </div>
      </div>

      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <button className="btn btn-ghost" onClick={onBack}>
          <Icon name="arrow-l" size={14} /> Back
        </button>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <span style={{ fontSize: 12, color: 'var(--ink-500)' }}>
            {done ? (
              <>
                Estimated regeneration:{' '}
                <strong className="mono" style={{ color: 'var(--ink-800)' }}>
                  ~{estimatedMinutes} min
                </strong>
              </>
            ) : (
              'Waiting for extraction to finish…'
            )}
          </span>
          <button
            className="btn btn-accent"
            disabled={startDisabled}
            onClick={onStart}
            style={{ opacity: startDisabled ? 0.5 : 1 }}
          >
            Start regeneration <Icon name="play" size={13} />
          </button>
        </div>
      </div>
    </div>
  );
}

function SchemaStat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div
        style={{
          fontSize: 10.5,
          fontWeight: 600,
          letterSpacing: '0.08em',
          textTransform: 'uppercase',
          color: 'var(--ink-500)',
        }}
      >
        {label}
      </div>
      <div
        className="mono"
        style={{
          fontSize: 22,
          fontWeight: 800,
          color: 'var(--ink-900)',
          letterSpacing: '-0.02em',
          marginTop: 2,
          lineHeight: 1,
        }}
      >
        {value}
      </div>
    </div>
  );
}

function ColCount({ icon, v, lbl }: { icon: IconName; v: number; lbl: string }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        color: 'var(--ink-500)',
        fontSize: 12.5,
      }}
    >
      <Icon name={icon} size={12} />
      <span className="mono" style={{ fontWeight: 600, color: 'var(--ink-700)' }}>
        {v}
      </span>
      <span style={{ fontSize: 11 }}>{lbl}</span>
    </div>
  );
}

function SegmentButton({
  label,
  active,
  disabled,
  onClick,
}: {
  label: string;
  active: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="btn btn-sm"
      style={{
        flex: 1,
        padding: 0,
        textTransform: 'capitalize',
        background: active ? 'var(--indigo-700)' : 'var(--surface)',
        color: active ? '#fff' : 'var(--ink-800)',
        border: active ? '1px solid var(--indigo-700)' : '1px solid var(--line)',
        justifyContent: 'center',
        opacity: disabled ? 0.5 : 1,
        fontSize: 11.5,
        height: 28,
      }}
    >
      {label}
    </button>
  );
}
