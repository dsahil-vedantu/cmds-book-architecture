// Re-opens the ExtractingPanel for an already-uploaded book so the
// user can re-visit the extraction progress + schema view from the
// Review page. The ExtractingPanel handles the case where extraction
// is already complete — it just shows all stages green with the
// "View extracted content" CTA.

import { useNavigate, useParams } from 'react-router-dom';

import { ExtractingPanel } from '../components/extraction/ExtractingPanel';
import { Icon } from '../components/Icon';
import { useBook } from '../api/books';

export default function BookExtractPage() {
  const { bookId } = useParams<{ bookId: string }>();
  const navigate = useNavigate();
  const bookState = useBook(bookId);

  if (!bookId) {
    return (
      <div className="content fade-up">
        <div className="content-narrow">
          <div className="card" style={{ padding: 28 }}>No book id in route.</div>
        </div>
      </div>
    );
  }

  const bookTitle =
    bookState.kind === 'ready' ? bookState.data.book.title : 'Loading…';

  return (
    <div className="content fade-up">
      <div className="content-narrow" style={{ maxWidth: 980 }}>
        <div className="page-header">
          <div>
            <h1 className="page-title">Extraction</h1>
            <div className="page-sub">
              Per-stage progress + schema viewer for this chapter.
            </div>
          </div>
          <button
            className="btn btn-ghost"
            onClick={() => navigate(`/books/${bookId}/review`)}
            title="Back to review"
          >
            <Icon name="arrow-l" size={16} /> Back to review
          </button>
        </div>

        <ExtractingPanel bookId={bookId} bookTitle={bookTitle} />
      </div>
    </div>
  );
}
