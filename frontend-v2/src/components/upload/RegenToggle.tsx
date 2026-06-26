import type { ReactNode } from 'react';

import { Icon, type IconName } from '../Icon';

type Props = {
  on: boolean;
  onToggle: () => void;
  icon: IconName;
  label: string;
  sub: string;
  meta?: ReactNode;
};

export function RegenToggle({ on, onToggle, icon, label, sub, meta }: Props) {
  return (
    <div
      style={{
        border: on ? '1.5px solid var(--indigo-700)' : '1.5px solid var(--line)',
        background: on ? 'var(--indigo-50)' : 'var(--surface-2)',
        borderRadius: 12,
        padding: 14,
        transition: 'all 160ms',
        boxShadow: on ? '0 0 0 4px rgba(26,35,126,0.06)' : 'none',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
        <div
          style={{
            width: 30,
            height: 30,
            borderRadius: 8,
            background: on ? 'var(--indigo-700)' : 'var(--bg-tint)',
            color: on ? '#fff' : 'var(--ink-500)',
            display: 'grid',
            placeItems: 'center',
            transition: 'all 160ms',
          }}
        >
          <Icon name={icon} size={15} />
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--ink-900)' }}>{label}</div>
          <div style={{ fontSize: 11, color: 'var(--ink-500)' }}>{sub}</div>
        </div>
        <div className={`toggle ${on ? 'on' : ''}`} onClick={onToggle} />
      </div>
      {on && meta && (
        <div
          style={{
            marginTop: 12,
            paddingTop: 12,
            borderTop: '1px solid var(--indigo-100)',
          }}
        >
          {meta}
        </div>
      )}
    </div>
  );
}
