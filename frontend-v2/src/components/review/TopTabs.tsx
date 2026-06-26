// Top-level Theory / Questions / Figures tabs.
//
// Each tab carries its own counts so the user can see at a glance what's
// been extracted. Counts come from the parent (already-fetched lists),
// not re-fetched here.

import { Icon } from '../Icon';
import type { TabKey } from '../../api/sections';

type Tab = { key: TabKey; label: string; icon: 'layers' | 'question' | 'image'; count?: number };

type Props = {
  active: TabKey;
  onChange: (k: TabKey) => void;
  counts: { theory: number; questions: number; figures: number };
};

export function TopTabs({ active, onChange, counts }: Props) {
  const tabs: Tab[] = [
    { key: 'theory',    label: 'Theory',    icon: 'layers',   count: counts.theory },
    { key: 'questions', label: 'Questions', icon: 'question', count: counts.questions },
    { key: 'figures',   label: 'Figures',   icon: 'image',    count: counts.figures },
  ];

  return (
    <div
      style={{
        display: 'flex',
        gap: 4,
        borderBottom: '1px solid var(--line)',
        padding: '0 28px',
        background: 'var(--surface)',
      }}
    >
      {tabs.map((t) => {
        const isActive = active === t.key;
        return (
          <button
            key={t.key}
            onClick={() => onChange(t.key)}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 8,
              padding: '14px 18px',
              border: 'none',
              borderBottom: isActive ? '2px solid var(--indigo-700)' : '2px solid transparent',
              background: 'transparent',
              color: isActive ? 'var(--indigo-700)' : 'var(--ink-500)',
              cursor: 'pointer',
              fontSize: 14,
              fontWeight: isActive ? 700 : 500,
              letterSpacing: '-0.005em',
              marginBottom: -1,
              transition: 'color 120ms, border-color 120ms',
            }}
          >
            <Icon name={t.icon} size={15} />
            <span>{t.label}</span>
            {t.count != null && (
              <span
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  padding: '1px 7px',
                  borderRadius: 999,
                  background: isActive ? 'var(--indigo-50)' : 'var(--bg-tint)',
                  color: isActive ? 'var(--indigo-700)' : 'var(--ink-500)',
                  fontVariantNumeric: 'tabular-nums',
                }}
              >
                {t.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
