import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { ExportMenu, type ExportFormat } from '../components/ExportMenu';
import { Icon, type IconName } from '../components/Icon';
import { MiniStat } from '../components/MiniStat';
import { SmartProgress } from '../components/SmartProgress';
import { StatusBadge } from '../lib/status';
import { COVER_GRADIENTS } from '../mocks/books';
import { useBook, type BackendChapter } from '../api/books';
import { useToast } from '../components/Toast';

export default function BookPage() {
  const { bookId } = useParams();
  const navigate = useNavigate();
  const state = useBook(bookId);
  const { flash } = useToast();
  const [exportOpen, setExportOpen] = useState(false);

  if (state.kind === 'loading') {
    return (
      <div className="content fade-up">
        <div className="content-narrow">
          <div className="card" style={{ padding: 28, color: 'var(--ink-500)' }}>
            Loading book…
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
                Couldn't load this book
              </div>
              <div
                style={{
                  fontSize: 12,
                  color: 'var(--ink-700)',
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
            <button
              className="btn btn-soft btn-sm"
              onClick={() => navigate('/library')}
            >
              Back to library
            </button>
          </div>
        </div>
      </div>
    );
  }

  const { book, chapters } = state.data;
  const grad = COVER_GRADIENTS[book.cover];
  const done = chapters.filter((c) => c.status === 'done').length;
  const processing = chapters.filter((c) => c.status === 'processing').length;
  const queued = chapters.filter((c) => c.status === 'queued').length;

  const handleExport = (fmt: ExportFormat) => {
    flash(`Exporting as ${fmt.toUpperCase()}…`);
  };

  const openChapter = (c: BackendChapter) => {
    if (c.status === 'done') navigate(`/books/${book.id}/chapters/${c.id}`);
  };

  return (
    <div className="content fade-up">
      <div className="content-narrow">
        {/* Book hero */}
        <div className="card" style={{ padding: 0, overflow: 'hidden', marginBottom: 22 }}>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '180px 1fr auto',
              gap: 28,
              padding: 26,
              alignItems: 'center',
            }}
          >
            {/* Cover */}
            <div
              style={{
                height: 220,
                width: 160,
                borderRadius: 8,
                background: grad,
                position: 'relative',
                padding: 18,
                color: '#fff',
                boxShadow: '0 14px 30px -10px rgba(15,23,42,0.28)',
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'space-between',
              }}
            >
              <div
                style={{
                  fontSize: 9.5,
                  fontWeight: 700,
                  letterSpacing: '0.14em',
                  textTransform: 'uppercase',
                  padding: '3px 7px',
                  background: 'rgba(0,0,0,0.20)',
                  borderRadius: 5,
                  fontFamily: 'var(--font-mono)',
                  alignSelf: 'flex-start',
                }}
              >
                {book.subject}
              </div>
              <div>
                <div style={{ fontSize: 10, opacity: 0.85, marginBottom: 4 }}>{book.grade}</div>
                <div
                  style={{
                    fontSize: 16,
                    fontWeight: 800,
                    letterSpacing: '-0.02em',
                    lineHeight: 1.15,
                  }}
                >
                  {book.title}
                </div>
              </div>
              <div
                style={{
                  position: 'absolute',
                  right: 0,
                  top: 0,
                  bottom: 0,
                  width: 4,
                  background: 'rgba(255,255,255,0.18)',
                }}
              />
            </div>

            {/* Title block */}
            <div>
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  marginBottom: 10,
                }}
              >
                <StatusBadge status={book.status} />
                <span style={{ fontSize: 12, color: 'var(--ink-500)' }}>
                  Updated {book.updated}
                </span>
              </div>
              <h1
                style={{
                  fontSize: 32,
                  fontWeight: 800,
                  letterSpacing: '-0.025em',
                  color: 'var(--ink-900)',
                  margin: 0,
                  lineHeight: 1.15,
                }}
              >
                {book.title}
              </h1>
              <div style={{ fontSize: 14, color: 'var(--ink-500)', marginTop: 8 }}>
                {book.subject} · {book.grade} · <span className="kbd">{book.template}</span>{' '}
                template
              </div>

              <div style={{ display: 'flex', gap: 22, marginTop: 18 }}>
                <MiniStat label="Chapters" value={book.chapters} sub={`${done} ready`} />
                <MiniStat
                  label="Questions"
                  value={book.questions}
                  sub="2 variants each"
                />
                <MiniStat label="Figures" value={book.figures} sub="regenerated" />
                <MiniStat label="Pages" value={book.pages} sub="source PDF" />
              </div>
            </div>

            {/* Actions */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <button
                className="btn btn-primary"
                onClick={() => navigate(`/books/${book.id}/review`)}
              >
                <Icon name="eye" size={16} /> View extracted content
              </button>
              <div style={{ position: 'relative' }}>
                <button
                  className="btn btn-ghost"
                  onClick={() => setExportOpen((v) => !v)}
                >
                  <Icon name="download" size={16} /> Export{' '}
                  <Icon
                    name="chevron"
                    size={14}
                    style={{ transform: 'rotate(90deg)' }}
                  />
                </button>
                {exportOpen && (
                  <ExportMenu
                    onClose={() => setExportOpen(false)}
                    onExport={handleExport}
                  />
                )}
              </div>
              <button className="btn btn-ghost">
                <Icon name="regen" size={16} /> Regenerate all
              </button>
            </div>
          </div>

          {/* Smart progress */}
          {book.status === 'processing' && (
            <div
              style={{
                padding: '18px 26px 22px',
                background:
                  'linear-gradient(180deg, var(--surface-2), var(--surface))',
                borderTop: '1px solid var(--line)',
              }}
            >
              <SmartProgress progress={book.progress} stage={book.stage} />
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
            Chapters
          </h3>
          <div style={{ display: 'flex', gap: 8 }}>
            <span className="badge">{chapters.length} total</span>
            <span className="badge ok">{done} ready</span>
            {processing > 0 && (
              <span className="badge info">{processing} processing</span>
            )}
            {queued > 0 && <span className="badge idle">{queued} queued</span>}
          </div>
        </div>

        <div className="card" style={{ overflow: 'hidden' }}>
          {chapters.length === 0 && (
            <div style={{ padding: 28, color: 'var(--ink-500)' }}>
              No chapters available for this book yet.
            </div>
          )}
          {chapters.map((c, i) => (
            <ChapterRow
              key={c.id}
              chapter={c}
              isFirst={i === 0}
              onOpen={openChapter}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function ChapterRow({
  chapter: c,
  isFirst,
  onOpen,
}: {
  chapter: BackendChapter;
  isFirst: boolean;
  onOpen: (c: BackendChapter) => void;
}) {
  const [hover, setHover] = useState(false);
  const interactive = c.status === 'done';
  return (
    <div
      onClick={() => onOpen(c)}
      onMouseEnter={() => interactive && setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'grid',
        gridTemplateColumns: '40px 1fr 90px 90px 90px 140px 36px',
        alignItems: 'center',
        gap: 14,
        padding: '16px 22px',
        borderTop: isFirst ? 'none' : '1px solid var(--line-2)',
        cursor: interactive ? 'pointer' : 'default',
        opacity: c.status === 'queued' ? 0.65 : 1,
        background: hover ? 'var(--surface-2)' : 'transparent',
        transition: 'background 120ms',
      }}
    >
      <div
        className="mono"
        style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--ink-400)' }}
      >
        {String(c.n).padStart(2, '0')}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <div
          style={{
            width: 32,
            height: 32,
            borderRadius: 8,
            background:
              c.status === 'done'
                ? 'var(--indigo-50)'
                : c.status === 'processing'
                ? 'var(--info-bg)'
                : 'var(--bg-tint)',
            color:
              c.status === 'done'
                ? 'var(--indigo-700)'
                : c.status === 'processing'
                ? 'var(--info)'
                : 'var(--ink-400)',
            display: 'grid',
            placeItems: 'center',
          }}
        >
          <Icon name="book" size={16} />
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
            {c.title}
          </div>
          {c.status === 'processing' ? (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                marginTop: 4,
              }}
            >
              <div className="progress-rail" style={{ height: 4, width: 140 }}>
                <div className="progress-fill" style={{ width: `${c.progress ?? 0}%` }} />
              </div>
              <span
                className="mono"
                style={{ fontSize: 11, color: 'var(--info)', fontWeight: 600 }}
              >
                {c.progress ?? 0}%
              </span>
            </div>
          ) : (
            <div style={{ fontSize: 11.5, color: 'var(--ink-500)', marginTop: 2 }}>
              {c.sections} sections
            </div>
          )}
        </div>
      </div>
      <ColCount icon="layers" v={c.sections} />
      <ColCount icon="question" v={c.questions} />
      <ColCount icon="image" v={c.figures} />
      <div>
        <StatusBadge status={c.status} />
      </div>
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
