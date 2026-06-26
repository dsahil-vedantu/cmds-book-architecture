import { useState } from 'react';

import { Icon } from './Icon';

export function NewBookTile({ onClick }: { onClick: () => void }) {
  const [hover, setHover] = useState(false);

  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        border: `2px dashed ${hover ? 'var(--indigo-500)' : '#C8CDDB'}`,
        borderRadius: 14,
        minHeight: 270,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 12,
        color: hover ? 'var(--indigo-700)' : 'var(--ink-500)',
        cursor: 'pointer',
        transition: 'all 120ms',
        background: hover ? 'var(--indigo-50)' : 'transparent',
      }}
    >
      <div
        style={{
          width: 52,
          height: 52,
          borderRadius: '50%',
          display: 'grid',
          placeItems: 'center',
          background: 'currentColor',
          color: '#fff',
        }}
      >
        <Icon name="plus" size={26} />
      </div>
      <div style={{ fontSize: 15, fontWeight: 700 }}>Upload a new book</div>
      <div style={{ fontSize: 12.5, maxWidth: 200, textAlign: 'center' }}>
        Drop a PDF and pick a style template to begin.
      </div>
    </div>
  );
}
