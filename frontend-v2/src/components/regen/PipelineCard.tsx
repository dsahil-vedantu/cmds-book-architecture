// Shared pipeline-card shell used by Theory / Questions / Figures
// regen param panels. Provides:
//   • master ON/OFF toggle
//   • icon + label + section count
//   • collapsible body for params (hidden when off)

import type { ReactNode } from 'react';

import { Icon, type IconName } from '../Icon';

type Props = {
  icon: IconName;
  label: string;
  description: string;
  count: number;
  countLabel: string;
  on: boolean;
  onToggle: () => void;
  children: ReactNode;
};

export function PipelineCard({
  icon,
  label,
  description,
  count,
  countLabel,
  on,
  onToggle,
  children,
}: Props) {
  return (
    <div
      className="card"
      style={{
        padding: 0,
        overflow: 'hidden',
        border: on ? '1.5px solid var(--indigo-700)' : '1px solid var(--line)',
        boxShadow: on ? '0 0 0 4px rgba(26,35,126,0.06)' : 'var(--sh-1)',
        transition: 'all 160ms',
      }}
    >
      <div
        style={{
          padding: '18px 22px',
          display: 'flex',
          alignItems: 'center',
          gap: 14,
          borderBottom: on ? '1px solid var(--indigo-100)' : 'none',
          background: on ? 'var(--indigo-50)' : 'var(--surface)',
        }}
      >
        <div
          style={{
            width: 40,
            height: 40,
            borderRadius: 10,
            background: on ? 'var(--indigo-700)' : 'var(--bg-tint)',
            color: on ? '#fff' : 'var(--ink-500)',
            display: 'grid',
            placeItems: 'center',
            transition: 'all 160ms',
          }}
        >
          <Icon name={icon} size={18} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              display: 'flex',
              alignItems: 'baseline',
              gap: 10,
            }}
          >
            <div
              style={{
                fontSize: 16,
                fontWeight: 800,
                color: 'var(--ink-900)',
                letterSpacing: '-0.01em',
              }}
            >
              {label}
            </div>
            <span
              className="kbd"
              style={{ fontSize: 11 }}
            >
              {count} {countLabel}
            </span>
          </div>
          <div style={{ fontSize: 12.5, color: 'var(--ink-500)', marginTop: 2 }}>
            {description}
          </div>
        </div>
        <div
          className={`toggle ${on ? 'on' : ''}`}
          onClick={onToggle}
          title={on ? 'Disable this pipeline' : 'Enable this pipeline'}
        />
      </div>
      {on && (
        <div style={{ padding: '18px 22px', background: 'var(--surface)' }}>
          {children}
        </div>
      )}
    </div>
  );
}

// ─── Param helpers (used by all 3 param cards) ────────────────────────

export function ParamLabel({
  children,
  hint,
}: {
  children: ReactNode;
  hint?: string;
}) {
  return (
    <div style={{ marginBottom: 8 }}>
      <div
        style={{
          fontSize: 11.5,
          fontWeight: 700,
          letterSpacing: '0.06em',
          textTransform: 'uppercase',
          color: 'var(--ink-700)',
        }}
      >
        {children}
      </div>
      {hint && (
        <div
          style={{
            fontSize: 11.5,
            color: 'var(--ink-500)',
            marginTop: 2,
            lineHeight: 1.4,
          }}
        >
          {hint}
        </div>
      )}
    </div>
  );
}

export function ParamRow({ children }: { children: ReactNode }) {
  return <div style={{ marginBottom: 18 }}>{children}</div>;
}

export function SegmentChoice<T extends string | number>({
  options,
  value,
  onChange,
  format,
}: {
  options: readonly T[];
  value: T;
  onChange: (v: T) => void;
  format?: (v: T) => string;
}) {
  return (
    <div
      style={{
        display: 'inline-flex',
        gap: 0,
        padding: 3,
        background: 'var(--bg-tint)',
        borderRadius: 8,
      }}
    >
      {options.map((opt) => {
        const active = opt === value;
        return (
          <button
            key={String(opt)}
            type="button"
            onClick={() => onChange(opt)}
            style={{
              padding: '6px 14px',
              fontSize: 12.5,
              fontWeight: active ? 700 : 500,
              border: 'none',
              borderRadius: 6,
              background: active ? '#fff' : 'transparent',
              color: active ? 'var(--indigo-700)' : 'var(--ink-700)',
              cursor: 'pointer',
              boxShadow: active ? 'var(--sh-1)' : 'none',
              textTransform: 'capitalize',
              transition: 'all 120ms',
            }}
          >
            {format ? format(opt) : String(opt)}
          </button>
        );
      })}
    </div>
  );
}

export function ParamTextarea({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <textarea
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      rows={3}
      style={{
        width: '100%',
        padding: '10px 12px',
        border: '1px solid var(--line)',
        borderRadius: 10,
        font: 'inherit',
        fontSize: 13,
        color: 'var(--ink-900)',
        resize: 'vertical',
        minHeight: 60,
        outline: 'none',
      }}
    />
  );
}
