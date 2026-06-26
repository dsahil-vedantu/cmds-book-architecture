// Inline sub-tab strip used inside the Review page (Regenerated / Original
// / Compare). Sits right below the top tabs.

import { Icon } from '../Icon';

export type ViewVariant = 'regen' | 'original' | 'compare';

type Props = {
  active: ViewVariant;
  hasRegen: boolean;
  onChange: (v: ViewVariant) => void;
};

const TABS: Array<{ key: ViewVariant; label: string; needsRegen: boolean }> = [
  { key: 'regen', label: 'Regenerated', needsRegen: true },
  { key: 'original', label: 'Original', needsRegen: false },
  { key: 'compare', label: 'Compare', needsRegen: true },
];

export function SubTabs({ active, hasRegen, onChange }: Props) {
  return (
    <div
      style={{
        display: 'flex',
        gap: 4,
        padding: '10px 28px',
        borderBottom: '1px solid var(--line)',
        background: 'var(--surface-2)',
        alignItems: 'center',
      }}
    >
      {TABS.map((t) => {
        const disabled = t.needsRegen && !hasRegen;
        const isActive = active === t.key;
        return (
          <button
            key={t.key}
            disabled={disabled}
            onClick={() => onChange(t.key)}
            title={disabled ? 'No regenerated version yet' : undefined}
            style={{
              padding: '6px 12px',
              fontSize: 12.5,
              fontWeight: isActive ? 700 : 600,
              border: '1px solid',
              borderColor: isActive ? 'var(--indigo-700)' : 'var(--line)',
              borderRadius: 8,
              background: isActive ? 'var(--indigo-50)' : 'var(--surface)',
              color: disabled
                ? 'var(--ink-400)'
                : isActive
                ? 'var(--indigo-700)'
                : 'var(--ink-800)',
              cursor: disabled ? 'not-allowed' : 'pointer',
              opacity: disabled ? 0.6 : 1,
            }}
          >
            {t.key === 'regen' && <Icon name="sparkles" size={12} />}{' '}
            {t.label}
          </button>
        );
      })}
      <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--ink-500)' }}>
        {hasRegen ? 'Regenerated version available' : 'No regeneration yet'}
      </span>
    </div>
  );
}
