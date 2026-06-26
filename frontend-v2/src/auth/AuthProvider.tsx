import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

export type AuthUser = {
  email: string;
  name: string;
  picture?: string;
  /** Initials shown in the sidebar avatar. */
  initials: string;
};

type AuthContextValue = {
  user: AuthUser | null;
  signIn: (user: AuthUser) => void;
  signOut: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);
const STORAGE_KEY = 'vstudio.auth.user';

function deriveInitials(name: string, email: string): string {
  const source = name.trim() || email.split('@')[0];
  const parts = source.split(/[\s.\-_]+/).filter(Boolean);
  if (parts.length === 0) return 'V';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? (JSON.parse(raw) as AuthUser) : null;
    } catch {
      return null;
    }
  });

  useEffect(() => {
    if (user) localStorage.setItem(STORAGE_KEY, JSON.stringify(user));
    else localStorage.removeItem(STORAGE_KEY);
  }, [user]);

  const signIn = useCallback((next: AuthUser) => {
    setUser({ ...next, initials: deriveInitials(next.name, next.email) });
  }, []);

  const signOut = useCallback(() => setUser(null), []);

  const value = useMemo<AuthContextValue>(
    () => ({ user, signIn, signOut }),
    [user, signIn, signOut]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}
