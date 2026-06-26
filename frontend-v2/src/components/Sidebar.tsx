import { useLocation, useNavigate } from 'react-router-dom';

import { Icon, type IconName } from './Icon';
import { Logo } from './Logo';
import { useAuth } from '../auth/AuthProvider';
import type { Book } from '../mocks/books';

type NavItemProps = {
  icon: IconName;
  label: string;
  to?: string;
  count?: number;
  active?: boolean;
  onClick?: () => void;
};

function NavRow({ icon, label, count, active, to, onClick }: NavItemProps) {
  const navigate = useNavigate();
  const handle = () => {
    if (onClick) return onClick();
    if (to) navigate(to);
  };
  return (
    <div className={`nav-item ${active ? 'active' : ''}`} onClick={handle}>
      <Icon name={icon} className="ic" />
      <span>{label}</span>
      {count != null && <span className="count">{count}</span>}
    </div>
  );
}

export function Sidebar({ books }: { books: Book[] }) {
  const { user, signOut } = useAuth();
  const { pathname } = useLocation();
  const navigate = useNavigate();

  const inBook = /^\/books\/[^/]+/.test(pathname);
  const bookId = inBook ? pathname.split('/')[2] : undefined;
  const currentBook = inBook ? books.find((b) => b.id === bookId) : undefined;

  const initials = user?.initials ?? 'V';
  const displayName = user?.name ?? 'Signed out';
  const displayEmail = user?.email ?? '—';

  return (
    <aside className="sidebar">
      <Logo />

      <div className="nav-section-label">Workspace</div>
      <NavRow icon="library"  label="My Library"  to="/library"   count={books.length} active={pathname === '/library' || pathname === '/'} />
      <NavRow icon="plus"     label="New Book"    to="/upload"    active={pathname === '/upload'} />
      <NavRow icon="palette"  label="Templates"   to="/templates" count={4} active={pathname === '/templates'} />
      <NavRow icon="settings" label="Settings"    to="/settings"  active={pathname === '/settings'} />

      {inBook && currentBook && (
        <>
          <div className="nav-section-label">Current book</div>
          <div
            style={{
              margin: '4px 4px 8px',
              padding: '10px 12px',
              borderRadius: 10,
              background: 'rgba(255,255,255,0.08)',
              border: '1px solid rgba(255,255,255,0.10)',
            }}
          >
            <div style={{ fontSize: 12.5, fontWeight: 700, lineHeight: 1.3 }}>{currentBook.title}</div>
            <div style={{ fontSize: 10.5, opacity: 0.65, marginTop: 4, letterSpacing: 0.02 }}>
              {currentBook.chapters} chapters · {currentBook.questions} questions
            </div>
          </div>
        </>
      )}

      <div className="nav-section-label" style={{ marginTop: 'auto' }}>Resources</div>
      <NavRow icon="help"     label="Docs & shortcuts" onClick={() => undefined} />
      <NavRow icon="sparkles" label="What's new"       onClick={() => undefined} />

      <div className="sidebar-footer">
        <div className="avatar">{initials}</div>
        <div className="who">
          <div className="nm">{displayName}</div>
          <div className="em">{displayEmail}</div>
        </div>
        <div
          className="top-icon-btn"
          style={{ color: 'rgba(255,255,255,0.7)', cursor: 'pointer' }}
          title="Sign out"
          onClick={() => {
            signOut();
            navigate('/login', { replace: true });
          }}
        >
          <Icon name="logout" size={16} />
        </div>
      </div>
    </aside>
  );
}

