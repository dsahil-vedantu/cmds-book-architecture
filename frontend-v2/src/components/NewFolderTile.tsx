import { useState } from 'react';

import { Icon } from './Icon';

export function NewFolderTile({ onClick }: { onClick: () => void }) {
  const [hover, setHover] = useState(false);
  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        border: `2px dashed ${hover ? 'var(--indigo-500)' : '#C8CDDB'}`,
        borderRadius: 14,
        minHeight: 196,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 10,
        color: hover ? 'var(--indigo-700)' : 'var(--ink-500)',
        cursor: 'pointer',
        transition: 'all 160ms',
        background: hover
          ? 'linear-gradient(135deg, var(--indigo-50) 0%, #fff 100%)'
          : 'transparent',
        padding: 14,
      }}
    >
      <div
        style={{
          width: 40,
          height: 40,
          borderRadius: '50%',
          display: 'grid',
          placeItems: 'center',
          background: 'currentColor',
          color: '#fff',
          boxShadow: hover ? '0 6px 18px -6px rgba(26,35,126,0.35)' : 'none',
          transition: 'box-shadow 160ms',
        }}
      >
        <Icon name="plus" size={20} />
      </div>
      <div style={{ fontSize: 13.5, fontWeight: 700 }}>New folder</div>
      <div
        style={{
          fontSize: 11.5,
          maxWidth: 160,
          textAlign: 'center',
          lineHeight: 1.4,
        }}
      >
        Group related chapter uploads together.
      </div>
    </div>
  );
}
