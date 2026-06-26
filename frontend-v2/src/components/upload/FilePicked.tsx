import { Icon } from '../Icon';
import type { PickedFile } from '../../mocks/upload';

type Props = { file: PickedFile; onRemove: () => void };

export function FilePicked({ file, onRemove }: Props) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 16,
        padding: 18,
        border: '1px solid var(--indigo-100)',
        background: 'var(--indigo-50)',
        borderRadius: 12,
      }}
    >
      <div
        style={{
          width: 52,
          height: 64,
          borderRadius: 6,
          background: 'linear-gradient(135deg, #C73824, #E94B35)',
          color: '#fff',
          display: 'grid',
          placeItems: 'center',
          fontSize: 11,
          fontWeight: 800,
          fontFamily: 'var(--font-mono)',
          boxShadow: '0 4px 10px -4px rgba(233,75,53,0.5)',
          position: 'relative',
        }}
      >
        PDF
        <div
          style={{
            position: 'absolute',
            top: 0,
            right: 0,
            width: 14,
            height: 14,
            background: 'rgba(0,0,0,0.20)',
            clipPath: 'polygon(0 0, 100% 100%, 100% 0)',
          }}
        />
      </div>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--ink-900)' }}>{file.name}</div>
        <div
          style={{
            display: 'flex',
            gap: 14,
            fontSize: 12,
            color: 'var(--ink-500)',
            marginTop: 4,
          }}
        >
          <span>{(file.size / 1_000_000).toFixed(1)} MB</span>
          <span>·</span>
          <span>{file.pages ?? 218} pages detected</span>
          <span>·</span>
          <span
            style={{
              color: 'var(--success)',
              display: 'inline-flex',
              alignItems: 'center',
              gap: 4,
            }}
          >
            <Icon name="check" size={12} /> Text layer present
          </span>
        </div>
      </div>
      <button className="btn btn-ghost btn-sm" onClick={onRemove}>
        Remove
      </button>
    </div>
  );
}
