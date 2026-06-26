import { useState } from 'react';

import { Icon } from './Icon';
import { PIPELINE_STAGES } from '../mocks/chapters';

type Props = { progress: number; stage?: string };

export function SmartProgress({ progress, stage }: Props) {
  const [expanded, setExpanded] = useState(false);
  const stages = PIPELINE_STAGES;
  const curIdx = Math.min(stages.length - 1, Math.floor(progress / (100 / stages.length)));

  return (
    <div>
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          justifyContent: 'space-between',
          marginBottom: 10,
        }}
      >
        <div>
          <div
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 8,
              fontSize: 12,
              fontWeight: 700,
              color: 'var(--indigo-700)',
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
            }}
          >
            <span
              className="spinner dark"
              style={{ width: 11, height: 11, borderWidth: 1.5 }}
            />{' '}
            Processing
          </div>
          {stage && (
            <div
              style={{
                fontSize: 14,
                fontWeight: 600,
                color: 'var(--ink-900)',
                marginTop: 4,
              }}
            >
              {stage}
            </div>
          )}
        </div>
        <div style={{ textAlign: 'right' }}>
          <div
            className="mono"
            style={{
              fontSize: 26,
              fontWeight: 800,
              color: 'var(--ink-900)',
              letterSpacing: '-0.02em',
              lineHeight: 1,
            }}
          >
            {progress}
            <span style={{ fontSize: 14, color: 'var(--ink-500)', fontWeight: 600 }}>%</span>
          </div>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => setExpanded((v) => !v)}
            style={{ marginTop: 4, height: 24, padding: '0 8px', fontSize: 11 }}
          >
            {expanded ? 'Hide stages' : 'Show stages'}{' '}
            <Icon name="chevron" size={11} className={expanded ? 'chev open' : 'chev'} />
          </button>
        </div>
      </div>
      <div className="progress-rail" style={{ height: 10 }}>
        <div className="progress-fill" style={{ width: `${progress}%` }} />
      </div>
      {expanded && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: `repeat(${stages.length}, 1fr)`,
            marginTop: 14,
            gap: 6,
          }}
        >
          {stages.map((s, i) => {
            const state = i < curIdx ? 'done' : i === curIdx ? 'now' : 'next';
            return (
              <div
                key={s.id}
                style={{
                  padding: '8px 10px',
                  borderRadius: 8,
                  background:
                    state === 'done'
                      ? 'var(--success-bg)'
                      : state === 'now'
                      ? 'var(--indigo-50)'
                      : 'var(--bg-tint)',
                  color:
                    state === 'done'
                      ? '#0B6A4F'
                      : state === 'now'
                      ? 'var(--indigo-700)'
                      : 'var(--ink-500)',
                }}
              >
                <div style={{ fontSize: 10, fontFamily: 'var(--font-mono)', opacity: 0.7 }}>
                  {String(i + 1).padStart(2, '0')}
                </div>
                <div style={{ fontSize: 11.5, fontWeight: 600, marginTop: 2 }}>{s.label}</div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
