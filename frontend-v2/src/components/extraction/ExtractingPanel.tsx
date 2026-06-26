// Polished extraction progress panel. Replaces the mock SchemaStep on
// the Upload page. Drives the real backend via useExtractionPipeline.
//
// Layout:
//   ┌────────────────────────────────────────────────────────────┐
//   │  Extracting "<chapter name>"                          XX%  │
//   │  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░         │
//   ├────────────────────────────────────────────────────────────┤
//   │  ✓  Schema      ─────  Detected N chapters                 │
//   │  ⟳  Theory      ─────  6 of 30 sections                    │
//   │  ⏸  Questions   ─────  waiting for theory                  │
//   │  ⏸  Figures     ─────  waiting for theory                  │
//   └────────────────────────────────────────────────────────────┘
//
//   On partial / done:
//     • Failed sections list with [Retry] buttons
//     • [Continue to Review →] CTA when done
//
// All behavior comes from the orchestrator hook — this is pure UI.

import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { Icon } from '../Icon';
import { SchemaViewerModal } from '../review/SchemaViewerModal';
import {
  useExtractionPipeline,
  type StageKey,
  type StageState,
} from '../../api/extractionPipeline';

type Props = {
  bookId: string;
  bookTitle: string;
  /** Called automatically when extraction reaches done|partial. */
  onComplete?: () => void;
};

const STAGE_META: Record<StageKey, { label: string; icon: 'layers' | 'sparkles' | 'question' | 'image' }> = {
  schema:    { label: 'Schema',    icon: 'sparkles' },
  theory:    { label: 'Theory',    icon: 'layers' },
  questions: { label: 'Questions', icon: 'question' },
  figures:   { label: 'Figures',   icon: 'image' },
};

export function ExtractingPanel({ bookId, bookTitle, onComplete }: Props) {
  const navigate = useNavigate();
  const pipeline = useExtractionPipeline();
  const [schemaOpen, setSchemaOpen] = useState(false);

  // Kick the pipeline once on mount.
  useEffect(() => {
    void pipeline.start(bookId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bookId]);

  // Fire onComplete callback when settled.
  useEffect(() => {
    if (pipeline.state.phase === 'done' || pipeline.state.phase === 'partial') {
      onComplete?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pipeline.state.phase]);

  const { state } = pipeline;
  const isTerminal =
    state.phase === 'done' ||
    state.phase === 'partial' ||
    state.phase === 'error';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      {/* Hero / overall progress */}
      <div
        className="card"
        style={{
          padding: 26,
          background: 'linear-gradient(180deg, var(--indigo-50), var(--surface))',
          borderColor: 'var(--indigo-100)',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'baseline',
            justifyContent: 'space-between',
            marginBottom: 14,
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
                letterSpacing: '0.1em',
                textTransform: 'uppercase',
                color: 'var(--indigo-700)',
              }}
            >
              {!isTerminal && (
                <span
                  className="spinner dark"
                  style={{ width: 11, height: 11, borderWidth: 1.5 }}
                />
              )}
              {state.phase === 'done' && <Icon name="check" size={13} />}
              {state.phase === 'partial' && '⚠'}
              {state.phase === 'error' && '⚠'}
              {phaseLabel(state.phase)}
            </div>
            <h2
              style={{
                fontSize: 22,
                fontWeight: 800,
                letterSpacing: '-0.02em',
                color: 'var(--ink-900)',
                margin: '6px 0 0',
              }}
            >
              {bookTitle}
            </h2>
          </div>
          <div
            className="mono"
            style={{
              fontSize: 32,
              fontWeight: 800,
              color: 'var(--ink-900)',
              letterSpacing: '-0.02em',
              lineHeight: 1,
            }}
          >
            {state.overallPct}
            <span style={{ fontSize: 16, color: 'var(--ink-500)', fontWeight: 600 }}>
              %
            </span>
          </div>
        </div>
        <div className="progress-rail" style={{ height: 10 }}>
          <div className="progress-fill" style={{ width: `${state.overallPct}%` }} />
        </div>
        {state.errorMessage && (
          <div
            style={{
              marginTop: 14,
              padding: '12px 16px',
              background: 'var(--red-50)',
              border: '1px solid var(--red-100)',
              borderRadius: 10,
              color: 'var(--red-700)',
              fontSize: 13,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 12,
            }}
          >
            <span style={{ flex: 1, minWidth: 0 }}>{state.errorMessage}</span>
            {state.phase === 'error' && (
              <button
                className="btn btn-primary btn-sm"
                onClick={() => void pipeline.retryStage('schema')}
                title="Re-run analyse to rebuild the schema from scratch"
              >
                <Icon name="regen" size={12} /> Retry schema
              </button>
            )}
          </div>
        )}
      </div>

      {/* Per-stage rows */}
      <div className="card" style={{ padding: 0 }}>
        {(['schema', 'theory', 'questions', 'figures'] as StageKey[]).map(
          (key, i) => {
            const stage = state[key];
            return (
              <StageRow
                key={key}
                isFirst={i === 0}
                stageKey={key}
                stage={stage}
                onRetry={() => void pipeline.retryStage(key)}
                // Only the schema row gets a "view schema" eye icon —
                // opens the SchemaViewerModal. Other rows get nothing.
                onViewSchema={
                  key === 'schema' ? () => setSchemaOpen(true) : undefined
                }
              />
            );
          },
        )}
      </div>

      {/* Section completeness summary — only after reconcile */}
      {(state.phase === 'done' || state.phase === 'partial') &&
        state.sectionCounts.expected > 0 && (
          <CompletenessSummary state={state} />
        )}

      {/* Failed sections retry list */}
      {state.failedSections.length > 0 && (
        <FailedSectionsList
          failures={state.failedSections}
          onRetry={(id) => void pipeline.retrySection(id)}
        />
      )}

      {/* Missing sections — never created */}
      {state.missingSections.length > 0 && (
        <MissingSectionsList
          missing={state.missingSections}
          onRetryAll={() => void pipeline.retryAllTheory()}
        />
      )}

      {/* Continue CTAs when settled — Start regeneration is primary */}
      {(state.phase === 'done' || state.phase === 'partial') && (
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            gap: 10,
            padding: '16px 18px',
            background: 'var(--surface)',
            border: '1px solid var(--line)',
            borderRadius: 12,
          }}
        >
          <div style={{ flex: 1, minWidth: 0 }}>
            <div
              style={{
                fontSize: 14,
                fontWeight: 700,
                color: 'var(--ink-900)',
                letterSpacing: '-0.005em',
              }}
            >
              Extraction {state.phase === 'done' ? 'complete' : 'finished with partial results'}
            </div>
            <div
              style={{ fontSize: 12, color: 'var(--ink-500)', marginTop: 2 }}
            >
              Ready to regenerate. You can also peek at the extracted content
              before starting.
            </div>
          </div>
          <button
            className="btn btn-ghost"
            onClick={() => navigate(`/books/${bookId}/review`)}
            title="View extracted content"
          >
            <Icon name="eye" size={14} /> View extracted
          </button>
          <button
            className="btn btn-primary"
            onClick={() => navigate(`/books/${bookId}/regenerate`)}
          >
            <Icon name="regen" size={14} /> Start regeneration
          </button>
        </div>
      )}

      <SchemaViewerModal
        bookId={bookId}
        open={schemaOpen}
        onClose={() => setSchemaOpen(false)}
      />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Stage row
// ─────────────────────────────────────────────────────────────────────

function StageRow({
  stageKey,
  stage,
  isFirst,
  onRetry,
  onViewSchema,
}: {
  stageKey: StageKey;
  stage: StageState;
  isFirst: boolean;
  onRetry: () => void;
  /** When set, the row renders a small "eye" icon button that opens the
   *  schema viewer. Only wired for the 'schema' stage row. */
  onViewSchema?: () => void;
}) {
  const meta = STAGE_META[stageKey];
  const isDone = stage.status === 'done';
  const isRunning = stage.status === 'running';
  const isQueued = stage.status === 'queued';
  const isFailed = stage.status === 'failed';

  const dotBg = isDone
    ? 'var(--success)'
    : isFailed
    ? 'var(--red-600)'
    : isRunning
    ? 'var(--indigo-700)'
    : 'var(--bg-tint)';

  const dotColor = stage.status === 'unknown' ? 'var(--ink-400)' : '#fff';

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '40px 130px 1fr 80px auto',
        alignItems: 'center',
        gap: 14,
        padding: '14px 22px',
        borderTop: isFirst ? 'none' : '1px solid var(--line-2)',
      }}
    >
      <div
        style={{
          width: 28,
          height: 28,
          borderRadius: '50%',
          background: dotBg,
          color: dotColor,
          display: 'grid',
          placeItems: 'center',
          flexShrink: 0,
        }}
      >
        {isDone ? (
          <Icon name="check" size={14} />
        ) : isFailed ? (
          '!'
        ) : isRunning ? (
          <span
            className="spinner"
            style={{ width: 10, height: 10, borderWidth: 1.5 }}
          />
        ) : (
          <Icon name={meta.icon} size={13} />
        )}
      </div>
      <div>
        <div
          style={{
            fontSize: 14,
            fontWeight: 700,
            color: 'var(--ink-900)',
            letterSpacing: '-0.005em',
          }}
        >
          {meta.label}
        </div>
        <div
          style={{
            fontSize: 11,
            color:
              isFailed
                ? 'var(--red-700)'
                : isDone
                ? 'var(--success)'
                : 'var(--ink-500)',
            marginTop: 2,
          }}
        >
          {isDone
            ? 'Done'
            : isFailed
            ? 'Failed'
            : isRunning
            ? 'Running'
            : isQueued
            ? 'Queued'
            : '—'}
        </div>
      </div>
      <div
        style={{
          fontSize: 13,
          color: 'var(--ink-700)',
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
        }}
      >
        {stage.error ? (
          <span style={{ color: 'var(--red-700)' }}>{stage.error}</span>
        ) : stage.message ? (
          stage.message
        ) : (
          '—'
        )}
      </div>
      <div
        className="mono"
        style={{
          fontSize: 14,
          fontWeight: 700,
          textAlign: 'right',
          color: isDone
            ? 'var(--success)'
            : isFailed
            ? 'var(--red-700)'
            : 'var(--ink-700)',
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {stage.progress}%
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        {onViewSchema && (
          <button
            className="btn btn-ghost btn-sm"
            onClick={onViewSchema}
            title="View schema details"
            style={{ padding: '4px 8px' }}
          >
            <Icon name="eye" size={14} />
          </button>
        )}
        {isFailed && (
          <button className="btn btn-ghost btn-sm" onClick={onRetry}>
            <Icon name="regen" size={12} /> Retry
          </button>
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Section completeness summary
// ─────────────────────────────────────────────────────────────────────

function CompletenessSummary({
  state,
}: {
  state: ReturnType<typeof useExtractionPipeline>['state'];
}) {
  const c = state.sectionCounts;
  const anyIssue = c.failed > 0 || c.missing > 0 || c.inFlight > 0;
  return (
    <div
      className="card"
      style={{
        padding: 18,
        background: anyIssue ? 'var(--red-50)' : 'var(--success-bg)',
        border: '1px solid ' + (anyIssue ? 'var(--red-100)' : '#A8DCC4'),
      }}
    >
      <div
        style={{
          fontSize: 11,
          fontWeight: 700,
          letterSpacing: '0.08em',
          textTransform: 'uppercase',
          color: anyIssue ? 'var(--red-700)' : '#0B6A4F',
          marginBottom: 10,
        }}
      >
        Section completeness
      </div>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(5, 1fr)',
          gap: 12,
        }}
      >
        <Stat label="Expected" value={c.expected} />
        <Stat label="Ready"    value={c.ready}    color="#0B6A4F" />
        <Stat label="Failed"   value={c.failed}   color={c.failed > 0 ? 'var(--red-700)' : 'var(--ink-400)'} />
        <Stat label="In-flight" value={c.inFlight} color={c.inFlight > 0 ? '#8A5300' : 'var(--ink-400)'} />
        <Stat label="Missing"  value={c.missing}  color={c.missing > 0 ? 'var(--red-700)' : 'var(--ink-400)'} />
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color?: string;
}) {
  return (
    <div>
      <div
        style={{
          fontSize: 10,
          fontWeight: 700,
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
          marginTop: 2,
          color: color ?? 'var(--ink-900)',
        }}
      >
        {value}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Failed sections list
// ─────────────────────────────────────────────────────────────────────

function FailedSectionsList({
  failures,
  onRetry,
}: {
  failures: ReturnType<typeof useExtractionPipeline>['state']['failedSections'];
  onRetry: (sectionRowId: string) => void;
}) {
  return (
    <div
      className="card"
      style={{
        padding: 0,
        background: 'var(--red-50)',
        border: '1px solid var(--red-100)',
      }}
    >
      <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--red-100)' }}>
        <div
          style={{
            fontSize: 13,
            fontWeight: 700,
            color: 'var(--red-700)',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}
        >
          ⚠ Failed sections — {failures.length}
        </div>
        <div style={{ fontSize: 12, color: 'var(--ink-500)', marginTop: 2 }}>
          Backend tried and gave up. Click Retry to re-run one section.
        </div>
      </div>
      {failures.map((f) => (
        <div
          key={f.id}
          style={{
            padding: '12px 18px',
            display: 'grid',
            gridTemplateColumns: '1fr 60px auto',
            gap: 12,
            alignItems: 'center',
            borderTop: '1px solid var(--red-100)',
            background: '#fff',
          }}
        >
          <div style={{ minWidth: 0 }}>
            <div
              style={{
                fontSize: 13,
                fontWeight: 600,
                color: 'var(--ink-900)',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
            >
              {f.title}
            </div>
            <div
              className="mono"
              style={{
                fontSize: 11,
                color: 'var(--ink-500)',
                marginTop: 2,
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
            >
              {f.section_id} · {f.attempts} attempts
            </div>
          </div>
          <div style={{ fontSize: 11, color: 'var(--red-700)' }}>
            {f.error ? f.error.slice(0, 40) : '—'}
          </div>
          <button className="btn btn-ghost btn-sm" onClick={() => onRetry(f.id)}>
            <Icon name="regen" size={12} /> Retry
          </button>
        </div>
      ))}
    </div>
  );
}

function MissingSectionsList({
  missing,
  onRetryAll,
}: {
  missing: ReturnType<typeof useExtractionPipeline>['state']['missingSections'];
  onRetryAll: () => void;
}) {
  return (
    <div
      className="card"
      style={{
        padding: 18,
        background: 'var(--warning-bg)',
        border: '1px solid #F5E2BD',
        display: 'flex',
        gap: 14,
        alignItems: 'center',
      }}
    >
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: '#8A5300' }}>
          ⚠ {missing.length} section{missing.length === 1 ? '' : 's'} never extracted
        </div>
        <div style={{ fontSize: 12, color: 'var(--ink-700)', marginTop: 2 }}>
          Schema expected these but no rows exist. Re-run theory to process them.
        </div>
      </div>
      <button className="btn btn-accent btn-sm" onClick={onRetryAll}>
        <Icon name="regen" size={12} /> Re-run theory
      </button>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────

function phaseLabel(phase: ReturnType<typeof useExtractionPipeline>['state']['phase']): string {
  switch (phase) {
    case 'idle':         return 'Starting…';
    case 'loading':      return 'Loading…';
    case 'analysing':    return 'Building schema';
    case 'approving':    return 'Preparing extraction';
    case 'extracting':   return 'Extracting';
    case 'reconciling':  return 'Verifying';
    case 'done':         return 'Complete';
    case 'partial':      return 'Complete with issues';
    case 'error':        return 'Failed';
    default:             return phase;
  }
}
