// V-Studio chapter upload flow.
//
//   step 1 — pick file (PDF/image/scanned) + folder + subject
//             → real POST /api/books creates the book row
//   step 2 — REAL extraction (orchestrator-driven)
//             schema → theory → (questions + figures parallel) → reconcile
//   done   — auto-navigate to /books/:id/review
//
// No mock animation. Backend pipelines untouched.

import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

import { ExtractingPanel } from '../components/extraction/ExtractingPanel';
import { Icon } from '../components/Icon';
import { Stepper } from '../components/upload/Stepper';
import { UploadStep } from '../components/upload/UploadStep';
import { useToast } from '../components/Toast';
import { uploadChapter } from '../api/books';
import type { PickedFile } from '../mocks/upload';

type Step = 1 | 2;

function deriveTitle(filename: string): string {
  return filename
    .replace(/\.[^.]+$/, '')
    .replace(/[-_]/g, ' ')
    .split(' ')
    .filter(Boolean)
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(' ')
    .trim();
}

export default function UploadPage() {
  const navigate = useNavigate();
  const { flash } = useToast();
  const [search] = useSearchParams();
  const preselectedFolder = search.get('folder') ?? '';

  const [step, setStep] = useState<Step>(1);
  const [file, setFile] = useState<PickedFile | null>(null);
  const realFileRef = useRef<File | null>(null);
  const [folder, setFolder] = useState<string>(preselectedFolder);
  const [subject, setSubject] = useState('');
  const [derivedTitle, setDerivedTitle] = useState('');
  const [isMultiColumn, setIsMultiColumn] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [createdBookId, setCreatedBookId] = useState<string | null>(null);

  // ─── Native File capture (for FormData upload) ──────────────────
  // UploadStep's hidden file input bubbles change/drop events that we
  // intercept here to stash the actual File handle. The metadata-only
  // onPick callback flows separately for UI state.
  useEffect(() => {
    const change = (ev: Event) => {
      const input = ev.target as HTMLInputElement | null;
      if (!input || input.type !== 'file') return;
      const f = input.files?.[0];
      if (f) realFileRef.current = f;
    };
    const drop = (ev: Event) => {
      const drag = ev as DragEvent;
      const f = drag.dataTransfer?.files?.[0];
      if (f) realFileRef.current = f;
    };
    document.addEventListener('change', change, true);
    document.addEventListener('drop', drop, true);
    return () => {
      document.removeEventListener('change', change, true);
      document.removeEventListener('drop', drop, true);
    };
  }, []);

  const onPick = (raw: { name: string; size?: number; pages?: number }) => {
    setFile({
      name: raw.name,
      size: raw.size ?? 0,
      pages: raw.pages,
    });
    setDerivedTitle((prev) => prev || deriveTitle(raw.name));
  };

  const onRemove = () => {
    setFile(null);
    setDerivedTitle('');
    realFileRef.current = null;
    setSubmitError(null);
  };

  // ─── Submit upload, then move to extraction step ────────────────
  const submit = async () => {
    if (!realFileRef.current) {
      setSubmitError(
        'No file selected — pick a PDF or image from the file picker.',
      );
      return;
    }
    if (!folder) {
      setSubmitError('Pick a folder first.');
      return;
    }
    setSubmitting(true);
    setSubmitError(null);
    try {
      const result = await uploadChapter({
        file: realFileRef.current,
        folderId: folder,
        title: derivedTitle || realFileRef.current.name,
        subject: subject || undefined,
        isMultiColumn: isMultiColumn || undefined,
      });
      setCreatedBookId(result.book_id);
      flash('File uploaded · starting extraction…');
      setStep(2);
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : 'Upload failed');
    } finally {
      setSubmitting(false);
    }
  };

  // ─── Render ────────────────────────────────────────────────────
  return (
    <div className="content fade-up">
      <div className="content-narrow" style={{ maxWidth: 980 }}>
        <div className="page-header">
          <div>
            <h1 className="page-title">Upload chapter</h1>
            <div className="page-sub">
              {step === 1
                ? 'Drop a chapter file — extraction starts the moment you continue.'
                : 'Extraction in progress. Hang tight — typically a few minutes.'}
            </div>
          </div>
          {step === 1 && (
            <button className="btn btn-ghost" onClick={() => navigate('/library')}>
              <Icon name="arrow-l" size={16} /> Cancel
            </button>
          )}
        </div>

        <Stepper step={step === 2 ? 2 : 1} />

        {step === 1 && (
          <UploadStep
            file={file}
            folder={folder}
            setFolder={setFolder}
            subject={subject}
            setSubject={setSubject}
            derivedTitle={derivedTitle}
            isMultiColumn={isMultiColumn}
            setIsMultiColumn={setIsMultiColumn}
            onPick={onPick}
            onRemove={onRemove}
            onNext={submit}
            submitting={submitting}
            submitError={submitError}
          />
        )}

        {step === 2 && createdBookId && (
          <ExtractingPanel
            bookId={createdBookId}
            bookTitle={derivedTitle || 'Untitled chapter'}
          />
        )}
      </div>
    </div>
  );
}
