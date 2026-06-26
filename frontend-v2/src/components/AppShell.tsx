import { Outlet } from 'react-router-dom';

import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';
import { useBooks } from '../api/books';
import type { Book } from '../mocks/books';

export type ShellContext = {
  books: Book[];
  loading: boolean;
  error: string | null;
  refetch: () => void;
};

export default function AppShell() {
  const state = useBooks();
  const books = state.kind === 'ready' ? state.books : [];
  const loading = state.kind === 'loading';
  const error = state.kind === 'error' ? state.error : null;

  const ctx: ShellContext = { books, loading, error, refetch: state.refetch };

  return (
    <div className="app">
      <Sidebar books={books} />
      <main className="main">
        <TopBar books={books} />
        <Outlet context={ctx} />
      </main>
    </div>
  );
}
