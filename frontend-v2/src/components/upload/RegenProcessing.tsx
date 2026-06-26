import { useEffect, useState } from 'react';

import { Icon } from '../Icon';
import { Stepper } from './Stepper';
import { REGEN_STAGES } from '../../mocks/upload';

type Props = {
  name: string;
  onDone: () => void;
};

export function RegenProcessing({ name, onDone }: Props) {
  const [pct, setPct] = useState(2);
  const [stageIdx, setStageIdx] = useState(0);
  const stages = REGEN_STAGES;

  useEffect(() => {
    let cancelled = false;
    const t = window.setInterval(() => {
      if (cancelled) return;
      setPct((p) => {
        const next = p + (Math.random() * 3 + 1.5);
        setStageIdx(
          Math.min(stages.length - 1, Math.floor(next / (100 / stages.length)))
        );
        if (next >= 100) {
          window.clearInterval(t);
          window.setTimeout(onDone, 700);
          return 100;
        }
        return next;
      });
    }, 420);
    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
  }, [onDone, stages.length]);

  const current = stages[stageIdx];

  return (
    <div className="content fade-up">
      <div className="content-narrow" style={{ maxWidth: 760 }}>
        <Stepper step={3} />
        <div style={{ textAlign: 'center', marginTop: 24, marginBottom: 32 }}>
          <div
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 8,
              padding: '5px 12px',
              borderRadius: 999,
              background: 'var(--indigo-50)',
              color: 'var(--indigo-700)',
              fontSize: 12,
              fontWeight: 700,
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
            }}
          >
            <span
              className="spinner dark"
              style={{ width: 10, height: 10, borderWidth: 1.5 }}
            />{' '}
            Regenerating
          </div>
          <h1
            style={{
              fontSize: 32,
              fontWeight: 800,
              letterSpacing: '-0.025em',
              color: 'var(--ink-900)',
              marginTop: 14,
              marginBottom: 6,
            }}
          >
            {name || 'Your book'}
          </h1>
          <div style={{ color: 'var(--ink-500)', fontSize: 14 }}>
            Theory, questions and figures are being rewritten chapter by chapter. We'll notify
            you when each is ready to review.
          </div>
        </div>

        <div className="card" style={{ padding: 28 }}>
          <div
            style={{
              display: 'flex',
              alignItems: 'baseline',
              justifyContent: 'space-between',
              marginBottom: 16,
            }}
          >
            <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--ink-900)' }}>
              {current.label}
            </div>
            <div
              className="mono"
              style={{
                fontSize: 22,
                fontWeight: 800,
                color: 'var(--ink-900)',
                letterSpacing: '-0.02em',
              }}
            >
              {Math.round(pct)}
              <span style={{ fontSize: 14, color: 'var(--ink-500)', fontWeight: 600 }}>%</span>
            </div>
          </div>
          <div className="progress-rail" style={{ height: 10 }}>
            <div className="progress-fill" style={{ width: `${pct}%` }} />
          </div>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              marginTop: 14,
              color: 'var(--ink-500)',
              fontSize: 12.5,
            }}
          >
            <span>
              Step {stageIdx + 1} of {stages.length}
            </span>
            <span>Extraction done · regenerating now</span>
          </div>

          <div
            style={{
              marginTop: 22,
              paddingTop: 18,
              borderTop: '1px solid var(--line)',
            }}
          >
            {stages.map((s, i) => {
              const state = i < stageIdx ? 'done' : i === stageIdx ? 'now' : 'next';
              return (
                <div
                  key={s.id}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 12,
                    padding: '8px 0',
                  }}
                >
                  <div
                    style={{
                      width: 22,
                      height: 22,
                      borderRadius: '50%',
                      background:
                        state === 'done'
                          ? 'var(--success)'
                          : state === 'now'
                          ? 'var(--indigo-700)'
                          : 'var(--bg-tint)',
                      color: state === 'next' ? 'var(--ink-400)' : '#fff',
                      display: 'grid',
                      placeItems: 'center',
                      fontSize: 11,
                      fontWeight: 700,
                    }}
                  >
                    {state === 'done' ? (
                      <Icon name="check" size={12} />
                    ) : state === 'now' ? (
                      <span
                        className="spinner"
                        style={{ width: 10, height: 10, borderWidth: 1.5 }}
                      />
                    ) : (
                      i + 1
                    )}
                  </div>
                  <div
                    style={{
                      flex: 1,
                      fontSize: 13.5,
                      color: state === 'next' ? 'var(--ink-500)' : 'var(--ink-900)',
                      fontWeight: state === 'now' ? 600 : 500,
                    }}
                  >
                    {s.label}
                  </div>
                  <div
                    className="mono"
                    style={{ fontSize: 10.5, color: 'var(--ink-400)' }}
                  >
                    {s.mono}
                  </div>
                  {state === 'done' && (
                    <span className="badge ok" style={{ fontSize: 10 }}>
                      OK
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        <div style={{ textAlign: 'center', marginTop: 22 }}>
          <button className="btn btn-ghost btn-sm" onClick={onDone}>
            Run in background <Icon name="arrow-r" size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}
