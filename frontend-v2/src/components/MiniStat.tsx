export function MiniStat({
  label,
  value,
  sub,
}: {
  label: string;
  value: string | number;
  sub?: string;
}) {
  return (
    <div>
      <div
        style={{
          fontSize: 11,
          fontWeight: 600,
          letterSpacing: '0.08em',
          textTransform: 'uppercase',
          color: 'var(--ink-500)',
        }}
      >
        {label}
      </div>
      <div
        className="mono"
        style={{
          fontSize: 22,
          fontWeight: 800,
          color: 'var(--ink-900)',
          letterSpacing: '-0.02em',
          fontFamily: 'var(--font-display)',
          marginTop: 2,
        }}
      >
        {value}
      </div>
      {sub && <div style={{ fontSize: 11, color: 'var(--ink-500)' }}>{sub}</div>}
    </div>
  );
}
