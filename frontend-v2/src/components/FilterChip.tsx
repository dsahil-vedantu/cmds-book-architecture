type Props = {
  label: string;
  count: number;
  active: boolean;
  onClick: () => void;
};

export function FilterChip({ label, count, active, onClick }: Props) {
  return (
    <button
      onClick={onClick}
      className="btn btn-sm"
      style={{
        background: active ? 'var(--ink-900)' : 'transparent',
        color: active ? '#fff' : 'var(--ink-700)',
        border: active ? '1px solid var(--ink-900)' : '1px solid var(--line)',
      }}
    >
      {label}
      <span
        style={{
          marginLeft: 6,
          padding: '0 6px',
          borderRadius: 6,
          fontSize: 11,
          background: active ? 'rgba(255,255,255,0.16)' : 'var(--bg-tint)',
          color: active ? '#fff' : 'var(--ink-500)',
          fontWeight: 600,
        }}
      >
        {count}
      </span>
    </button>
  );
}
