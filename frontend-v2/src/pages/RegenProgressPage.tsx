// Regen progress page — landed-on after the user clicks "Run regeneration"
// on the Config page. Attaches to the saved kick (job IDs in
// sessionStorage), polls every 2s, and shows per-pipeline progress.
//
// On done|partial:  shows [View regenerated content →] CTA
//                   navigates to /books/:id/review?view=regen

import { useEffect } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { Icon } from '../components/Icon';
import {
  useRegenPipeline,
  type RegenStageKey,
  type RegenStageState,
} from '../api/regenPipeline';
import { useBook } from '../api/books';

export default function RegenProgressPage() {
  const { bookId } = useParams();
  const navigate = useNavigate();
  const bookState = useBook(bookId);
  const pipeline = useRegenPipeline();

  // Attach once on mount.
  useEffect(() => {
    if (!bookId) return;
    pipeline.attach(bookId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bookId]);

  const { state } = pipeline;
  const settled =
    state.phase === 'done' ||
    state.phase === 'partial' ||
    state.phase === 'error';

  const bookTitle =
    bookState.kind === 'ready' ? bookState.data.book.title : 'Loading…';

  return (
    <div className="content fade-up">
      <div className="content-narrow" style={{ maxWidth: 920 }}>
        {/* Header */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 14,
            marginBottom: 22,
          }}
        >
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => navigate(`/books/${bookId}/review`)}
          >
            <Icon name="arrow-l" size={14} /> Back to review
          </button>
          <div style={{ flex: 1 }}>
            <div
              style={{
                fontSize: 11,
                fontWeight: 700,
                letterSpacing: '0.12em',
                textTransform: 'uppercase',
                color: 'var(--ink-500)',
                marginBottom: 2,
              }}
            >
              Regenerating
            </div>
            <h1
              className="page-title"
              style={{ fontSize: 26, lineHeight: 1.15, margin: 0 }}
            >
              {bookTitle}
            </h1>
          </div>
        </div>

        {/* Hero progress card */}
        <div
          className="card"
          style={{
            padding: 26,
            background: 'linear-gradient(180deg, var(--indigo-50), var(--surface))',
            borderColor: 'var(--indigo-100)',
            marginBottom: 18,
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
              {!settled && (
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
                padding: '10px 14px',
                background: 'var(--red-50)',
                border: '1px solid var(--red-100)',
                borderRadius: 10,
                color: 'var(--red-700)',
                fontSize: 13,
              }}
            >
              {state.errorMessage}
            </div>
          )}
        </div>

        {/* Per-pipeline rows */}
        <div className="card" style={{ padding: 0 }}>
          {(['theory', 'questions', 'figures'] as RegenStageKey[]).map(
            (key, i) => (
              <StageRow
                key={key}
                stageKey={key}
                stage={state[key]}
                isFirst={i === 0}
              />
            ),
          )}
        </div>

        {/* CTAs when settled */}
        {settled && (
          <div
            style={{
              marginTop: 18,
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              padding: '16px 18px',
              background: 'var(--surface)',
              border: '1px solid var(--line)',
              borderRadius: 12,
            }}
          >
            <div style={{ flex: 1 }}>
              <div
                style={{
                  fontSize: 14,
                  fontWeight: 700,
                  color: 'var(--ink-900)',
                }}
              >
                Regeneration{' '}
                {state.phase === 'done'
                  ? 'complete'
                  : state.phase === 'partial'
                  ? 'finished with partial results'
                  : 'failed'}
              </div>
              <div
                style={{ fontSize: 12, color: 'var(--ink-500)', marginTop: 2 }}
              >
                You can now compare regenerated content against the original.
              </div>
            </div>
            <button
              className="btn btn-ghost"
              onClick={() => navigate(`/books/${bookId}/regenerate`)}
            >
              <Icon name="regen" size={14} /> Run again
            </button>
            <button
              className="btn btn-primary"
              onClick={() => navigate(`/books/${bookId}/regen-review`)}
            >
              <Icon name="eye" size={14} /> View regenerated content
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Stage row ───────────────────────────────────────────────────────

const STAGE_META: Record<
  RegenStageKey,
  { label: string; icon: 'layers' | 'question' | 'image' }
> = {
  theory: { label: 'Theory', icon: 'layers' },
  questions: { label: 'Questions', icon: 'question' },
  figures: { label: 'Figures', icon: 'image' },
};

function StageRow({
  stageKey,
  stage,
  isFirst,
}: {
  stageKey: RegenStageKey;
  stage: RegenStageState;
  isFirst: boolean;
}) {
  const meta = STAGE_META[stageKey];
  const notEnabled = !stage.enabled;
  const isDone = stage.status === 'done';
  const isFailed = stage.status === 'failed';
  const isRunning = stage.status === 'running';

  const dotBg = notEnabled
    ? 'var(--bg-tint)'
    : isDone
    ? 'var(--success)'
    : isFailed
    ? 'var(--red-600)'
    : isRunning
    ? 'var(--indigo-700)'
    : 'var(--bg-tint)';
  const dotColor = notEnabled ? 'var(--ink-400)' : '#fff';

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '40px 130px 1fr 80px',
        alignItems: 'center',
        gap: 14,
        padding: '14px 22px',
        borderTop: isFirst ? 'none' : '1px solid var(--line-2)',
        opacity: notEnabled ? 0.55 : 1,
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
        }}
      >
        {notEnabled ? (
          '—'
        ) : isDone ? (
          <Icon name="check" size={14} />
        ) : isFailed ? (
          '!'
        ) : isRunning ? (
          <span className="spinner" style={{ width: 10, height: 10, borderWidth: 1.5 }} />
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
          }}
        >
          {meta.label}
        </div>
        <div
          style={{
            fontSize: 11,
            color: notEnabled
              ? 'var(--ink-500)'
              : isFailed
              ? 'var(--red-700)'
              : isDone
              ? 'var(--success)'
              : 'var(--ink-500)',
            marginTop: 2,
          }}
        >
          {notEnabled
            ? 'Skipped'
            : isDone
            ? 'Done'
            : isFailed
            ? 'Failed'
            : isRunning
            ? 'Running'
            : 'Queued'}
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
        {notEnabled ? (
          '—'
        ) : stage.error ? (
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
          color: notEnabled
            ? 'var(--ink-400)'
            : isDone
            ? 'var(--success)'
            : isFailed
            ? 'var(--red-700)'
            : 'var(--ink-700)',
        }}
      >
        {notEnabled ? '—' : `${stage.progress}%`}
      </div>
    </div>
  );
}

function phaseLabel(phase: string): string {
  switch (phase) {
    case 'idle':
      return 'Starting…';
    case 'loading':
      return 'Loading…';
    case 'running':
      return 'Regenerating';
    case 'reconciling':
      return 'Verifying';
    case 'done':
      return 'Complete';
    case 'partial':
      return 'Complete with issues';
    case 'error':
      return 'Failed';
    default:
      return phase;
  }
}
