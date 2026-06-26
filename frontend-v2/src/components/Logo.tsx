type LogoSize = 'md' | 'lg';

export function Logo({ size = 'md' }: { size?: LogoSize }) {
  const big = size === 'lg';
  const dim = big ? 48 : 38;
  return (
    <div className="brand">
      <VedantuMark dim={dim} />
      <div className="brand-wordmark">
        <div className="name" style={big ? { fontSize: 20 } : undefined}>
          V-Studio
        </div>
        <div className="tag">by Vedantu</div>
      </div>
    </div>
  );
}

/**
 * Vedantu V mark — orange/red gradient circle with a stylized white V.
 * Inline SVG so we never get a broken-image state if /logo.png is missing.
 */
function VedantuMark({ dim }: { dim: number }) {
  return (
    <svg
      width={dim}
      height={dim}
      viewBox="0 0 100 100"
      xmlns="http://www.w3.org/2000/svg"
      preserveAspectRatio="xMidYMid meet"
      style={{
        width: dim,
        height: dim,
        minWidth: dim,
        minHeight: dim,
        maxWidth: dim,
        maxHeight: dim,
        aspectRatio: '1 / 1',
        display: 'block',
        flex: '0 0 auto',
      }}
      aria-label="Vedantu"
    >
      <defs>
        <linearGradient id="vmark-grad" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#FF7A4D" />
          <stop offset="100%" stopColor="#E94B35" />
        </linearGradient>
      </defs>
      <circle cx="50" cy="50" r="50" fill="url(#vmark-grad)" />
      {/* Stylised V: two wedge strokes meeting low-center with a soft notch */}
      <path
        d="M28 18 L40 18 L50 62 L60 18 L72 18 L55 82 Q50 90 45 82 Z"
        fill="#FFFFFF"
      />
      <circle cx="50" cy="72" r="5" fill="url(#vmark-grad)" />
    </svg>
  );
}
