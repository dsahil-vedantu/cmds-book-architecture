// V-Studio Library — grid of book folders.
//
// Each card is a Folder (group of chapter uploads). The top stat band
// aggregates across all folders so the numbers update on add/delete (the
// `refetch` callback is called by NewFolderModal and after deletes).
//
// Real `/api/folders` + `/api/books` drive both the cards and the stats.

import { useMemo } from 'react';
import { useNavigate, useOutletContext } from 'react-router-dom';

import { FolderCard } from '../components/FolderCard';
import { Icon } from '../components/Icon';
import { NewFolderTile } from '../components/NewFolderTile';
import { StatTile } from '../components/StatTile';
import { useFolders, type Folder } from '../api/folders';
import type { ShellContext } from '../components/AppShell';
import { useToast } from '../components/Toast';
import { useState } from 'react';
import { NewFolderModal } from '../components/NewFolderModal';

// Full palette — mirrors backend/app/api/folders.py PALETTE. Used to find
// a substitute when two folders happen to land on the same color within a
// single visible row.
const PALETTE = [
  '#1A237E', '#E94B35', '#10B981', '#8B5CF6',
  '#F59E0B', '#0E7C6B', '#3B82F6', '#EC4899',
  '#06B6D4', '#F43F5E', '#84CC16', '#D946EF',
  '#FB923C', '#64748B', '#EAB308', '#A78471',
];

const ROW_SIZE = 5;

/**
 * Reassign folder colors so that no two folders share a color within the
 * same visible row. Only swaps when a collision exists — folders keep
 * their stored color whenever possible.
 *
 * Pure visual override: backend `folder.color` stays untouched.
 */
function dedupePerRow(folders: Folder[]): Folder[] {
  const out: Folder[] = [];
  for (let i = 0; i < folders.length; i++) {
    const rowStart = Math.floor(i / ROW_SIZE) * ROW_SIZE;
    const usedInRow = new Set(out.slice(rowStart, i).map((f) => f.color));
    const original = folders[i];
    let color = original.color;
    if (usedInRow.has(color)) {
      // Find a palette color not yet used in this row. If the palette is
      // smaller than ROW_SIZE somehow, fall back to the original color.
      const substitute = PALETTE.find((c) => !usedInRow.has(c));
      if (substitute) color = substitute;
    }
    out.push(color === original.color ? original : { ...original, color });
  }
  return out;
}

export default function LibraryPage() {
  const navigate = useNavigate();
  const { books } = useOutletContext<ShellContext>();
  const { flash } = useToast();
  const foldersState = useFolders();
  const [showNew, setShowNew] = useState(false);

  const rawFolders = foldersState.kind === 'ready' ? foldersState.folders : [];
  const folders = dedupePerRow(rawFolders);
  const loading = foldersState.kind === 'loading';
  const error = foldersState.kind === 'error' ? foldersState.error : null;

  // Aggregate stats across folders (these update live on refetch).
  const stats = useMemo(
    () => ({
      folders: folders.length,
      chapters: folders.reduce((s, f) => s + f.chapters, 0),
      questions: folders.reduce((s, f) => s + f.questions, 0),
      figures: folders.reduce((s, f) => s + f.figures, 0),
    }),
    [folders]
  );

  const openFolder = (id: string) => navigate(`/folders/${id}`);

  // Total books across folders — for the empty-state copy decision.
  const totalChapters = books.length;
  void totalChapters;

  return (
    <div className="content fade-up">
      <div className="content-narrow">
        <div className="page-header">
          <div>
            <h1 className="page-title">My Library</h1>
            <div className="page-sub">
              Book folders group related chapter uploads. Click a folder to see the chapters
              inside.
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              className="btn btn-ghost"
              onClick={foldersState.refetch}
              disabled={loading}
            >
              <Icon name="regen" size={16} /> Refresh
            </button>
            <button className="btn btn-soft" onClick={() => setShowNew(true)}>
              <Icon name="plus" size={16} /> New folder
            </button>
            <button className="btn btn-accent" onClick={() => navigate('/upload')}>
              <Icon name="upload" size={16} /> Upload chapter
            </button>
          </div>
        </div>

        {error && <LibraryError error={error} onRetry={foldersState.refetch} />}

        {/* Top stat band — always shown, even when empty */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(4, 1fr)',
            gap: 14,
            marginBottom: 24,
          }}
        >
          <StatTile label="Book folders" value={stats.folders} sub="containers" />
          <StatTile label="Chapters" value={stats.chapters.toLocaleString()} sub="uploaded" />
          <StatTile
            label="Questions"
            value={stats.questions.toLocaleString()}
            sub="extracted across all folders"
          />
          <StatTile
            label="Figures"
            value={stats.figures.toLocaleString()}
            sub="extracted"
            accent
          />
        </div>

        {/* Folder grid — 5 per row, tight */}
        {loading && folders.length === 0 ? (
          <FolderSkeleton />
        ) : (
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(5, 1fr)',
              gap: 14,
            }}
          >
            {folders.map((f) => (
              <FolderCard key={f.id} folder={f} onOpen={openFolder} />
            ))}
            <NewFolderTile onClick={() => setShowNew(true)} />
          </div>
        )}

        {!loading && !error && folders.length === 0 && (
          <div
            style={{
              fontSize: 13,
              color: 'var(--ink-500)',
              textAlign: 'center',
              marginTop: 18,
            }}
          >
            No folders yet. Create one to get started.
          </div>
        )}
      </div>

      <NewFolderModal
        open={showNew}
        onClose={() => setShowNew(false)}
        onCreated={(f) => {
          flash(`Folder "${f.name}" created`);
          void foldersState.refetch();
        }}
      />
    </div>
  );
}

function LibraryError({ error, onRetry }: { error: string; onRetry: () => void }) {
  return (
    <div
      className="card"
      style={{
        padding: 20,
        marginBottom: 18,
        background: 'var(--red-50)',
        border: '1px solid var(--red-100)',
        display: 'flex',
        alignItems: 'center',
        gap: 14,
      }}
    >
      <div
        style={{
          width: 38,
          height: 38,
          borderRadius: 10,
          background: 'var(--red-600)',
          color: '#fff',
          display: 'grid',
          placeItems: 'center',
          flexShrink: 0,
        }}
      >
        !
      </div>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--red-700)' }}>
          Couldn't reach the backend
        </div>
        <div
          style={{
            fontSize: 12,
            color: 'var(--ink-700)',
            marginTop: 2,
            fontFamily: 'var(--font-mono)',
            wordBreak: 'break-word',
          }}
        >
          {error}
        </div>
      </div>
      <button className="btn btn-ghost btn-sm" onClick={onRetry}>
        Retry
      </button>
    </div>
  );
}

function FolderSkeleton() {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 14 }}>
      {Array.from({ length: 5 }).map((_, i) => (
        <div
          key={i}
          className="card"
          style={{ padding: 0, overflow: 'hidden', minHeight: 154, opacity: 0.6 }}
        >
          <div
            style={{
              height: 86,
              background:
                'linear-gradient(90deg, var(--bg-tint) 0%, var(--surface-2) 50%, var(--bg-tint) 100%)',
              backgroundSize: '200% 100%',
              animation: 'shimmer 1.6s linear infinite',
            }}
          />
          <div style={{ padding: 12 }}>
            <div
              style={{
                height: 10,
                background: 'var(--bg-tint)',
                borderRadius: 4,
                width: '50%',
              }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}
