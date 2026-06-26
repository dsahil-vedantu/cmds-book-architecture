import { useState } from 'react';
import { useNavigate, useOutletContext, useParams } from 'react-router-dom';

import { CompareView } from '../components/CompareView';
import { FigureTile } from '../components/FigureTile';
import { Icon } from '../components/Icon';
import { QuestionCard } from '../components/QuestionCard';
import { SectionHeader } from '../components/SectionHeader';
import { useAuth } from '../auth/AuthProvider';
import type { Book } from '../mocks/books';
import { getChapter } from '../mocks/chapters';
import {
  ORIGINAL_THEORY,
  QUESTIONS_DEMO,
  REGEN_THEORY,
  type TheorySection,
} from '../mocks/chapterContent';

type ShellCtx = { books: Book[] };
type Tab = 'regen' | 'orig' | 'cmp';

export default function ChapterPage() {
  const { bookId, chapterId } = useParams();
  const navigate = useNavigate();
  const { books } = useOutletContext<ShellCtx>();
  const { user } = useAuth();

  const book = books.find((b) => b.id === bookId);
  const chapter = bookId && chapterId ? getChapter(bookId, chapterId) : undefined;

  const [tab, setTab] = useState<Tab>('regen');
  const [expanded, setExpanded] = useState<Record<string, boolean>>({
    s1: true,
    s2: false,
    s3: false,
  });

  if (!book || !chapter) {
    return (
      <div className="content fade-up">
        <div className="content-narrow">
          <div className="card" style={{ padding: 28 }}>
            <div style={{ color: 'var(--ink-700)' }}>
              Chapter not found.{' '}
              <button
                className="btn btn-soft btn-sm"
                onClick={() => navigate('/library')}
              >
                Back to library
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  const theory: TheorySection[] = tab === 'orig' ? ORIGINAL_THEORY : REGEN_THEORY;
  const reviewer = user?.name ?? 'You';

  return (
    <div className="content fade-up">
      <div className="content-narrow">
        {/* Chapter header */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: 18,
          }}
        >
          <div>
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => navigate(`/books/${book.id}`)}
              style={{ marginBottom: 10 }}
            >
              <Icon name="arrow-l" size={14} /> Back to {book.title}
            </button>
            <h1 className="page-title">
              <span
                style={{
                  color: 'var(--ink-400)',
                  fontFamily: 'var(--font-mono)',
                  fontWeight: 700,
                  marginRight: 12,
                }}
              >
                Ch {String(chapter.n).padStart(2, '0')}
              </span>
              {chapter.title}
            </h1>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 14,
                marginTop: 8,
                color: 'var(--ink-500)',
                fontSize: 13,
              }}
            >
              <span>
                <Icon name="layers" size={13} style={{ verticalAlign: -2 }} />{' '}
                {chapter.sections} sections
              </span>
              <span>
                <Icon name="question" size={13} style={{ verticalAlign: -2 }} />{' '}
                {chapter.questions} questions · 2 variants each
              </span>
              <span>
                <Icon name="image" size={13} style={{ verticalAlign: -2 }} />{' '}
                {chapter.figures} figures · {Math.max(0, chapter.figures - 3)} regenerated
              </span>
              <span className="badge ok">
                <span className="dot" />
                Ready for review
              </span>
            </div>
          </div>
          <div style={{ display: 'flex', gap: 10 }}>
            <button className="btn btn-ghost">
              <Icon name="regen" size={14} /> Regenerate
            </button>
            <button className="btn btn-primary">
              <Icon name="download" size={14} /> Export chapter
            </button>
          </div>
        </div>

        {/* Tab strip */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 16,
            marginBottom: 16,
          }}
        >
          <div className="tabs">
            <button
              className={`tab ${tab === 'regen' ? 'active' : ''}`}
              onClick={() => setTab('regen')}
            >
              <Icon name="sparkles" size={14} /> Regenerated
            </button>
            <button
              className={`tab ${tab === 'orig' ? 'active' : ''}`}
              onClick={() => setTab('orig')}
            >
              <Icon name="file" size={14} /> Original
            </button>
            <button
              className={`tab ${tab === 'cmp' ? 'active' : ''}`}
              onClick={() => setTab('cmp')}
            >
              <Icon name="split" size={14} /> Compare
            </button>
          </div>
          {tab === 'regen' && (
            <span className="badge regen">
              <Icon name="sparkles" size={11} /> 3 sections rewritten
            </span>
          )}
          {tab === 'orig' && (
            <span className="badge">
              <Icon name="file" size={11} /> Verbatim from source PDF
            </span>
          )}
          {tab === 'cmp' && (
            <span className="badge info">
              <Icon name="split" size={11} /> Synced scroll · diff highlighted
            </span>
          )}
          <div className="spacer" />
          <span style={{ fontSize: 12, color: 'var(--ink-500)' }}>
            Reviewing as <strong style={{ color: 'var(--ink-800)' }}>{reviewer}</strong>
          </span>
        </div>

        {tab === 'cmp' ? (
          <CompareView />
        ) : (
          <>
            {/* Theory */}
            <SectionHeader
              icon="layers"
              title="Theory"
              count={`${theory.length} sections`}
              badge={
                tab === 'regen' ? (
                  <span className="badge regen">
                    <Icon name="sparkles" size={11} /> Regenerated
                  </span>
                ) : undefined
              }
            />
            {theory.map((sec) => (
              <div className="acc" key={sec.id}>
                <div
                  className="acc-head"
                  onClick={() =>
                    setExpanded((e) => ({ ...e, [sec.id]: !e[sec.id] }))
                  }
                >
                  <div className={`chev ${expanded[sec.id] ? 'open' : ''}`}>
                    <Icon name="chevron" size={16} />
                  </div>
                  <div style={{ flex: 1 }}>
                    <div
                      style={{
                        fontSize: 15,
                        fontWeight: 700,
                        color: 'var(--ink-900)',
                        letterSpacing: '-0.01em',
                      }}
                    >
                      {sec.heading}
                    </div>
                    {!expanded[sec.id] && (
                      <div
                        style={{
                          fontSize: 13,
                          color: 'var(--ink-500)',
                          marginTop: 4,
                          maxWidth: 720,
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {sec.body}
                      </div>
                    )}
                  </div>
                  {sec.regen && (
                    <span className="badge regen" style={{ fontSize: 10 }}>
                      <Icon name="sparkles" size={10} />
                    </span>
                  )}
                  <button
                    className="top-icon-btn"
                    style={{ width: 30, height: 30 }}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <Icon name="regen" size={14} />
                  </button>
                </div>
                {expanded[sec.id] && <div className="acc-body">{sec.body}</div>}
              </div>
            ))}

            {/* Questions */}
            <div style={{ marginTop: 28 }} />
            <SectionHeader
              icon="question"
              title="Questions"
              count={`${QUESTIONS_DEMO.length} shown of ${chapter.questions}`}
            />
            {QUESTIONS_DEMO.map((q) => (
              <QuestionCard key={q.n} q={q} regen={tab === 'regen'} />
            ))}
            <button className="btn btn-ghost" style={{ marginTop: 10 }}>
              Show all {chapter.questions} questions
            </button>

            {/* Figures */}
            <div style={{ marginTop: 28 }} />
            <SectionHeader
              icon="image"
              title="Figures"
              count={`${chapter.figures} images`}
            />
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(4, 1fr)',
                gap: 14,
              }}
            >
              {Array.from({ length: chapter.figures })
                .slice(0, 8)
                .map((_, i) => (
                  <FigureTile key={i} idx={i} regen={tab === 'regen' && i < 3} />
                ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
