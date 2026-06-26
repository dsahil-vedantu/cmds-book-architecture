import { useState } from 'react';

import { Icon, type IconName } from './Icon';

export type ExportFormat = 'docx' | 'pdf' | 'md';

type Props = {
  onClose: () => void;
  onExport: (fmt: ExportFormat) => void;
};

export function ExportMenu({ onClose, onExport }: Props) {
  return (
    <>
      {/* Click-away mask */}
      <div style={{ position: 'fixed', inset: 0, zIndex: 50 }} onClick={onClose} />
      <div
        className="card fade-up"
        style={{
          position: 'absolute',
          top: 'calc(100% + 8px)',
          right: 0,
          width: 280,
          padding: 8,
          zIndex: 51,
          boxShadow: 'var(--sh-pop)',
        }}
      >
        <div
          style={{
            padding: '8px 10px 4px',
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
            color: 'var(--ink-500)',
          }}
        >
          Format
        </div>
        <ExportRow
          ic="docx"
          label="DOCX (Word)"
          sub="Editable, with headings & figures"
          onClick={() => { onExport('docx'); onClose(); }}
        />
        <ExportRow
          ic="pdf"
          label="PDF"
          sub="Print-ready, paginated"
          onClick={() => { onExport('pdf'); onClose(); }}
        />
        <ExportRow
          ic="md"
          label="Markdown"
          sub="For wiki / static sites"
          onClick={() => { onExport('md'); onClose(); }}
        />

        <div style={{ height: 1, background: 'var(--line)', margin: '6px 8px' }} />

        <div
          style={{
            padding: '4px 10px 4px',
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
            color: 'var(--ink-500)',
          }}
        >
          Scope
        </div>
        <ScopeRow />
      </div>
    </>
  );
}

function ScopeRow() {
  const [scope, setScope] = useState<'full' | 'chapter'>('full');
  return (
    <div style={{ padding: 8, display: 'flex', gap: 6 }}>
      <button
        className={`btn ${scope === 'full' ? 'btn-soft' : 'btn-ghost'} btn-sm`}
        style={{ flex: 1, justifyContent: 'center' }}
        onClick={() => setScope('full')}
      >
        Full book
      </button>
      <button
        className={`btn ${scope === 'chapter' ? 'btn-soft' : 'btn-ghost'} btn-sm`}
        style={{ flex: 1, justifyContent: 'center' }}
        onClick={() => setScope('chapter')}
      >
        Per chapter
      </button>
    </div>
  );
}

function ExportRow({
  ic,
  label,
  sub,
  onClick,
}: {
  ic: IconName;
  label: string;
  sub: string;
  onClick: () => void;
}) {
  const [hover, setHover] = useState(false);
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        width: '100%',
        padding: 10,
        border: 'none',
        background: hover ? 'var(--bg-tint)' : 'transparent',
        borderRadius: 8,
        textAlign: 'left',
        cursor: 'pointer',
        transition: 'background 120ms',
      }}
    >
      <div
        style={{
          width: 34,
          height: 34,
          borderRadius: 8,
          background: 'var(--indigo-50)',
          color: 'var(--indigo-700)',
          display: 'grid',
          placeItems: 'center',
        }}
      >
        <Icon name={ic} size={16} />
      </div>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--ink-900)' }}>{label}</div>
        <div style={{ fontSize: 11, color: 'var(--ink-500)' }}>{sub}</div>
      </div>
      <Icon name="download" size={14} className="muted" />
    </button>
  );
}
