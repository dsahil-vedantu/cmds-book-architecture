import { Fragment } from 'react';

import { Icon } from '../Icon';

// 3 visible steps end-to-end. Phase A handles steps 1 + 2. Step 3
// (Regenerate) is a separate page reached from the Review screen; it
// shows here so the user sees the full journey ahead.
const ITEMS = [
  { n: 1, label: 'Upload' },
  { n: 2, label: 'Extract' },
  { n: 3, label: 'Regenerate' },
] as const;

export function Stepper({ step }: { step: 1 | 2 | 3 }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 0, marginBottom: 22 }}>
      {ITEMS.map((it, i) => {
        const active = step === it.n;
        const done = step > it.n;
        return (
          <Fragment key={it.n}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div
                style={{
                  width: 32,
                  height: 32,
                  borderRadius: '50%',
                  background: done
                    ? 'var(--success)'
                    : active
                    ? 'var(--indigo-700)'
                    : 'var(--surface)',
                  color: done || active ? '#fff' : 'var(--ink-400)',
                  border: active || done ? 'none' : '1px solid var(--line)',
                  display: 'grid',
                  placeItems: 'center',
                  fontWeight: 700,
                  fontSize: 13,
                  boxShadow: active ? '0 6px 16px -6px rgba(26,35,126,0.45)' : 'none',
                  transition: 'all 200ms',
                }}
              >
                {done ? <Icon name="check" size={14} /> : it.n}
              </div>
              <div
                style={{
                  fontSize: 13.5,
                  fontWeight: 600,
                  color: active
                    ? 'var(--ink-900)'
                    : done
                    ? 'var(--success)'
                    : 'var(--ink-500)',
                }}
              >
                {it.label}
              </div>
            </div>
            {i < ITEMS.length - 1 && (
              <div
                style={{
                  flex: 1,
                  height: 1,
                  background: done ? 'var(--success)' : 'var(--line)',
                  margin: '0 14px',
                  transition: 'background 200ms',
                }}
              />
            )}
          </Fragment>
        );
      })}
    </div>
  );
}
