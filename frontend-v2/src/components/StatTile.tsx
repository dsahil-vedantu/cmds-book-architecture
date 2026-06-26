type Props = {
  label: string;
  value: string | number;
  sub?: string;
  accent?: boolean;
};

export function StatTile({ label, value, sub, accent }: Props) {
  const accentTile = accent
    ? {
        background: 'linear-gradient(135deg, #1A237E, #2B3492)',
        borderColor: 'var(--indigo-700)',
        color: '#fff',
      }
    : undefined;

  return (
    <div className="stat" style={accentTile}>
      <div className="lbl" style={accent ? { color: 'rgba(255,255,255,0.7)' } : undefined}>
        {label}
      </div>
      <div className="val" style={accent ? { color: '#fff' } : undefined}>
        {value}
      </div>
      {sub && (
        <div className="sub" style={accent ? { color: '#FFC7BB' } : undefined}>
          {sub}
        </div>
      )}
    </div>
  );
}
