import { useState } from 'react';

import { Icon } from './Icon';
import type { DemoQuestion } from '../mocks/chapterContent';

type Props = { q: DemoQuestion; regen: boolean };

export function QuestionCard({ q, regen }: Props) {
  const [open, setOpen] = useState(false);
  const diffColor = q.diff === 'Easy' ? 'ok' : q.diff === 'Medium' ? 'warn' : 'regen';

  return (
    <div className="qcard">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
        <div className="qnum">Q{String(q.n).padStart(2, '0')}</div>
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            marginBottom: 6,
            flexWrap: 'wrap',
          }}
        >
          <span className="badge">{q.type}</span>
          <span className={`badge ${diffColor}`}>
            <span className="dot" />
            {q.diff}
          </span>
          {regen && q.variants && (
            <span className="badge regen">
              <Icon name="sparkles" size={11} /> {q.variants.length} variant
              {q.variants.length > 1 ? 's' : ''}
            </span>
          )}
        </div>
        <div
          style={{
            fontSize: 14.5,
            color: 'var(--ink-900)',
            lineHeight: 1.55,
            whiteSpace: 'pre-wrap',
          }}
        >
          {q.q}
        </div>

        {q.options && q.correct != null && (
          <div
            style={{
              marginTop: 12,
              display: 'grid',
              gridTemplateColumns: '1fr 1fr',
              gap: 6,
            }}
          >
            {q.options.map((o, i) => (
              <div
                key={i}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  padding: '8px 12px',
                  border: '1px solid var(--line)',
                  borderRadius: 8,
                  background: i === q.correct ? 'var(--success-bg)' : 'var(--surface-2)',
                  fontSize: 13,
                }}
              >
                <div
                  style={{
                    width: 18,
                    height: 18,
                    borderRadius: '50%',
                    border: i === q.correct ? 'none' : '1.5px solid var(--ink-300)',
                    background: i === q.correct ? 'var(--success)' : 'transparent',
                    color: '#fff',
                    display: 'grid',
                    placeItems: 'center',
                    fontSize: 10,
                  }}
                >
                  {i === q.correct && <Icon name="check" size={11} />}
                </div>
                <span
                  style={{
                    color: i === q.correct ? '#0B6A4F' : 'var(--ink-800)',
                    fontWeight: i === q.correct ? 600 : 400,
                  }}
                >
                  {String.fromCharCode(65 + i)}. {o}
                </span>
              </div>
            ))}
          </div>
        )}

        {regen && q.variants && open && (
          <div
            style={{
              marginTop: 12,
              paddingTop: 12,
              borderTop: '1px dashed var(--line)',
            }}
          >
            <div
              style={{
                fontSize: 11,
                fontWeight: 700,
                letterSpacing: '0.08em',
                textTransform: 'uppercase',
                color: 'var(--ink-500)',
                marginBottom: 8,
              }}
            >
              AI variants
            </div>
            {q.variants.map((v, i) => (
              <div
                key={i}
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 10,
                  padding: '8px 0',
                }}
              >
                <div
                  className="qnum"
                  style={{ background: 'var(--red-50)', color: 'var(--red-700)' }}
                >
                  V{i + 1}
                </div>
                <div style={{ fontSize: 13.5, color: 'var(--ink-800)', lineHeight: 1.55 }}>{v}</div>
              </div>
            ))}
          </div>
        )}

        {q.answer && (
          <div style={{ marginTop: 10, fontSize: 12.5, color: 'var(--ink-500)' }}>
            <strong style={{ color: 'var(--ink-700)' }}>Answer:</strong> {q.answer}
          </div>
        )}

        <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
          {regen && q.variants && (
            <button className="btn btn-ghost btn-sm" onClick={() => setOpen((v) => !v)}>
              {open
                ? 'Hide variants'
                : `Show ${q.variants.length} variant${q.variants.length > 1 ? 's' : ''}`}
            </button>
          )}
          <button className="btn btn-ghost btn-sm">
            <Icon name="regen" size={12} /> Regenerate
          </button>
          <button className="btn btn-ghost btn-sm">
            <Icon name="eye" size={12} /> View original
          </button>
        </div>
      </div>
    </div>
  );
}
