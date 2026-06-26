import { useState } from 'react';

import { Icon } from './Icon';
import { createFolder, type Folder } from '../api/folders';

type Props = {
  open: boolean;
  onClose: () => void;
  onCreated: (folder: Folder) => void;
};

export function NewFolderModal({ open, onClose, onCreated }: Props) {
  const [name, setName] = useState('');
  const [subject, setSubject] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  if (!open) return null;

  const reset = () => {
    setName('');
    setSubject('');
    setErr(null);
    setBusy(false);
  };

  const submit = async () => {
    const trimmed = name.trim();
    if (!trimmed) {
      setErr('Folder name is required.');
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const folder = await createFolder({
        name: trimmed,
        subject: subject.trim() || undefined,
      });
      onCreated(folder);
      reset();
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to create folder');
      setBusy(false);
    }
  };

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={() => !busy && onClose()}
        style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(15,23,42,0.45)',
          zIndex: 200,
          animation: 'fadeUp 200ms both',
        }}
      />
      {/* Dialog */}
      <div
        className="card fade-up"
        style={{
          position: 'fixed',
          top: '50%',
          left: '50%',
          transform: 'translate(-50%, -50%)',
          width: 460,
          maxWidth: 'calc(100vw - 32px)',
          padding: 26,
          zIndex: 201,
          boxShadow: 'var(--sh-pop)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 18 }}>
          <div
            style={{
              width: 36,
              height: 36,
              borderRadius: 10,
              background: 'var(--indigo-50)',
              color: 'var(--indigo-700)',
              display: 'grid',
              placeItems: 'center',
            }}
          >
            <Icon name="library" size={18} />
          </div>
          <div style={{ flex: 1 }}>
            <h3
              style={{
                fontSize: 17,
                fontWeight: 800,
                margin: 0,
                color: 'var(--ink-900)',
                letterSpacing: '-0.01em',
              }}
            >
              New book folder
            </h3>
            <div style={{ fontSize: 12, color: 'var(--ink-500)', marginTop: 2 }}>
              A folder groups multiple chapter uploads under one tag.
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div className="field">
            <label>Folder name</label>
            <input
              autoFocus
              type="text"
              value={name}
              placeholder="e.g. NCERT Class 10"
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && submit()}
              disabled={busy}
            />
          </div>
          <div className="field">
            <label>Subject (optional)</label>
            <input
              type="text"
              value={subject}
              placeholder="e.g. Mathematics"
              onChange={(e) => setSubject(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && submit()}
              disabled={busy}
            />
            <div style={{ fontSize: 11, color: 'var(--ink-500)' }}>
              Inherited by chapters uploaded into this folder.
            </div>
          </div>
        </div>

        {err && (
          <div
            style={{
              marginTop: 14,
              padding: '10px 14px',
              background: 'var(--red-50)',
              border: '1px solid var(--red-100)',
              borderRadius: 10,
              color: 'var(--red-700)',
              fontSize: 13,
            }}
          >
            {err}
          </div>
        )}

        <div
          style={{
            marginTop: 22,
            display: 'flex',
            justifyContent: 'flex-end',
            gap: 10,
          }}
        >
          <button
            className="btn btn-ghost"
            onClick={() => {
              reset();
              onClose();
            }}
            disabled={busy}
          >
            Cancel
          </button>
          <button className="btn btn-primary" onClick={submit} disabled={busy}>
            {busy ? <span className="spinner" /> : <Icon name="plus" size={14} />} Create
            folder
          </button>
        </div>
      </div>
    </>
  );
}
