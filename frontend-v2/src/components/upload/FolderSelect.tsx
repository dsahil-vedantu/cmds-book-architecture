import { useState } from 'react';

import { Icon } from '../Icon';
import { FOLDERS, type BookFolder } from '../../mocks/upload';

type Props = {
  value: string;
  onChange: (id: string) => void;
};

export function FolderSelect({ value, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [folders, setFolders] = useState<BookFolder[]>(FOLDERS);

  const sel = folders.find((f) => f.id === value);

  const handleCreate = () => {
    const trimmed = newName.trim();
    if (!trimmed) return;
    const id = trimmed.toLowerCase().replace(/\s+/g, '-');
    const next: BookFolder = { id, name: trimmed, count: 0, color: '#3F4AB0' };
    setFolders((fs) => [...fs, next]);
    onChange(id);
    setCreating(false);
    setNewName('');
    setOpen(false);
  };

  return (
    <div className="field" style={{ position: 'relative' }}>
      <label>Book folder</label>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={{
          height: 40,
          padding: '0 12px',
          border: '1px solid',
          borderColor: open ? 'var(--indigo-500)' : 'var(--line)',
          borderRadius: 10,
          background: 'var(--surface)',
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          font: 'inherit',
          fontSize: 13.5,
          color: 'var(--ink-900)',
          cursor: 'pointer',
          textAlign: 'left',
          transition: 'all 120ms',
          boxShadow: open ? '0 0 0 3px rgba(63,74,176,0.14)' : 'none',
        }}
      >
        <span style={{ width: 8, height: 18, borderRadius: 2, background: sel?.color }} />
        <span style={{ flex: 1 }}>{sel?.name ?? 'Pick a folder'}</span>
        <span style={{ fontSize: 11, color: 'var(--ink-500)' }}>{sel?.count ?? 0} books</span>
        <Icon
          name="chevron"
          size={14}
          className="muted"
          style={{
            transform: open ? 'rotate(-90deg)' : 'rotate(90deg)',
            transition: 'transform 120ms',
          }}
        />
      </button>
      <div style={{ fontSize: 11, color: 'var(--ink-500)' }}>
        Organize your library — books in this folder will appear grouped together.
      </div>

      {open && (
        <>
          <div
            style={{ position: 'fixed', inset: 0, zIndex: 30 }}
            onClick={() => {
              setOpen(false);
              setCreating(false);
            }}
          />
          <div
            className="card fade-up"
            style={{
              position: 'absolute',
              top: 'calc(100% + 4px)',
              left: 0,
              right: 0,
              zIndex: 31,
              boxShadow: 'var(--sh-pop)',
              padding: 6,
              maxHeight: 320,
              overflowY: 'auto',
            }}
          >
            <div
              style={{
                padding: '6px 10px 4px',
                fontSize: 10.5,
                fontWeight: 700,
                letterSpacing: '0.1em',
                textTransform: 'uppercase',
                color: 'var(--ink-500)',
              }}
            >
              Your folders
            </div>
            {folders.map((f) => (
              <FolderRow
                key={f.id}
                folder={f}
                selected={f.id === value}
                onPick={() => {
                  onChange(f.id);
                  setOpen(false);
                }}
              />
            ))}
            <div style={{ height: 1, background: 'var(--line)', margin: '4px 6px' }} />
            {!creating ? (
              <CreateButton onClick={() => setCreating(true)} />
            ) : (
              <div style={{ padding: 8, display: 'flex', gap: 6 }}>
                <input
                  autoFocus
                  type="text"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
                  placeholder="Folder name…"
                  style={{
                    flex: 1,
                    height: 34,
                    padding: '0 10px',
                    border: '1px solid var(--line)',
                    borderRadius: 8,
                    font: 'inherit',
                    fontSize: 13,
                    outline: 'none',
                  }}
                />
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  onClick={handleCreate}
                >
                  Create
                </button>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function FolderRow({
  folder,
  selected,
  onPick,
}: {
  folder: BookFolder;
  selected: boolean;
  onPick: () => void;
}) {
  const [hover, setHover] = useState(false);
  const bg = selected ? 'var(--indigo-50)' : hover ? 'var(--bg-tint)' : 'transparent';
  return (
    <button
      type="button"
      onClick={onPick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        width: '100%',
        padding: '9px 10px',
        border: 'none',
        borderRadius: 8,
        background: bg,
        font: 'inherit',
        textAlign: 'left',
        cursor: 'pointer',
      }}
    >
      <span style={{ width: 6, height: 18, borderRadius: 2, background: folder.color }} />
      <span
        style={{
          flex: 1,
          fontSize: 13.5,
          color: 'var(--ink-900)',
          fontWeight: selected ? 600 : 500,
        }}
      >
        {folder.name}
      </span>
      <span className="kbd" style={{ fontSize: 10 }}>
        {folder.count}
      </span>
      {selected && <Icon name="check" size={14} style={{ color: 'var(--indigo-700)' }} />}
    </button>
  );
}

function CreateButton({ onClick }: { onClick: () => void }) {
  const [hover, setHover] = useState(false);
  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        width: '100%',
        padding: '9px 10px',
        border: 'none',
        borderRadius: 8,
        background: hover ? 'var(--indigo-50)' : 'transparent',
        font: 'inherit',
        textAlign: 'left',
        cursor: 'pointer',
        color: 'var(--indigo-700)',
        fontWeight: 600,
        fontSize: 13.5,
      }}
    >
      <Icon name="plus" size={16} /> Create new folder
    </button>
  );
}
