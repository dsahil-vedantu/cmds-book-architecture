// Folder picker wired to the real /api/folders endpoint.
//
// Lists current folders, lets the user pick one, and opens an inline
// "create folder" input that hits POST /api/folders. On success the new
// folder becomes the selection. Keeps the visual language of the
// design's static FolderSelect.

import { useEffect, useState } from 'react';

import { Icon } from '../Icon';
import { createFolder, listFolders, type Folder } from '../../api/folders';

type Props = {
  value: string;
  onChange: (id: string) => void;
};

export function RealFolderSelect({ value, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [folders, setFolders] = useState<Folder[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const fs = await listFolders();
      setFolders(fs);
      // Auto-select the first folder if none chosen yet.
      if (!value && fs.length > 0) onChange(fs[0].id);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load folders');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const sel = folders.find((f) => f.id === value);

  const handleCreate = async () => {
    const trimmed = newName.trim();
    if (!trimmed) return;
    setBusy(true);
    try {
      const folder = await createFolder({ name: trimmed });
      setFolders((fs) => [...fs, folder]);
      onChange(folder.id);
      setCreating(false);
      setNewName('');
      setOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create folder');
    } finally {
      setBusy(false);
    }
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
          boxShadow: open ? '0 0 0 3px rgba(63,74,176,0.14)' : 'none',
        }}
      >
        <span
          style={{
            width: 8,
            height: 18,
            borderRadius: 2,
            background: sel?.color ?? 'var(--ink-300)',
          }}
        />
        <span style={{ flex: 1 }}>
          {loading
            ? 'Loading folders…'
            : sel
            ? sel.name
            : folders.length === 0
            ? 'No folders yet — create one'
            : 'Pick a folder'}
        </span>
        {sel && (
          <span style={{ fontSize: 11, color: 'var(--ink-500)' }}>
            {sel.chapters} chapter{sel.chapters === 1 ? '' : 's'}
          </span>
        )}
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
        Choose where this chapter PDF should live, or create a new folder.
      </div>

      {error && (
        <div
          style={{
            marginTop: 8,
            padding: '8px 12px',
            background: 'var(--red-50)',
            border: '1px solid var(--red-100)',
            borderRadius: 8,
            color: 'var(--red-700)',
            fontSize: 12,
          }}
        >
          {error}
        </div>
      )}

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
            {folders.length === 0 && !loading && (
              <div
                style={{ padding: '10px 12px', fontSize: 12, color: 'var(--ink-500)' }}
              >
                No folders yet. Create the first one below.
              </div>
            )}
            {folders.map((f) => (
              <button
                key={f.id}
                type="button"
                onClick={() => {
                  onChange(f.id);
                  setOpen(false);
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  width: '100%',
                  padding: '9px 10px',
                  border: 'none',
                  borderRadius: 8,
                  background: f.id === value ? 'var(--indigo-50)' : 'transparent',
                  font: 'inherit',
                  textAlign: 'left',
                  cursor: 'pointer',
                }}
              >
                <span
                  style={{ width: 6, height: 18, borderRadius: 2, background: f.color }}
                />
                <span
                  style={{
                    flex: 1,
                    fontSize: 13.5,
                    color: 'var(--ink-900)',
                    fontWeight: f.id === value ? 600 : 500,
                  }}
                >
                  {f.name}
                </span>
                <span className="kbd" style={{ fontSize: 10 }}>
                  {f.chapters}
                </span>
                {f.id === value && (
                  <Icon name="check" size={14} style={{ color: 'var(--indigo-700)' }} />
                )}
              </button>
            ))}
            <div style={{ height: 1, background: 'var(--line)', margin: '4px 6px' }} />
            {!creating ? (
              <button
                type="button"
                onClick={() => setCreating(true)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  width: '100%',
                  padding: '9px 10px',
                  border: 'none',
                  borderRadius: 8,
                  background: 'transparent',
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
            ) : (
              <div style={{ padding: 8, display: 'flex', gap: 6 }}>
                <input
                  autoFocus
                  type="text"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
                  placeholder="Folder name…"
                  disabled={busy}
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
                  disabled={busy}
                >
                  {busy ? <span className="spinner" /> : 'Create'}
                </button>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
