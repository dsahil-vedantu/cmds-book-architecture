// Throwaway dev page for verifying useExtractionPipeline against a real
// backend. Renders raw state + buttons; no design polish. Delete this
// page (and its route in App.tsx) once the polished ExtractingPanel ships.
//
// Usage:
//   1. Upload a fresh chapter via /upload (gives you a book_id in the URL
//      or via the toast — or pull one from /api/books).
//   2. Open /dev/extract?bookId=<uuid>
//   3. Click "Start pipeline" — watch the state machine + the network tab.

import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { useExtractionPipeline, type StageKey } from '../api/extractionPipeline';

const STAGE_LABELS: Record<StageKey, string> = {
  schema: 'Schema',
  theory: 'Theory',
  questions: 'Questions',
  figures: 'Figures',
};

export default function DevExtractPage() {
  const [search] = useSearchParams();
  const queryBookId = search.get('bookId') ?? '';
  const [bookId, setBookId] = useState(queryBookId);

  const { state, start, retryStage, retrySection, retryAllTheory, cancel } =
    useExtractionPipeline();

  // Auto-fill from query on first render.
  useEffect(() => {
    if (queryBookId && queryBookId !== bookId) setBookId(queryBookId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryBookId]);

  const handleStart = () => {
    const id = bookId.trim();
    if (!id) {
      alert('Paste a book_id first');
      return;
    }
    void start(id);
  };

  return (
    <div
      style={{
        padding: 32,
        fontFamily: 'var(--font-mono)',
        fontSize: 13,
        maxWidth: 980,
        margin: '0 auto',
      }}
    >
      <h1 style={{ fontFamily: 'var(--font-sans)', fontSize: 22, marginBottom: 6 }}>
        useExtractionPipeline — dev harness
      </h1>
      <p style={{ fontFamily: 'var(--font-sans)', color: 'var(--ink-500)', marginTop: 0 }}>
        Raw state + buttons. Verifies parallel firing + polling + reconcile + retry
        against a real backend. Delete this page once the polished UI ships.
      </p>

      <div
        style={{
          display: 'flex',
          gap: 8,
          marginBottom: 18,
          alignItems: 'center',
        }}
      >
        <input
          type="text"
          value={bookId}
          onChange={(e) => setBookId(e.target.value)}
          placeholder="paste book_id UUID here"
          style={{
            flex: 1,
            height: 36,
            padding: '0 12px',
            border: '1px solid var(--line)',
            borderRadius: 8,
            font: 'inherit',
            fontSize: 12.5,
          }}
        />
        <button
          className="btn btn-primary"
          onClick={handleStart}
          disabled={['loading', 'analysing', 'approving', 'extracting', 'reconciling'].includes(state.phase)}
        >
          {['loading', 'analysing', 'approving', 'extracting', 'reconciling'].includes(state.phase)
            ? 'Running…'
            : 'Start pipeline'}
        </button>
        <button className="btn btn-ghost" onClick={cancel}>
          Stop polling
        </button>
      </div>

      <div
        style={{
          marginBottom: 18,
          padding: 14,
          background: 'var(--surface-2)',
          border: '1px solid var(--line)',
          borderRadius: 10,
          display: 'grid',
          gridTemplateColumns: '120px 1fr',
          gap: '6px 18px',
          fontSize: 13,
        }}
      >
        <div style={{ color: 'var(--ink-500)' }}>phase</div>
        <div>
          <strong>{state.phase}</strong>
        </div>
        <div style={{ color: 'var(--ink-500)' }}>bookId</div>
        <div>{state.bookId ?? '—'}</div>
        <div style={{ color: 'var(--ink-500)' }}>overallPct</div>
        <div>
          <strong>{state.overallPct}%</strong>
        </div>
        <div style={{ color: 'var(--ink-500)' }}>errorMessage</div>
        <div style={{ color: state.errorMessage ? 'var(--red-700)' : 'var(--ink-500)' }}>
          {state.errorMessage ?? '—'}
        </div>
      </div>

      <h3 style={{ fontFamily: 'var(--font-sans)', fontSize: 14, margin: '18px 0 8px' }}>
        Stages
      </h3>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {(['schema', 'theory', 'questions', 'figures'] as StageKey[]).map((k) => {
          const st = state[k];
          return (
            <div
              key={k}
              style={{
                padding: '10px 14px',
                background: 'var(--surface)',
                border: '1px solid var(--line)',
                borderRadius: 10,
                display: 'grid',
                gridTemplateColumns: '110px 80px 60px 1fr auto',
                gap: 12,
                alignItems: 'center',
              }}
            >
              <strong style={{ fontFamily: 'var(--font-sans)' }}>{STAGE_LABELS[k]}</strong>
              <span
                style={{
                  fontSize: 11,
                  padding: '2px 8px',
                  borderRadius: 4,
                  background: pillBg(st.status),
                  color: pillFg(st.status),
                }}
              >
                {st.status}
              </span>
              <span>{st.progress}%</span>
              <span
                style={{
                  fontSize: 12,
                  color: 'var(--ink-500)',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {st.error ? (
                  <span style={{ color: 'var(--red-700)' }}>{st.error}</span>
                ) : st.message ? (
                  st.message
                ) : st.jobId ? (
                  `job: ${st.jobId.slice(0, 8)}…`
                ) : (
                  '—'
                )}
              </span>
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => void retryStage(k)}
                disabled={!state.bookId}
              >
                Retry
              </button>
            </div>
          );
        })}
      </div>

      <h3 style={{ fontFamily: 'var(--font-sans)', fontSize: 14, margin: '20px 0 8px' }}>
        Section completeness
      </h3>
      <div
        style={{
          padding: '12px 14px',
          background:
            state.sectionCounts.missing > 0 ||
            state.sectionCounts.failed > 0 ||
            state.sectionCounts.inFlight > 0
              ? 'var(--red-50)'
              : 'var(--success-bg)',
          border:
            '1px solid ' +
            (state.sectionCounts.missing > 0 ||
            state.sectionCounts.failed > 0 ||
            state.sectionCounts.inFlight > 0
              ? 'var(--red-100)'
              : '#A8DCC4'),
          borderRadius: 10,
          display: 'grid',
          gridTemplateColumns: 'repeat(5, 1fr)',
          gap: 12,
          fontSize: 12,
          fontFamily: 'var(--font-sans)',
        }}
      >
        <div>
          <div style={{ color: 'var(--ink-500)', fontSize: 10.5, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
            Expected
          </div>
          <div className="mono" style={{ fontSize: 20, fontWeight: 800 }}>
            {state.sectionCounts.expected}
          </div>
        </div>
        <div>
          <div style={{ color: 'var(--ink-500)', fontSize: 10.5, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
            Ready
          </div>
          <div
            className="mono"
            style={{ fontSize: 20, fontWeight: 800, color: '#0B6A4F' }}
          >
            {state.sectionCounts.ready}
          </div>
        </div>
        <div>
          <div style={{ color: 'var(--ink-500)', fontSize: 10.5, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
            Failed
          </div>
          <div
            className="mono"
            style={{
              fontSize: 20,
              fontWeight: 800,
              color: state.sectionCounts.failed > 0 ? 'var(--red-700)' : 'var(--ink-400)',
            }}
          >
            {state.sectionCounts.failed}
          </div>
        </div>
        <div>
          <div style={{ color: 'var(--ink-500)', fontSize: 10.5, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
            In-flight
          </div>
          <div
            className="mono"
            style={{
              fontSize: 20,
              fontWeight: 800,
              color:
                state.sectionCounts.inFlight > 0 ? '#8A5300' : 'var(--ink-400)',
            }}
          >
            {state.sectionCounts.inFlight}
          </div>
        </div>
        <div>
          <div style={{ color: 'var(--ink-500)', fontSize: 10.5, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
            Missing
          </div>
          <div
            className="mono"
            style={{
              fontSize: 20,
              fontWeight: 800,
              color:
                state.sectionCounts.missing > 0 ? 'var(--red-700)' : 'var(--ink-400)',
            }}
          >
            {state.sectionCounts.missing}
          </div>
        </div>
      </div>

      {state.missingSections.length > 0 && (
        <>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', margin: '20px 0 8px' }}>
            <h3 style={{ fontFamily: 'var(--font-sans)', fontSize: 14, margin: 0 }}>
              Missing sections ({state.missingSections.length}) — never created
            </h3>
            <button className="btn btn-accent btn-sm" onClick={() => void retryAllTheory()}>
              Re-run theory for all
            </button>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {state.missingSections.slice(0, 20).map((ms) => (
              <div
                key={ms.section_id}
                style={{
                  padding: '8px 14px',
                  background: 'var(--warning-bg)',
                  border: '1px solid #F5E2BD',
                  borderRadius: 10,
                  display: 'grid',
                  gridTemplateColumns: '160px 1fr',
                  gap: 12,
                  fontSize: 12,
                }}
              >
                <span className="mono">{ms.section_id}</span>
                <span style={{ fontFamily: 'var(--font-sans)' }}>{ms.title}</span>
              </div>
            ))}
            {state.missingSections.length > 20 && (
              <div style={{ fontSize: 12, color: 'var(--ink-500)' }}>
                …and {state.missingSections.length - 20} more
              </div>
            )}
          </div>
        </>
      )}

      {state.failedSections.length > 0 && (
        <>
          <h3 style={{ fontFamily: 'var(--font-sans)', fontSize: 14, margin: '20px 0 8px' }}>
            Failed sections ({state.failedSections.length})
          </h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {state.failedSections.map((fs) => (
              <div
                key={fs.id}
                style={{
                  padding: '8px 14px',
                  background: 'var(--red-50)',
                  border: '1px solid var(--red-100)',
                  borderRadius: 10,
                  display: 'grid',
                  gridTemplateColumns: '160px 1fr 60px auto',
                  gap: 12,
                  alignItems: 'center',
                  fontSize: 12,
                }}
              >
                <span className="mono">{fs.section_id}</span>
                <span style={{ fontFamily: 'var(--font-sans)' }}>{fs.title}</span>
                <span style={{ color: 'var(--ink-500)' }}>{fs.attempts} att</span>
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={() => void retrySection(fs.id)}
                >
                  Retry
                </button>
              </div>
            ))}
          </div>
        </>
      )}

      <h3 style={{ fontFamily: 'var(--font-sans)', fontSize: 14, margin: '20px 0 8px' }}>
        Raw state JSON
      </h3>
      <pre
        style={{
          padding: 14,
          background: 'var(--ink-900)',
          color: '#A7F3D0',
          borderRadius: 10,
          fontSize: 11,
          lineHeight: 1.5,
          overflow: 'auto',
          maxHeight: 360,
        }}
      >
        {JSON.stringify(state, null, 2)}
      </pre>
    </div>
  );
}

function pillBg(s: string) {
  if (s === 'done') return 'var(--success-bg)';
  if (s === 'failed') return 'var(--red-50)';
  if (s === 'running') return 'var(--info-bg)';
  if (s === 'queued') return 'var(--warning-bg)';
  return 'var(--bg-tint)';
}

function pillFg(s: string) {
  if (s === 'done') return '#0B6A4F';
  if (s === 'failed') return 'var(--red-700)';
  if (s === 'running') return '#1853AC';
  if (s === 'queued') return '#8A5300';
  return 'var(--ink-500)';
}
