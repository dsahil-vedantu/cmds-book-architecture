// FolderPage — drill-down from Library.
//
// Shows the folder header (name, color, subject, aggregate counts) and a
// list of chapters (= books) inside. Chapters are pulled from the shared
// shell context (which already fetched /api/books) and filtered by
// folder_id, so we avoid an extra round-trip.

import { useState } from 'react';
import { useNavigate, useOutletContext, useParams } from 'react-router-dom';

import { Icon, type IconName } from '../components/Icon';
import { StatusBadge } from '../lib/status';
import { useFolder, deleteFolder } from '../api/folders';
import { listRegenerations } from '../api/regenerations';

// Mirror FolderCard's GRADIENTS map. Keep both in sync when adding colors.
const GRADIENTS: Record<string, string> = {
  '#1A237E': 'linear-gradient(135deg, #5C6BC0 0%, #7986CB 100%)',
  '#E94B35': 'linear-gradient(135deg, #FF7A60 0%, #FFA48F 100%)',
  '#10B981': 'linear-gradient(135deg, #34D399 0%, #6EE7B7 100%)',
  '#8B5CF6': 'linear-gradient(135deg, #A78BFA 0%, #C4B5FD 100%)',
  '#F59E0B': 'linear-gradient(135deg, #FBBF24 0%, #FCD34D 100%)',
  '#0E7C6B': 'linear-gradient(135deg, #2DD4BF 0%, #5EEAD4 100%)',
  '#3B82F6': 'linear-gradient(135deg, #60A5FA 0%, #93C5FD 100%)',
  '#EC4899': 'linear-gradient(135deg, #F472B6 0%, #F9A8D4 100%)',
  '#06B6D4': 'linear-gradient(135deg, #22D3EE 0%, #67E8F7 100%)',
  '#F43F5E': 'linear-gradient(135deg, #FB7185 0%, #FDA4AF 100%)',
  '#84CC16': 'linear-gradient(135deg, #A3E635 0%, #D9F99D 100%)',
  '#D946EF': 'linear-gradient(135deg, #E879F9 0%, #F0ABFC 100%)',
  '#FB923C': 'linear-gradient(135deg, #FB923C 0%, #FDBA74 100%)',
  '#64748B': 'linear-gradient(135deg, #94A3B8 0%, #CBD5E1 100%)',
  '#EAB308': 'linear-gradient(135deg, #FACC15 0%, #FDE047 100%)',
  '#A78471': 'linear-gradient(135deg, #B79683 0%, #D6C2AE 100%)',
};
const DARK_TEXT = new Set(['#F59E0B', '#84CC16', '#EAB308']);
const gradientFor = (c: string) =>
  GRADIENTS[c] ?? `linear-gradient(135deg, ${c} 0%, ${c}aa 100%)`;
const heroTextColor = (c: string) =>
  DARK_TEXT.has(c) ? 'var(--ink-900)' : '#fff';
import { useToast } from '../components/Toast';
import type { Book } from '../mocks/books';
import type { ShellContext } from '../components/AppShell';

export default function FolderPage() {
  const { folderId } = useParams();
  const navigate = useNavigate();
  const { books, refetch: refetchBooks } = useOutletContext<ShellContext>();
  const state = useFolder(folderId);
  const { flash } = useToast();
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  if (state.kind === 'loading') {
    return (
      <div className="content fade-up">
        <div className="content-narrow">
          <div className="card" style={{ padding: 28, color: 'var(--ink-500)' }}>
            Loading folder…
          </div>
        </div>
      </div>
    );
  }
  if (state.kind === 'error') {
    return (
      <div className="content fade-up">
        <div className="content-narrow">
          <div
            className="card"
            style={{
              padding: 24,
              background: 'var(--red-50)',
              border: '1px solid var(--red-100)',
              display: 'flex',
              gap: 14,
              alignItems: 'center',
            }}
          >
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 700, color: 'var(--red-700)' }}>
                Couldn't load this folder
              </div>
              <div
                style={{
                  fontSize: 12,
                  fontFamily: 'var(--font-mono)',
                  marginTop: 4,
                  wordBreak: 'break-word',
                }}
              >
                {state.error}
              </div>
            </div>
            <button className="btn btn-ghost btn-sm" onClick={state.refetch}>
              Retry
            </button>
            <button className="btn btn-soft btn-sm" onClick={() => navigate('/library')}>
              Back to library
            </button>
          </div>
        </div>
      </div>
    );
  }

  const folder = state.folder;
  const chapters = books.filter((b) => b.folder_id === folder.id);

  const handleDelete = async () => {
    if (folder.chapters > 0) {
      setDeleteError(
        `Folder has ${folder.chapters} chapter(s) inside. Move or delete them first.`
      );
      return;
    }
    if (!confirm(`Delete folder "${folder.name}"? This cannot be undone.`)) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteFolder(folder.id);
      flash(`Folder "${folder.name}" deleted`);
      navigate('/library');
    } catch (e) {
      setDeleteError(e instanceof Error ? e.message : 'Delete failed');
      setDeleting(false);
    }
  };

  return (
    <div className="content fade-up">
      <div className="content-narrow">
        {/* Folder hero */}
        <div className="card" style={{ padding: 0, overflow: 'hidden', marginBottom: 22 }}>
          {(() => {
            const txt = heroTextColor(folder.color);
            const dark = txt !== '#fff';
            return (
              <div
                style={{
                  padding: '24px 26px',
                  background: gradientFor(folder.color),
                  color: txt,
                  position: 'relative',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 18,
                  overflow: 'hidden',
                }}
              >
                {/* Soft highlight */}
                <div
                  style={{
                    position: 'absolute',
                    inset: 0,
                    background:
                      'radial-gradient(500px 280px at 90% -20%, rgba(255,255,255,0.22), transparent 60%)',
                    pointerEvents: 'none',
                  }}
                />
                {/* Watermark folder icon */}
                <div
                  style={{
                    position: 'absolute',
                    right: -20,
                    bottom: -30,
                    opacity: dark ? 0.18 : 0.22,
                    color: txt,
                    pointerEvents: 'none',
                    transform: 'rotate(-8deg)',
                  }}
                >
                  <svg width="220" height="220" viewBox="0 0 24 24" fill="none">
                    <path
                      d="M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"
                      fill="currentColor"
                    />
                  </svg>
                </div>
                <div
                  style={{
                    width: 56,
                    height: 56,
                    borderRadius: 12,
                    background: dark ? 'rgba(0,0,0,0.10)' : 'rgba(255,255,255,0.20)',
                    color: txt,
                    display: 'grid',
                    placeItems: 'center',
                    position: 'relative',
                    flexShrink: 0,
                  }}
                >
                  <Icon name="library" size={26} />
                </div>
                <div style={{ flex: 1, position: 'relative' }}>
                  <div
                    style={{
                      fontSize: 11,
                      fontWeight: 700,
                      letterSpacing: '0.14em',
                      textTransform: 'uppercase',
                      opacity: 0.85,
                      marginBottom: 4,
                    }}
                  >
                    Book folder
                  </div>
                  <h1
                    style={{
                      fontSize: 28,
                      fontWeight: 800,
                      letterSpacing: '-0.02em',
                      margin: 0,
                      lineHeight: 1.1,
                    }}
                  >
                    {folder.name}
                  </h1>
                  {folder.subject && (
                    <div style={{ fontSize: 13, marginTop: 6, opacity: 0.9 }}>
                      Subject: <strong>{folder.subject}</strong>
                    </div>
                  )}
                </div>
                <button
                  className="btn"
                  style={{
                    background: dark ? 'var(--ink-900)' : 'rgba(255,255,255,0.20)',
                    color: dark ? '#fff' : '#fff',
                    border: dark ? 'none' : '1px solid rgba(255,255,255,0.28)',
                    position: 'relative',
                  }}
                  onClick={() => navigate(`/upload?folder=${folder.id}`)}
                >
                  <Icon name="upload" size={14} /> Upload chapter
                </button>
              </div>
            );
          })()}

          {/* Stat strip */}
          <div
            style={{
              padding: '18px 26px',
              display: 'flex',
              gap: 28,
              borderTop: '1px solid var(--line)',
              background: 'var(--surface-2)',
            }}
          >
            <Stat label="Chapters" value={folder.chapters} />
            <Stat label="Questions" value={folder.questions} />
            <Stat label="Figures" value={folder.figures} />
            <Stat label="Ready" value={folder.chapters_ready} />
            <Stat label="Processing" value={folder.chapters_processing} />
            <Stat label="Queued" value={folder.chapters_queued} />
            <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
              <button
                className="btn btn-ghost btn-sm"
                onClick={handleDelete}
                disabled={deleting}
              >
                Delete folder
              </button>
            </div>
          </div>
          {deleteError && (
            <div
              style={{
                padding: '10px 26px',
                background: 'var(--red-50)',
                color: 'var(--red-700)',
                fontSize: 13,
                borderTop: '1px solid var(--red-100)',
              }}
            >
              {deleteError}
            </div>
          )}
        </div>

        {/* Chapter list header */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: 12,
          }}
        >
          <h3
            style={{
              fontSize: 16,
              fontWeight: 700,
              color: 'var(--ink-900)',
              margin: 0,
              letterSpacing: '-0.01em',
            }}
          >
            Chapters in this folder
          </h3>
          <button
            className="btn btn-ghost btn-sm"
            onClick={refetchBooks}
          >
            <Icon name="regen" size={14} /> Refresh
          </button>
        </div>

        <div className="card" style={{ overflow: 'hidden' }}>
          {chapters.length === 0 && (
            <div style={{ padding: 28, color: 'var(--ink-500)', textAlign: 'center' }}>
              No chapters in this folder yet.{' '}
              <button
                className="btn btn-soft btn-sm"
                style={{ marginLeft: 8 }}
                onClick={() => navigate(`/upload?folder=${folder.id}`)}
              >
                Upload first chapter
              </button>
            </div>
          )}
          {chapters.map((c, i) => (
            <ChapterRow
              key={c.id}
              book={c}
              isFirst={i === 0}
              // If a regeneration already exists → jump straight into the
              // regen-review page (the demo-ready editor flow). Otherwise
              // fall back to the extract page where the user can view
              // extracted content or start a regeneration.
              onOpen={async () => {
                try {
                  const regens = await listRegenerations(c.id);
                  if (regens && regens.length > 0) {
                    navigate(`/books/${c.id}/regen-review`);
                    return;
                  }
                } catch {
                  // Network / 404 → fall through to extract page.
                }
                navigate(`/books/${c.id}/extract`);
              }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div
        style={{
          fontSize: 10.5,
          fontWeight: 600,
          letterSpacing: '0.08em',
          textTransform: 'uppercase',
          color: 'var(--ink-500)',
        }}
      >
        {label}
      </div>
      <div
        className="mono"
        style={{
          fontSize: 22,
          fontWeight: 800,
          color: 'var(--ink-900)',
          marginTop: 2,
        }}
      >
        {value}
      </div>
    </div>
  );
}

function ChapterRow({
  book,
  isFirst,
  onOpen,
}: {
  book: Book;
  isFirst: boolean;
  onOpen: () => void;
}) {
  const [hover, setHover] = useState(false);
  return (
    <div
      onClick={onOpen}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'grid',
        gridTemplateColumns: '40px 1fr 90px 90px 90px 130px 100px 36px',
        alignItems: 'center',
        gap: 14,
        padding: '14px 22px',
        borderTop: isFirst ? 'none' : '1px solid var(--line-2)',
        cursor: 'pointer',
        background: hover ? 'var(--surface-2)' : 'transparent',
        transition: 'background 120ms',
      }}
    >
      <div
        className="mono"
        style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--ink-400)' }}
      >
        PDF
      </div>
      <div>
        <div
          style={{
            fontSize: 14.5,
            fontWeight: 600,
            color: 'var(--ink-900)',
            letterSpacing: '-0.005em',
          }}
        >
          {book.title}
        </div>
        <div style={{ fontSize: 11.5, color: 'var(--ink-500)', marginTop: 2 }}>
          {book.subject}
        </div>
      </div>
      <ColCount icon="layers" v={book.chapters} />
      <ColCount icon="question" v={book.questions} />
      <ColCount icon="image" v={book.figures} />
      <div>
        <StatusBadge status={book.status} />
      </div>
      <div style={{ fontSize: 12, color: 'var(--ink-500)' }}>{book.updated}</div>
      <div style={{ color: 'var(--ink-400)', textAlign: 'right' }}>
        <Icon name="chevron" size={16} />
      </div>
    </div>
  );
}

function ColCount({ icon, v }: { icon: IconName; v: number }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        color: 'var(--ink-500)',
        fontSize: 13,
      }}
    >
      <Icon name={icon} size={13} />
      <span className="mono" style={{ fontWeight: 600, color: 'var(--ink-700)' }}>
        {v}
      </span>
    </div>
  );
}
