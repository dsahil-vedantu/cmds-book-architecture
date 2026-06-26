import { useLocation, useNavigate } from 'react-router-dom';

import { Icon } from './Icon';
import type { Book } from '../mocks/books';
import { getChapter } from '../mocks/chapters';

type CrumbProps = { children: React.ReactNode; current?: boolean; onClick?: () => void };

function Crumb({ children, current, onClick }: CrumbProps) {
  return (
    <span className={`seg ${current ? 'current' : ''}`} onClick={onClick}>
      {children}
    </span>
  );
}

export function TopBar({ books }: { books: Book[] }) {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const parts = pathname.split('/').filter(Boolean);

  let crumbs: React.ReactNode = null;
  if (parts.length === 0 || parts[0] === 'library') {
    crumbs = <Crumb current>My Library</Crumb>;
  } else if (parts[0] === 'upload') {
    crumbs = (
      <>
        <Crumb onClick={() => navigate('/library')}>My Library</Crumb>
        <span className="sep">/</span>
        <Crumb current>New Book</Crumb>
      </>
    );
  } else if (parts[0] === 'folders') {
    crumbs = (
      <>
        <Crumb onClick={() => navigate('/library')}>My Library</Crumb>
        <span className="sep">/</span>
        <Crumb current>Folder</Crumb>
      </>
    );
  } else if (parts[0] === 'templates') {
    crumbs = <Crumb current>Templates</Crumb>;
  } else if (parts[0] === 'settings') {
    crumbs = <Crumb current>Settings</Crumb>;
  } else if (parts[0] === 'books') {
    const book = books.find((b) => b.id === parts[1]);
    if (!book) {
      crumbs = (
        <>
          <Crumb onClick={() => navigate('/library')}>My Library</Crumb>
          <span className="sep">/</span>
          <Crumb current>Unknown book</Crumb>
        </>
      );
    } else if (parts.length === 2) {
      crumbs = (
        <>
          <Crumb onClick={() => navigate('/library')}>My Library</Crumb>
          <span className="sep">/</span>
          <Crumb current>{book.title}</Crumb>
        </>
      );
    } else {
      // /books/:bookId/chapters/:chapterId
      const chapter = getChapter(book.id, parts[3]);
      const label = chapter
        ? `Ch ${chapter.n} · ${chapter.title}`
        : 'Chapter';
      crumbs = (
        <>
          <Crumb onClick={() => navigate('/library')}>My Library</Crumb>
          <span className="sep">/</span>
          <Crumb onClick={() => navigate(`/books/${book.id}`)}>{book.title}</Crumb>
          <span className="sep">/</span>
          <Crumb current>{label}</Crumb>
        </>
      );
    }
  }

  return (
    <header className="topbar">
      <div className="crumbs">{crumbs}</div>
      <div className="search">
        <span className="ic-search">
          <Icon name="search" size={15} />
        </span>
        <input placeholder="Search books, chapters, questions…" />
        <span
          style={{
            position: 'absolute',
            right: 8,
            top: '50%',
            transform: 'translateY(-50%)',
            display: 'flex',
            gap: 4,
          }}
        >
          <span className="kbd">⌘</span>
          <span className="kbd">K</span>
        </span>
      </div>
      <button className="top-icon-btn" title="What's new">
        <Icon name="sparkles" />
      </button>
      <button className="top-icon-btn" title="Notifications">
        <Icon name="bell" />
        <span className="dot" />
      </button>
      <button className="top-icon-btn" title="Help">
        <Icon name="help" />
      </button>
      <div style={{ width: 1, height: 24, background: 'var(--line)', margin: '0 4px' }} />
      <button className="btn btn-primary btn-sm" onClick={() => navigate('/upload')}>
        <Icon name="plus" size={16} /> New book
      </button>
    </header>
  );
}
