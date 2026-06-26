import { useRef, useState } from 'react';

import { FilePicked } from './FilePicked';
import { RealFolderSelect } from './RealFolderSelect';
import { Icon } from '../Icon';
import type { PickedFile } from '../../mocks/upload';

type Props = {
  file: PickedFile | null;
  folder: string;
  setFolder: (id: string) => void;
  subject: string;
  setSubject: (s: string) => void;
  /** Chapter title — derived from filename, not user-editable. */
  derivedTitle: string;
  /** Multi-column layout flag — routes schema generation to the
   * multi-column-aware Gemini prompt (MHT-CET, JEE prep books). */
  isMultiColumn: boolean;
  setIsMultiColumn: (v: boolean) => void;
  onPick: (f: { name: string; size?: number; pages?: number }) => void;
  onRemove: () => void;
  onNext: () => void;
  /** Submission state from the parent. */
  submitting?: boolean;
  submitError?: string | null;
};

// Accept PDF, common image types, and TIFF (scanned-document case).
const ACCEPT = 'application/pdf,image/png,image/jpeg,image/jpg,image/webp,image/tiff,.pdf,.png,.jpg,.jpeg,.webp,.tif,.tiff';

export function UploadStep({
  file,
  folder,
  setFolder,
  subject,
  setSubject,
  derivedTitle,
  isMultiColumn,
  setIsMultiColumn,
  onPick,
  onRemove,
  onNext,
  submitting,
  submitError,
}: Props) {
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleBrowse = () => inputRef.current?.click();

  return (
    <div className="card" style={{ padding: 28 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          justifyContent: 'space-between',
          marginBottom: 16,
        }}
      >
        <h3
          style={{
            fontSize: 18,
            fontWeight: 700,
            color: 'var(--ink-900)',
            margin: 0,
            letterSpacing: '-0.01em',
          }}
        >
          Upload chapter
        </h3>
        <div style={{ fontSize: 12, color: 'var(--ink-500)' }}>
          PDF, image, or scanned image · up to 200 MB
        </div>
      </div>

      {!file ? (
        <div
          className={`dropzone ${dragging ? 'drag' : ''}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            const f = e.dataTransfer.files?.[0];
            if (f) onPick({ name: f.name, size: f.size });
          }}
          onClick={handleBrowse}
        >
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPT}
            style={{ display: 'none' }}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) onPick({ name: f.name, size: f.size });
            }}
          />
          <div
            style={{
              width: 72,
              height: 72,
              margin: '0 auto 16px',
              borderRadius: '50%',
              background: 'linear-gradient(135deg, var(--indigo-50), #fff)',
              border: '1px solid var(--indigo-100)',
              display: 'grid',
              placeItems: 'center',
              color: 'var(--indigo-700)',
            }}
          >
            <Icon name="upload" size={28} />
          </div>
          <div
            style={{
              fontSize: 17,
              fontWeight: 700,
              color: 'var(--ink-900)',
              letterSpacing: '-0.01em',
            }}
          >
            Drop your chapter file here, or{' '}
            <span style={{ color: 'var(--indigo-700)' }}>click to browse</span>
          </div>
          <div style={{ fontSize: 13, color: 'var(--ink-500)', marginTop: 8 }}>
            PDF · scanned PDF · image (PNG / JPG / WebP / TIFF) — V-Studio extracts
            chapters, theory, questions and figures automatically.
          </div>
        </div>
      ) : (
        <FilePicked file={file} onRemove={onRemove} />
      )}

      {file && (
        <>
          <div
            style={{
              marginTop: 22,
              display: 'grid',
              gridTemplateColumns: '1.6fr 1fr',
              gap: 18,
            }}
          >
            <RealFolderSelect value={folder} onChange={setFolder} />
            <div className="field">
              <label>Subject</label>
              <input
                type="text"
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                placeholder="e.g. Mathematics"
              />
              <div style={{ fontSize: 11, color: 'var(--ink-500)' }}>
                Optional — defaults to the folder's subject if it has one.
              </div>
            </div>
          </div>

          {/* Read-only derived chapter title for transparency */}
          <div
            style={{
              marginTop: 14,
              padding: '12px 14px',
              background: 'var(--surface-2)',
              border: '1px solid var(--line)',
              borderRadius: 10,
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              fontSize: 13,
            }}
          >
            <Icon name="file" size={14} className="muted" />
            <div style={{ flex: 1 }}>
              <div
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  textTransform: 'uppercase',
                  letterSpacing: '0.08em',
                  color: 'var(--ink-500)',
                  marginBottom: 2,
                }}
              >
                Chapter title (from filename)
              </div>
              <div style={{ color: 'var(--ink-900)', fontWeight: 600 }}>
                {derivedTitle || '—'}
              </div>
            </div>
          </div>

          {/* Multi-column layout flag — routes the analyse worker to the
              multi-column-aware schema prompt so dense MCQ-bank books
              (MHT-CET, JEE prep, etc.) get properly classified instead
              of being collapsed into "all explanations" excluded sections. */}
          <label
            style={{
              marginTop: 14,
              display: 'flex',
              alignItems: 'flex-start',
              gap: 10,
              padding: '12px 14px',
              background: 'var(--surface-2)',
              border: '1px solid var(--line)',
              borderRadius: 10,
              cursor: 'pointer',
              fontSize: 13,
            }}
          >
            <input
              type="checkbox"
              checked={isMultiColumn}
              onChange={(e) => setIsMultiColumn(e.target.checked)}
              style={{ marginTop: 3 }}
            />
            <div>
              <div style={{ color: 'var(--ink-900)', fontWeight: 600 }}>
                Multi-column PDF (2 or 3 columns per page)
              </div>
              <div
                style={{
                  fontSize: 11,
                  color: 'var(--ink-500)',
                  marginTop: 2,
                }}
              >
                Tick for MHT-CET, JEE/NEET prep books, dense question banks
                where each page is split into side-by-side columns. Routes
                the analyser to a layout-aware schema prompt.
              </div>
            </div>
          </label>
        </>
      )}

      {submitError && (
        <div
          style={{
            marginTop: 16,
            padding: '10px 14px',
            background: 'var(--red-50)',
            border: '1px solid var(--red-100)',
            borderRadius: 10,
            color: 'var(--red-700)',
            fontSize: 13,
          }}
        >
          {submitError}
        </div>
      )}

      {file && (
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginTop: 24,
          }}
        >
          <button
            className="btn btn-ghost"
            onClick={onRemove}
            disabled={submitting}
          >
            Choose different file
          </button>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            <span style={{ fontSize: 12, color: 'var(--ink-500)' }}>
              Extraction starts as soon as you continue.
            </span>
            <button
              className="btn btn-primary"
              onClick={onNext}
              disabled={submitting || !folder}
            >
              {submitting ? <span className="spinner" /> : <Icon name="arrow-r" size={14} />}
              {submitting ? 'Uploading…' : 'Start extraction'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
