import { Icon } from './Icon';
import { StatusBadge } from '../lib/status';
import { COVER_GRADIENTS, type Book } from '../mocks/books';

export function BookCard({ book, onOpen }: { book: Book; onOpen: (id: string) => void }) {
  const grad = COVER_GRADIENTS[book.cover] ?? COVER_GRADIENTS.indigo;

  return (
    <div
      className="card card-hover"
      onClick={() => onOpen(book.id)}
      style={{ padding: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}
    >
      {/* Cover */}
      <div
        style={{
          position: 'relative',
          height: 168,
          background: grad,
          display: 'flex',
          alignItems: 'flex-end',
          padding: 18,
          color: '#fff',
        }}
      >
        <div
          style={{
            position: 'absolute',
            inset: 0,
            background:
              'radial-gradient(400px 220px at 20% 100%, rgba(255,255,255,0.20), transparent 70%)',
            pointerEvents: 'none',
          }}
        />
        <div
          style={{
            position: 'absolute',
            top: 14,
            left: 14,
            fontSize: 10.5,
            fontWeight: 700,
            letterSpacing: '0.14em',
            textTransform: 'uppercase',
            padding: '4px 8px',
            background: 'rgba(0,0,0,0.20)',
            borderRadius: 6,
            fontFamily: 'var(--font-mono)',
          }}
        >
          {book.subject}
        </div>
        <div style={{ position: 'absolute', top: 14, right: 14 }}>
          {book.status === 'processing' && (
            <div
              style={{
                background: 'rgba(0,0,0,0.30)',
                padding: '4px 10px',
                borderRadius: 999,
                fontSize: 11,
                fontWeight: 600,
                display: 'flex',
                alignItems: 'center',
                gap: 6,
              }}
            >
              <span className="spinner" style={{ width: 10, height: 10, borderWidth: 1.5 }} />
              {book.progress}%
            </div>
          )}
          {book.status === 'queued' && (
            <div
              style={{
                background: 'rgba(0,0,0,0.30)',
                padding: '4px 10px',
                borderRadius: 999,
                fontSize: 11,
                fontWeight: 600,
              }}
            >
              Queued
            </div>
          )}
        </div>
        <div style={{ position: 'relative' }}>
          <div style={{ fontSize: 11, opacity: 0.85, marginBottom: 4, fontWeight: 500 }}>
            {book.grade}
          </div>
          <div
            style={{
              fontSize: 19,
              fontWeight: 800,
              letterSpacing: '-0.02em',
              lineHeight: 1.15,
              maxWidth: '90%',
            }}
          >
            {book.title}
          </div>
        </div>
        {/* Decorative spine */}
        <div
          style={{
            position: 'absolute',
            right: 0,
            top: 0,
            bottom: 0,
            width: 4,
            background: 'rgba(255,255,255,0.16)',
          }}
        />
        <div
          style={{
            position: 'absolute',
            right: 4,
            top: 0,
            bottom: 0,
            width: 1,
            background: 'rgba(0,0,0,0.10)',
          }}
        />
      </div>

      {/* Footer */}
      <div
        style={{ padding: '14px 16px 16px', display: 'flex', flexDirection: 'column', gap: 10 }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <StatusBadge status={book.status} />
          <span style={{ fontSize: 11.5, color: 'var(--ink-500)', marginLeft: 'auto' }}>
            {book.updated}
          </span>
        </div>
        {book.status === 'processing' && (
          <>
            <div className="progress-rail" style={{ height: 5 }}>
              <div className="progress-fill" style={{ width: `${book.progress}%` }} />
            </div>
            {book.stage && (
              <div style={{ fontSize: 11.5, color: 'var(--ink-500)' }}>{book.stage}</div>
            )}
          </>
        )}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 14,
            color: 'var(--ink-500)',
            fontSize: 12,
            marginTop: 2,
          }}
        >
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
            <Icon name="layers" size={13} />
            <span className="mono">{book.chapters}</span> ch
          </span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
            <Icon name="question" size={13} />
            <span className="mono">{book.questions}</span> Q
          </span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
            <Icon name="image" size={13} />
            <span className="mono">{book.figures}</span> fig
          </span>
          <span
            style={{
              marginLeft: 'auto',
              display: 'inline-flex',
              alignItems: 'center',
              gap: 4,
              fontFamily: 'var(--font-mono)',
              fontSize: 10.5,
              background: 'var(--bg-tint)',
              padding: '2px 7px',
              borderRadius: 6,
              fontWeight: 600,
              color: 'var(--ink-700)',
            }}
          >
            {book.template}
          </span>
        </div>
      </div>
    </div>
  );
}
