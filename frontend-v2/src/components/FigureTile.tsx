import { Icon } from './Icon';

type Props = { idx: number; regen: boolean };

export function FigureTile({ idx, regen }: Props) {
  const seed = idx;
  return (
    <div className="card" style={{ overflow: 'hidden', padding: 0 }}>
      <div
        style={{
          height: 130,
          background: 'var(--surface-2)',
          position: 'relative',
          borderBottom: '1px solid var(--line)',
        }}
      >
        <svg viewBox="0 0 200 130" width="100%" height="100%" style={{ display: 'block' }}>
          <defs>
            <pattern id={`g${idx}`} width="20" height="20" patternUnits="userSpaceOnUse">
              <path d="M 20 0 L 0 0 0 20" fill="none" stroke="#E5E8F5" strokeWidth="0.5" />
            </pattern>
          </defs>
          <rect width="200" height="130" fill={`url(#g${idx})`} />
          <line x1="0" y1="80" x2="200" y2="80" stroke="#94A3B8" strokeWidth="0.6" />
          <line x1="100" y1="0" x2="100" y2="130" stroke="#94A3B8" strokeWidth="0.6" />
          <path
            d={`M 30 ${20 + seed * 5} Q 100 ${130 + seed * 3}, 170 ${20 + seed * 5}`}
            fill="none"
            stroke="#1A237E"
            strokeWidth="2"
          />
          <circle cx="60" cy="80" r="3" fill="#E94B35" />
          <circle cx="140" cy="80" r="3" fill="#E94B35" />
        </svg>
        {regen && (
          <span
            className="badge regen"
            style={{ position: 'absolute', top: 8, right: 8, fontSize: 10 }}
          >
            <Icon name="sparkles" size={10} /> AI
          </span>
        )}
      </div>
      <div style={{ padding: '10px 12px' }}>
        <div style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--ink-900)' }}>
          Fig {idx + 1}: Parabola of y = ax² + bx + c
        </div>
        <div style={{ fontSize: 11, color: 'var(--ink-500)', marginTop: 2 }}>
          Referenced in §3.{(idx % 3) + 1}
        </div>
      </div>
    </div>
  );
}
