import { Icon } from './Icon';
import type { Folder } from '../api/folders';

type Props = { folder: Folder; onOpen: (id: string) => void };

// Medium-saturation gradients — colorful but airy. Keep this map in sync
// with the backend PALETTE in backend/app/api/folders.py — adding a hex
// there without a matching gradient here falls back to a flat tinted
// gradient (still functional, but less polished).
const GRADIENTS: Record<string, string> = {
  // Originals (8)
  '#1A237E': 'linear-gradient(135deg, #5C6BC0 0%, #7986CB 100%)', // indigo
  '#E94B35': 'linear-gradient(135deg, #FF7A60 0%, #FFA48F 100%)', // red-orange
  '#10B981': 'linear-gradient(135deg, #34D399 0%, #6EE7B7 100%)', // green
  '#8B5CF6': 'linear-gradient(135deg, #A78BFA 0%, #C4B5FD 100%)', // violet
  '#F59E0B': 'linear-gradient(135deg, #FBBF24 0%, #FCD34D 100%)', // amber
  '#0E7C6B': 'linear-gradient(135deg, #2DD4BF 0%, #5EEAD4 100%)', // teal
  '#3B82F6': 'linear-gradient(135deg, #60A5FA 0%, #93C5FD 100%)', // blue
  '#EC4899': 'linear-gradient(135deg, #F472B6 0%, #F9A8D4 100%)', // pink
  // Alternates (8)
  '#06B6D4': 'linear-gradient(135deg, #22D3EE 0%, #67E8F7 100%)', // cyan
  '#F43F5E': 'linear-gradient(135deg, #FB7185 0%, #FDA4AF 100%)', // rose
  '#84CC16': 'linear-gradient(135deg, #A3E635 0%, #D9F99D 100%)', // lime
  '#D946EF': 'linear-gradient(135deg, #E879F9 0%, #F0ABFC 100%)', // magenta
  '#FB923C': 'linear-gradient(135deg, #FB923C 0%, #FDBA74 100%)', // orange
  '#64748B': 'linear-gradient(135deg, #94A3B8 0%, #CBD5E1 100%)', // slate
  '#EAB308': 'linear-gradient(135deg, #FACC15 0%, #FDE047 100%)', // mustard
  '#A78471': 'linear-gradient(135deg, #B79683 0%, #D6C2AE 100%)', // mocha
};

function gradientFor(color: string): string {
  return GRADIENTS[color] ?? `linear-gradient(135deg, ${color} 0%, ${color}aa 100%)`;
}

// Pale / yellow / pastel colors don't have enough contrast for white text
// — flip those to dark ink for legibility.
const DARK_TEXT_COLORS = new Set([
  '#F59E0B', // amber
  '#84CC16', // lime
  '#EAB308', // mustard
]);
const headerTextColor = (color: string) =>
  DARK_TEXT_COLORS.has(color) ? 'var(--ink-900)' : '#fff';

function humanize(iso: string): string {
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return iso;
  const days = Math.floor((Date.now() - then) / 86_400_000);
  if (days <= 0) return 'today';
  if (days === 1) return '1 day ago';
  if (days < 30) return `${days} days ago`;
  return new Date(iso).toLocaleDateString();
}

function pickStatus(f: Folder): { label: string; cls: 'ok' | 'info' | 'idle' } {
  if (f.chapters_processing > 0) return { label: 'Processing', cls: 'info' };
  if (f.chapters_ready === f.chapters && f.chapters > 0)
    return { label: 'Ready', cls: 'ok' };
  if (f.chapters_queued > 0) return { label: 'Queued', cls: 'idle' };
  return { label: 'Empty', cls: 'idle' };
}

export function FolderCard({ folder: f, onOpen }: Props) {
  const status = pickStatus(f);
  const grad = gradientFor(f.color);
  const textColor = headerTextColor(f.color);
  const isDarkText = textColor !== '#fff';

  return (
    <div
      className="card card-hover"
      onClick={() => onOpen(f.id)}
      style={{
        padding: 0,
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
        borderRadius: 14,
        position: 'relative',
      }}
    >
      {/* Colorful header */}
      <div
        style={{
          position: 'relative',
          minHeight: 116,
          background: grad,
          color: textColor,
          padding: '14px 16px 16px',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'space-between',
          overflow: 'hidden',
        }}
      >
        {/* Soft highlight */}
        <div
          style={{
            position: 'absolute',
            inset: 0,
            background:
              'radial-gradient(360px 180px at 20% 110%, rgba(255,255,255,0.25), transparent 70%)',
            pointerEvents: 'none',
          }}
        />
        {/* Faint folder watermark */}
        <div
          style={{
            position: 'absolute',
            right: -10,
            bottom: -14,
            opacity: isDarkText ? 0.18 : 0.22,
            color: textColor,
            pointerEvents: 'none',
            transform: 'rotate(-8deg)',
          }}
        >
          <svg width="100" height="100" viewBox="0 0 24 24" fill="none">
            <path
              d="M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"
              fill="currentColor"
            />
          </svg>
        </div>

        {/* Top row: subject pill */}
        <div
          style={{
            position: 'relative',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            minHeight: 20,
          }}
        >
          <span
            style={{
              fontSize: 9.5,
              fontWeight: 700,
              letterSpacing: '0.14em',
              textTransform: 'uppercase',
              padding: '3px 8px',
              background: isDarkText ? 'rgba(0,0,0,0.10)' : 'rgba(0,0,0,0.20)',
              color: textColor,
              borderRadius: 5,
              fontFamily: 'var(--font-mono)',
            }}
          >
            {f.subject ?? 'Folder'}
          </span>
        </div>

        {/* Title */}
        <div
          style={{
            position: 'relative',
            fontSize: 16,
            fontWeight: 800,
            letterSpacing: '-0.015em',
            lineHeight: 1.2,
            wordBreak: 'break-word',
            paddingRight: 28,
          }}
        >
          {f.name}
        </div>
      </div>

      {/* Footer */}
      <div
        style={{
          padding: '12px 14px 14px',
          display: 'flex',
          flexDirection: 'column',
          gap: 10,
          background: 'var(--surface)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className={`badge ${status.cls}`}>
            <span className="dot" />
            {status.label}
          </span>
          <span style={{ fontSize: 11, color: 'var(--ink-500)', marginLeft: 'auto' }}>
            {humanize(f.updated_at || f.created_at)}
          </span>
        </div>

        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            color: 'var(--ink-500)',
            fontSize: 11.5,
            paddingTop: 8,
            borderTop: '1px solid var(--line-2)',
          }}
        >
          <Stat icon="layers" v={f.chapters} lbl="ch" />
          <Stat icon="question" v={f.questions} lbl="Q" />
          <Stat icon="image" v={f.figures} lbl="fig" />
        </div>
      </div>
    </div>
  );
}

function Stat({
  icon,
  v,
  lbl,
}: {
  icon: 'layers' | 'question' | 'image';
  v: number;
  lbl: string;
}) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      <Icon name={icon} size={11} />
      <span className="mono" style={{ fontWeight: 700, color: 'var(--ink-700)' }}>
        {v}
      </span>
      <span>{lbl}</span>
    </span>
  );
}
